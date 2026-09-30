"""Deterministic enforcement for user-facing interaction decisions.

The language model proposes a behavior.  This module makes the claims that are
cheap to verify (priority coverage, question budget, repetition and length)
program properties instead of prompt aspirations.
"""

from __future__ import annotations

import re

from .models import (
    CoarseLevel,
    ContractCompliance,
    EvidenceNeedStatus,
    InteractionAction,
    InteractionNeed,
    InteractionNeedType,
    PolicyDecision,
    SharedProblemState,
)

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_LEVEL_RANK = {
    CoarseLevel.HIGH: 0,
    CoarseLevel.MEDIUM: 1,
    CoarseLevel.LOW: 2,
    CoarseLevel.UNKNOWN: 1,
}
_BURDEN_RANK = {
    CoarseLevel.LOW: 0,
    CoarseLevel.MEDIUM: 1,
    CoarseLevel.UNKNOWN: 1,
    CoarseLevel.HIGH: 2,
}

_FALLBACK_QUESTIONS = {
    InteractionNeedType.GOAL_ALIGNMENT: "为了不擅自扩大范围，你现在最希望先得到什么结果？",
    InteractionNeedType.SITUATION_RECONSTRUCTION: "请只描述你现在实际看到的现象。",
    InteractionNeedType.EPISTEMIC_CALIBRATION: "先不判断原因，哪条现象能直接支持这个判断？",
    InteractionNeedType.CONTRADICTION_REPAIR: "这两种说法中，哪一个符合你现在看到的情况？",
    InteractionNeedType.EVIDENCE_ACQUISITION: "请补充当前最关键的一条直接证据。",
    InteractionNeedType.USER_SUPPORT: "我们先做一个你愿意且方便完成的小步骤，可以吗？",
    InteractionNeedType.URGENCY_PRIORITIZATION: "时间有限时，你希望先恢复哪项最关键的使用？",
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _question_count(value: str) -> int:
    return value.count("?") + value.count("？")


def _response_limit(response_length: str) -> int:
    return {"minimal": 140, "short": 240, "medium": 500, "long": 900}.get(response_length, 500)


class PolicyHarness:
    """Validate and, when necessary, replace unsafe policy prose."""

    def __init__(self, behavior_capabilities: dict):
        self._capabilities = behavior_capabilities

    def enforce(
        self,
        state: SharedProblemState,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        if decision.action in {
            InteractionAction.EMIT_CASE_READY,
            InteractionAction.META_RESPONSE,
        }:
            return decision

        priority_need = self._priority_need(state.interaction_needs)
        addressed = self._addressed_need(state.interaction_needs, decision.action)
        violations: list[str] = []
        if priority_need is not None:
            priority_actions = set(self._capabilities.get(priority_need.type, ()))
            if decision.action not in priority_actions:
                violations.append("highest_priority_need_not_addressed")

        response = decision.response_text or decision.question or ""
        if _question_count(response) > decision.contract.question_budget:
            violations.append("question_budget_exceeded")
        if len(response) > _response_limit(decision.contract.response_length):
            violations.append("response_length_exceeded")
        if any(
            _normalized(proposed) == _normalized(action)
            for proposed in decision.proposed_actions
            for action in decision.contract.forbidden_actions
            if _normalized(proposed) and _normalized(action)
        ):
            violations.append("forbidden_action_repeated")
        if not response.strip():
            violations.append("empty_interaction_response")

        open_evidence = [
            item
            for item in state.missing_evidence
            if item.status is EvidenceNeedStatus.OPEN
            and (decision.contract.allow_nonblocking_questions or item.blocking)
        ]
        target = next(
            (
                item
                for item in open_evidence
                if _normalized(item.key) == _normalized(decision.target_evidence or "")
            ),
            None,
        )
        if decision.target_evidence and open_evidence and target is None:
            violations.append("invalid_target_evidence")
        evidence_seeking_needs = {
            InteractionNeedType.SITUATION_RECONSTRUCTION,
            InteractionNeedType.EPISTEMIC_CALIBRATION,
            InteractionNeedType.EVIDENCE_ACQUISITION,
        }
        if (
            open_evidence
            and priority_need is not None
            and priority_need.type in evidence_seeking_needs
            and target is None
            and "invalid_target_evidence" not in violations
        ):
            violations.append("missing_target_evidence")
        if (
            decision.action in {InteractionAction.VERIFY, InteractionAction.SCAFFOLD}
            and open_evidence
            and target is None
            and "invalid_target_evidence" not in violations
        ):
            violations.append("invalid_target_evidence")
        if target is not None and target.attempt_count > 0:
            violations.append("repeated_no_progress_target")

        if not violations:
            decision.addressed_need = addressed.type if addressed else None
            decision.compliance = ContractCompliance(compliant=True)
            return decision

        repaired = self._repair(state, decision, priority_need)
        repaired.compliance = ContractCompliance(
            compliant=True,
            violations=violations,
            repaired=True,
        )
        return repaired

    def _repair(
        self,
        state: SharedProblemState,
        decision: PolicyDecision,
        priority_need: InteractionNeed | None,
    ) -> PolicyDecision:
        need_type = (
            priority_need.type
            if priority_need is not None
            else InteractionNeedType.EVIDENCE_ACQUISITION
        )
        allowed = list(self._capabilities.get(need_type, (InteractionAction.VERIFY,)))
        action = allowed[0]
        evidence_need_types = {
            InteractionNeedType.SITUATION_RECONSTRUCTION,
            InteractionNeedType.EPISTEMIC_CALIBRATION,
            InteractionNeedType.EVIDENCE_ACQUISITION,
        }
        open_evidence = self.select_evidence(state) if need_type in evidence_need_types else None
        question = (
            open_evidence.question
            if open_evidence is not None
            else _FALLBACK_QUESTIONS.get(need_type, "请补充当前最关键的一条直接证据。")
        )
        if decision.contract.question_budget == 0:
            question = "我会先按当前已经确认的信息继续处理。"
        response = question[: _response_limit(decision.contract.response_length)]
        return PolicyDecision(
            action=action,
            supporting_actions=[],
            reason="policy harness repaired a contract or priority violation",
            target_evidence=open_evidence.key if open_evidence else None,
            question=question if decision.contract.question_budget else None,
            response_text=response,
            candidate_actions=decision.candidate_actions,
            contract=decision.contract,
            addressed_need=need_type,
        )

    @staticmethod
    def select_evidence(state: SharedProblemState):
        eligible = [
            item
            for item in state.missing_evidence
            if item.status is EvidenceNeedStatus.OPEN
            and (state.user_state.deadline is None or item.blocking)
            and (item.issue_id is None or item.issue_id == state.active_issue_id)
        ]
        if not eligible:
            return None
        untried = [item for item in eligible if item.attempt_count == 0]
        if untried:
            eligible = untried
        return min(
            eligible,
            key=lambda item: (
                0 if item.blocking else 1,
                _PRIORITY_RANK[item.priority.value],
                _BURDEN_RANK[item.user_burden],
                _LEVEL_RANK[item.decision_impact],
                item.attempt_count,
            ),
        )

    def _addressed_need(
        self,
        needs: list[InteractionNeed],
        action: InteractionAction,
    ) -> InteractionNeed | None:
        return next(
            (need for need in needs if action in self._capabilities.get(need.type, ())),
            None,
        )

    @staticmethod
    def _priority_need(needs: list[InteractionNeed]) -> InteractionNeed | None:
        return needs[0] if needs else None
