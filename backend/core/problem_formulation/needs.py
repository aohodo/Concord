"""Declarative interaction-need detection and contract construction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .models import (
    ActionOutcome,
    ActionRelevance,
    CaseStatus,
    ClaimType,
    CoarseLevel,
    CollaborationPreference,
    DeliveryPreference,
    EvidenceNeedStatus,
    EvidencePriority,
    ExplanationPreference,
    InteractionAction,
    InteractionContract,
    InteractionNeed,
    InteractionNeedType,
    ProgressPreference,
    ResolutionPolicy,
    SharedProblemState,
    VerificationStatus,
)

Predicate = Callable[[SharedProblemState], bool]


def _goal_requires_alignment(state: SharedProblemState) -> bool:
    if state.goal.explicit_goal:
        return False
    # A source-grounded problem report can support a provisional outcome. M1
    # should not force the user to approve internal paperwork before M2 starts.
    return not bool(state.goal.inferred_goal)


def _direct_observation_missing(state: SharedProblemState) -> bool:
    def applies(issue_id: str | None) -> bool:
        return issue_id is None or issue_id == state.active_issue_id

    claim_observation = any(
        claim.type is ClaimType.OBSERVATION
        and claim.verification_status is not VerificationStatus.CONTESTED
        and applies(claim.issue_id)
        for claim in state.claims
    )
    contract_observation = any(
        item.status is EvidenceNeedStatus.RESOLVED and bool(item.resolution_quote)
        and applies(item.issue_id)
        for item in state.missing_evidence
    )
    return not (claim_observation or contract_observation)


@dataclass(frozen=True)
class NeedRule:
    type: InteractionNeedType
    reason: str
    priority: EvidencePriority
    predicate: Predicate


NEED_RULES = (
    NeedRule(
        InteractionNeedType.GOAL_ALIGNMENT,
        "the desired outcome is unknown or changes the downstream plan",
        EvidencePriority.CRITICAL,
        _goal_requires_alignment,
    ),
    NeedRule(
        InteractionNeedType.SITUATION_RECONSTRUCTION,
        "the directly observed situation is not yet grounded",
        EvidencePriority.HIGH,
        _direct_observation_missing,
    ),
    NeedRule(
        InteractionNeedType.EPISTEMIC_CALIBRATION,
        "a user hypothesis must remain separate from observed evidence",
        EvidencePriority.HIGH,
        lambda state: (
            any(claim.type is ClaimType.HYPOTHESIS for claim in state.claims)
            and not any(
                item.action in {InteractionAction.CALIBRATE, InteractionAction.CHALLENGE}
                for item in state.formulation_history
            )
        ),
    ),
    NeedRule(
        InteractionNeedType.CONTRADICTION_REPAIR,
        "the shared case contains unresolved conflicting reports",
        EvidencePriority.CRITICAL,
        lambda state: bool(state.contradictions),
    ),
    NeedRule(
        InteractionNeedType.EVIDENCE_ACQUISITION,
        "resolution-critical evidence is still missing",
        EvidencePriority.HIGH,
        lambda state: any(
            item.status is EvidenceNeedStatus.OPEN
            and (item.issue_id is None or item.issue_id == state.active_issue_id)
            for item in state.missing_evidence
        ),
    ),
    NeedRule(
        InteractionNeedType.URGENCY_PRIORITIZATION,
        "time or patience constraints require a shorter interaction path",
        EvidencePriority.MEDIUM,
        lambda state: bool(state.user_state.deadline)
        or state.user_state.patience is CoarseLevel.LOW,
    ),
    NeedRule(
        InteractionNeedType.USER_SUPPORT,
        "current frustration, control, or effort constraints require scaffolding",
        EvidencePriority.MEDIUM,
        lambda state: any(
            (
                state.user_state.frustration is CoarseLevel.HIGH,
                state.user_state.perceived_controllability is CoarseLevel.LOW,
                state.user_state.effort_willingness is CoarseLevel.LOW,
            )
        ),
    ),
)


class InteractionNeedDetector:
    """Evaluate independent rules rather than a user-type decision tree."""

    def detect(self, state: SharedProblemState) -> list[InteractionNeed]:
        terminal = {
            CaseStatus.FORMULATION_ERROR: [],
            CaseStatus.META_HANDLED: [],
            CaseStatus.CASE_READY: [
                InteractionNeed(
                    type=InteractionNeedType.READY_FOR_RESOLUTION,
                    reason="minimum shared problem formulation is ready",
                    priority=EvidencePriority.HIGH,
                )
            ],
        }
        if state.status in terminal:
            return terminal[state.status]

        matches = [
            InteractionNeed(type=rule.type, reason=rule.reason, priority=rule.priority)
            for rule in NEED_RULES
            if rule.predicate(state)
        ]
        order = {
            EvidencePriority.CRITICAL: 0,
            EvidencePriority.HIGH: 1,
            EvidencePriority.MEDIUM: 2,
            EvidencePriority.LOW: 3,
        }
        return sorted(matches, key=lambda need: order[need.priority])


@dataclass(frozen=True)
class ContractRule:
    predicate: Predicate
    patch: dict


CONTRACT_RULES = (
    ContractRule(
        lambda state: state.user_state.domain_knowledge is CoarseLevel.HIGH,
        {"terminology": "technical"},
    ),
    ContractRule(
        lambda state: state.user_state.domain_knowledge is CoarseLevel.LOW,
        {"terminology": "plain", "detail_level": "guided"},
    ),
    ContractRule(
        lambda state: state.user_state.clarity is CoarseLevel.LOW,
        {"detail_level": "structured"},
    ),
    ContractRule(
        lambda state: state.user_state.delivery_preference
        is DeliveryPreference.PROCESS_VISIBLE,
        {"result_first": False, "detail_level": "structured"},
    ),
    ContractRule(
        lambda state: state.user_state.explanation_preference
        is ExplanationPreference.MINIMAL,
        {"response_length": "minimal", "detail_level": "concise"},
    ),
    ContractRule(
        lambda state: state.user_state.explanation_preference
        is ExplanationPreference.DETAILED,
        {"response_length": "long", "detail_level": "detailed"},
    ),
    ContractRule(
        lambda state: state.user_state.collaboration_preference
        is CollaborationPreference.STEP_BY_STEP,
        {"max_steps_requested": 1},
    ),
    ContractRule(
        lambda state: state.user_state.progress_preference
        in {ProgressPreference.MILESTONES, ProgressPreference.EVERY_STEP},
        {"progress_disclosure": True},
    ),
    ContractRule(
        lambda state: any(
            (
                state.user_state.frustration is CoarseLevel.HIGH,
                state.user_state.patience is CoarseLevel.LOW,
                state.user_state.effort_willingness is CoarseLevel.LOW,
                bool(state.user_state.deadline),
            )
        ),
        {
            "max_steps_requested": 1,
            "detail_level": "concise",
            "response_length": "minimal",
            "action_burden": CoarseLevel.LOW,
            "allow_nonblocking_questions": False,
            "max_clarification_turns": 1,
            "progress_disclosure": True,
        },
    ),
)


class InteractionContractBuilder:
    """Fold applicable constraints into one auditable response contract."""

    def build(self, state: SharedProblemState) -> InteractionContract:
        values = InteractionContract().model_dump()
        for rule in CONTRACT_RULES:
            if rule.predicate(state):
                values.update(rule.patch)
        values["forbidden_actions"] = [
            item.action
            for item in state.action_result_ledger
            if item.relevance is ActionRelevance.ACTIVE
            and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
            and not item.retry_condition
        ]
        return InteractionContract.model_validate(values)


class ResolutionPolicyBuilder:
    """Derive a provisional M2 policy without turning it into a user label."""

    @staticmethod
    def build(
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> ResolutionPolicy:
        user = state.user_state
        delivery = (
            user.delivery_preference
            if user.delivery_preference is not DeliveryPreference.UNKNOWN
            else DeliveryPreference.RESULT_FIRST
        )
        explanation = (
            user.explanation_preference
            if user.explanation_preference is not ExplanationPreference.UNKNOWN
            else ExplanationPreference.ON_DEMAND
        )
        collaboration = (
            user.collaboration_preference
            if user.collaboration_preference is not CollaborationPreference.UNKNOWN
            else CollaborationPreference.AGENT_LED
        )
        progress = (
            user.progress_preference
            if user.progress_preference is not ProgressPreference.UNKNOWN
            else ProgressPreference.MILESTONES
        )
        return ResolutionPolicy(
            delivery_mode=delivery,
            explanation_mode=explanation,
            collaboration_mode=collaboration,
            progress_mode=progress,
            max_questions_per_turn=contract.question_budget,
            max_user_actions_per_turn=contract.max_steps_requested,
            allow_nonblocking_questions=contract.allow_nonblocking_questions,
            forbidden_retries=list(contract.forbidden_actions),
            deadline=user.deadline,
            evidence_turn_ids=sorted(
                {
                    item.turn_id
                    for item in state.user_state_evidence
                    if item.is_current
                }
            ),
        )
