"""Runtime-owned success-criterion verification for M2 resolution decisions."""

from __future__ import annotations

from typing import Any

from .models import CriterionVerification


def success_criteria(context: dict[str, Any]) -> list[str]:
    """Return M1 criteria without inventing missing acceptance conditions."""

    output: list[str] = []
    for item in context.get("goal", {}).get("success_criteria", []):
        value = item.get("value") if isinstance(item, dict) else item
        normalized = str(value or "").strip()
        if normalized and normalized not in output:
            output.append(normalized)
    return output


def verified_evidence_refs(tool_history: list[dict[str, Any]]) -> set[str]:
    """Collect only references backed by a successful independent observation."""

    refs: set[str] = set()
    for item in tool_history:
        if item.get("category") not in {"observe", "verify"}:
            continue
        if item.get("status") != "succeeded" or item.get("data") is None:
            continue
        if item.get("expectation_matched") is False:
            continue
        invocation_id = str(item.get("invocation_id") or "").strip()
        target = str(item.get("verification_target") or "").strip()
        if invocation_id:
            refs.add(f"invocation:{invocation_id}")
        if target:
            refs.add(f"target:{target}")
            data = item.get("data")
            if isinstance(data, dict) and "value" in data:
                refs.add(f"fact:{target}={data['value']}")
        for evidence in item.get("evidence", []):
            if not isinstance(evidence, dict) or not evidence.get("key"):
                continue
            refs.add(f"fact:{evidence['key']}={evidence.get('value')}")
    return refs


def validate_criterion_verifications(
    *,
    context: dict[str, Any],
    tool_history: list[dict[str, Any]],
    declared: list[CriterionVerification],
) -> tuple[list[CriterionVerification], list[str]]:
    """Validate model bindings and derive only exact structured fact matches.

    Natural-language semantic equivalence remains a model responsibility, but the
    referenced evidence itself must exist in the runtime's independent tool history.
    Exact ``key=value`` criteria can be bound without another model decision.
    """

    required = success_criteria(context)
    if not required:
        return [], []
    refs = verified_evidence_refs(tool_history)
    accepted: dict[str, CriterionVerification] = {}
    for binding in declared:
        if binding.criterion not in required:
            continue
        if binding.evidence_refs and all(ref in refs for ref in binding.evidence_refs):
            accepted[binding.criterion] = binding
    for criterion in required:
        exact_ref = f"fact:{criterion}"
        if criterion not in accepted and exact_ref in refs:
            accepted[criterion] = CriterionVerification(
                criterion=criterion,
                evidence_refs=[exact_ref],
            )
    missing = [item for item in required if item not in accepted]
    return [accepted[item] for item in required if item in accepted], missing


__all__ = [
    "success_criteria",
    "validate_criterion_verifications",
    "verified_evidence_refs",
]
