"""Failure mining and quarantined improvement proposal generation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, uuid5

from core.case_orchestration import CaseRunSnapshot

from .models import (
    FailureCategory,
    FailureSignal,
    ImprovementKind,
    ImprovementProposal,
)


def _reliability(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class FailureMiner:
    def mine(self, snapshot: CaseRunSnapshot) -> list[FailureSignal]:
        history = list((snapshot.m2 or {}).get("tool_history", []))
        signals: list[FailureSignal] = []
        failed_calls = [
            item
            for item in history
            if item.get("status") in {"failed", "timed_out", "denied", "conflict"}
        ]
        fingerprints = [
            json.dumps(
                {
                    "tool_id": item.get("tool_id"),
                    "arguments": item.get("arguments", {}),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for item in failed_calls
        ]
        for fingerprint, count in Counter(fingerprints).items():
            if count > 1:
                signals.append(
                    self._signal(
                        snapshot,
                        FailureCategory.REPEATED_FAILED_ACTION,
                        fingerprint,
                        [{"repeat_count": count, "call": json.loads(fingerprint)}],
                    )
                )

        for item in failed_calls:
            tool_id = str(item.get("tool_id", "unknown"))
            status = str(item.get("status", "unknown"))
            if status == "denied":
                signals.append(
                    self._signal(
                        snapshot,
                        FailureCategory.PERMISSION_BOUNDARY,
                        tool_id,
                        [item],
                    )
                )
            if item.get("error_code") in {"NO_CAPABLE_TOOL", "TOOL_NOT_FOUND"}:
                signals.append(
                    self._signal(
                        snapshot,
                        FailureCategory.TOOL_GAP,
                        str(item.get("error_code")),
                        [item],
                    )
                )

        for item in history:
            if item.get("tool_id") != "knowledge_search" or item.get("status") != "succeeded":
                continue
            evidence = list(item.get("evidence", []))
            if not evidence or all(_reliability(entry.get("reliability")) <= 0 for entry in evidence):
                signals.append(
                    self._signal(
                        snapshot,
                        FailureCategory.KNOWLEDGE_GAP,
                        str(item.get("metadata", {}).get("query", "unknown-query")),
                        [{"tool_id": "knowledge_search", "evidence_count": len(evidence)}],
                    )
                )

        guardrails = list((snapshot.m2 or {}).get("guardrail_events", []))
        if any(item.get("code") == "UNVERIFIED_RESOLUTION_REJECTED" for item in guardrails):
            signals.append(
                self._signal(
                    snapshot,
                    FailureCategory.VERIFICATION_GAP,
                    "unverified_resolution_attempt",
                    guardrails,
                )
            )
        if snapshot.status in {"exhausted", "paused"} or any(
            item.get("event") == "no_progress"
            for item in (snapshot.m2 or {}).get("decision_events", [])
        ):
            signals.append(
                self._signal(
                    snapshot,
                    FailureCategory.NO_PROGRESS,
                    str(snapshot.context_summary.goal.get("goal_kind", "unknown")),
                    [{"phase": snapshot.phase.value, "status": snapshot.status}],
                )
            )
        metrics = dict((snapshot.m3 or {}).get("metrics", {}))
        if metrics and float(metrics.get("net_gain", 0)) < 0:
            signals.append(
                self._signal(
                    snapshot,
                    FailureCategory.COLLABORATION_NEGATIVE_GAIN,
                    str((snapshot.m3 or {}).get("mode", "unknown")),
                    [metrics],
                )
            )
        return signals

    @staticmethod
    def _signal(
        snapshot: CaseRunSnapshot,
        category: FailureCategory,
        signature: str,
        evidence: list[dict[str, Any]],
    ) -> FailureSignal:
        return FailureSignal(
            tenant_id=snapshot.tenant_id,
            source_case_id=snapshot.case_id,
            category=category,
            signature=signature,
            evidence=evidence,
        )


class ImprovementProposer:
    """Turn repeated signals into reviewable work, never production mutations."""

    _KIND: ClassVar[dict[FailureCategory, ImprovementKind]] = {
        FailureCategory.KNOWLEDGE_GAP: ImprovementKind.KNOWLEDGE,
        FailureCategory.TOOL_GAP: ImprovementKind.TOOL,
        FailureCategory.PERMISSION_BOUNDARY: ImprovementKind.POLICY,
        FailureCategory.VERIFICATION_GAP: ImprovementKind.EVALUATION,
        FailureCategory.REPEATED_FAILED_ACTION: ImprovementKind.POLICY,
        FailureCategory.NO_PROGRESS: ImprovementKind.SKILL,
        FailureCategory.COLLABORATION_NEGATIVE_GAIN: ImprovementKind.POLICY,
    }
    _ARTIFACT: ClassVar[dict[FailureCategory, dict[str, str]]] = {
        FailureCategory.KNOWLEDGE_GAP: {
            "artifact_type": "knowledge_acquisition_task",
            "target": "curated internal or public evidence with provenance",
        },
        FailureCategory.TOOL_GAP: {
            "artifact_type": "tool_adapter_task",
            "target": "read-only or simulated adapter plus contract tests",
        },
        FailureCategory.PERMISSION_BOUNDARY: {
            "artifact_type": "permission_policy_task",
            "target": "capability, approval and handoff policy",
        },
        FailureCategory.VERIFICATION_GAP: {
            "artifact_type": "evaluation_scenario_task",
            "target": "independent success oracle and negative control",
        },
        FailureCategory.REPEATED_FAILED_ACTION: {
            "artifact_type": "control_policy_task",
            "target": "failure-sensitive action ranking and search switch",
        },
        FailureCategory.NO_PROGRESS: {
            "artifact_type": "skill_task",
            "target": "diagnostic procedure with stop and escalation conditions",
        },
        FailureCategory.COLLABORATION_NEGATIVE_GAIN: {
            "artifact_type": "collaboration_policy_task",
            "target": "marginal-gain threshold and collaborator selection",
        },
    }

    def propose(self, signals: list[FailureSignal]) -> list[ImprovementProposal]:
        grouped: dict[tuple[FailureCategory, str], list[FailureSignal]] = defaultdict(list)
        for signal in signals:
            grouped[(signal.category, signal.signature)].append(signal)
        proposals: list[ImprovementProposal] = []
        for (category, signature), items in grouped.items():
            distinct_cases = {item.source_case_id for item in items}
            if len(distinct_cases) < 2:
                continue
            stable_id = str(
                uuid5(NAMESPACE_URL, f"concord:m7:{items[0].tenant_id}:{category}:{signature}")
            )
            proposals.append(
                ImprovementProposal(
                    proposal_id=stable_id,
                    tenant_id=items[0].tenant_id,
                    kind=self._KIND[category],
                    title=f"Address recurring {category.value}",
                    problem_statement=(
                        f"The same structural signal occurred in {len(distinct_cases)} "
                        f"independent Cases: {signature}"
                    ),
                    evidence_signal_ids=[item.signal_id for item in items],
                    suggested_artifact={
                        **self._ARTIFACT[category],
                        "category": category.value,
                        "signature": signature,
                        "release_policy": "isolated_evaluation_then_human_decision",
                    },
                    acceptance_criteria=[
                        "target failure decreases on held-out scenarios",
                        "verified success does not regress",
                        "user effort and runtime cost do not materially increase",
                    ],
                    evaluation_scenarios=[
                        "original failure replay",
                        "same domain with changed state",
                        "different domain negative control",
                        "missing permission boundary",
                    ],
                )
            )
        return proposals


__all__ = ["FailureMiner", "ImprovementProposer"]
