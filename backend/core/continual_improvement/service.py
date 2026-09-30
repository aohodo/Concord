"""M7 application service: verify, retain, retrieve, mine and propose."""

from __future__ import annotations

from typing import Any

from core.case_orchestration import CaseRunSnapshot
from core.tools import RuntimeToolProfile

from .experience import (
    ExperienceExtractor,
    ExperienceMatcher,
    applicability_from_context,
    render_matches,
)
from .mining import FailureMiner, ImprovementProposer
from .models import (
    ExperienceFeedback,
    ExperienceMatch,
    FailureSignal,
    ImprovementProposal,
    OutcomeVerdict,
    OutcomeVerification,
    ProposalStatus,
    utc_now,
)
from .repository import ContinualImprovementRepository
from .verification import OutcomeVerifier


class ContinualImprovementService:
    def __init__(self, repository: ContinualImprovementRepository) -> None:
        self.repository = repository
        self.verifier = OutcomeVerifier()
        self.extractor = ExperienceExtractor()
        self.matcher = ExperienceMatcher()
        self.failure_miner = FailureMiner()
        self.proposer = ImprovementProposer()

    async def observe_case(
        self,
        snapshot: CaseRunSnapshot,
    ) -> tuple[OutcomeVerification, list[FailureSignal], list[ImprovementProposal]]:
        outcome = await self.repository.save_outcome(self.verifier.verify(snapshot))
        references = list((snapshot.m2 or {}).get("adopted_experience_ids", []))
        if outcome.verdict in {
            OutcomeVerdict.VERIFIED_SUCCESS,
            OutcomeVerdict.VERIFIED_FAILURE,
        }:
            reuse_outcome = (
                "supported"
                if outcome.verdict is OutcomeVerdict.VERIFIED_SUCCESS
                else "contradicted"
            )
            for experience_id in references:
                await self.repository.record_feedback(
                    ExperienceFeedback(
                        experience_id=str(experience_id),
                        target_case_id=snapshot.case_id,
                        outcome=reuse_outcome,
                        verification_evidence=outcome.verification_evidence,
                    )
                )
        experience = self.extractor.extract(snapshot, outcome)
        if experience is not None:
            await self.repository.save_experience(experience)
        stored_signals = [
            await self.repository.save_failure_signal(item)
            for item in self.failure_miner.mine(snapshot)
        ]
        all_signals = await self.repository.list_failure_signals(
            tenant_id=snapshot.tenant_id
        )
        proposals = self.proposer.propose(all_signals)
        for proposal in proposals:
            await self.repository.save_proposal(proposal)
        return outcome, stored_signals, proposals

    async def retrieve(
        self,
        resolution_context: dict[str, Any],
        *,
        tenant_id: str,
        limit: int = 3,
    ) -> list[ExperienceMatch]:
        experiences = await self.repository.list_experiences(tenant_id=tenant_id)
        current_case_id = str(resolution_context.get("case_id", ""))
        if current_case_id:
            experiences = [
                item for item in experiences if item.source_case_id != current_case_id
            ]
        return self.matcher.retrieve(
            applicability_from_context(resolution_context),
            experiences,
            limit=limit,
        )

    async def enrich_resolution_context(
        self,
        resolution_context: dict[str, Any],
        *,
        tenant_id: str,
        limit: int = 3,
        runtime_tool_profile: RuntimeToolProfile | None = None,
    ) -> tuple[dict[str, Any], list[ExperienceMatch]]:
        retrieval_context = dict(resolution_context)
        if runtime_tool_profile is not None:
            retrieval_context.update(
                {
                    "available_tool_capabilities": sorted(
                        runtime_tool_profile.available_capabilities
                    ),
                    "available_permissions": sorted(
                        runtime_tool_profile.granted_permissions
                    ),
                    "tool_contract_versions": dict(
                        runtime_tool_profile.tool_contract_versions
                    ),
                    "runtime_capabilities_known": (
                        runtime_tool_profile.capabilities_known
                    ),
                    "runtime_permissions_known": runtime_tool_profile.permissions_known,
                    "runtime_contract_versions_known": (
                        runtime_tool_profile.contract_versions_known
                    ),
                }
            )
        matches = await self.retrieve(
            retrieval_context,
            tenant_id=tenant_id,
            limit=limit,
        )
        enriched = retrieval_context
        enriched["candidate_experiences"] = render_matches(matches)
        enriched["experience_policy"] = {
            "role": "candidate_strategy_not_current_fact",
            "must_reverify": True,
            "production_self_modification": False,
        }
        return enriched, matches

    async def list_proposals(
        self,
        *,
        tenant_id: str,
        status: ProposalStatus | None = None,
    ) -> list[ImprovementProposal]:
        return await self.repository.list_proposals(tenant_id=tenant_id, status=status)

    async def record_isolated_evaluation(
        self,
        *,
        tenant_id: str,
        proposal_id: str,
        evaluator: str,
        result: dict[str, Any],
        expected_version: int,
    ) -> ImprovementProposal:
        proposal = await self.repository.get_proposal(
            tenant_id=tenant_id, proposal_id=proposal_id
        )
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.version != expected_version:
            raise ValueError("proposal version changed; reload before recording evaluation")
        if proposal.status in {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.ROLLED_BACK,
        }:
            raise ValueError("decided proposal cannot accept new evaluation results")
        evaluation = dict(result)
        evaluation["evaluator"] = evaluator
        evaluation["recorded_at"] = utc_now()
        updated = proposal.model_copy(deep=True)
        updated.evaluation_results.append(evaluation)
        updated.status = ProposalStatus.EVALUATING
        updated.version += 1
        updated.updated_at = utc_now()
        return await self.repository.save_proposal(updated)

    async def decide_proposal(
        self,
        *,
        tenant_id: str,
        proposal_id: str,
        actor_id: str,
        decision: str,
        reason: str,
        expected_version: int,
    ) -> ImprovementProposal:
        proposal = await self.repository.get_proposal(
            tenant_id=tenant_id, proposal_id=proposal_id
        )
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.version != expected_version:
            raise ValueError("proposal version changed; reload before deciding")
        normalized = decision.strip().lower()
        if normalized == "approve":
            if not proposal.evaluation_results:
                raise ValueError("approval requires at least one isolated evaluation")
            status = ProposalStatus.APPROVED
        elif normalized == "reject":
            status = ProposalStatus.REJECTED
        elif normalized == "rollback" and proposal.status is ProposalStatus.APPROVED:
            status = ProposalStatus.ROLLED_BACK
        else:
            raise ValueError("decision must be approve, reject, or rollback an approval")
        updated = proposal.model_copy(deep=True)
        updated.status = status
        updated.decision_by = actor_id
        updated.decision_reason = reason
        updated.version += 1
        updated.updated_at = utc_now()
        return await self.repository.save_proposal(updated)


__all__ = ["ContinualImprovementService"]
