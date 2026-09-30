"""Evidence-first outcome verification for M7 experience retention."""

from __future__ import annotations

from typing import Any

from core.adaptive_resolution.verification import success_criteria
from core.case_orchestration import CasePhase, CaseRunSnapshot
from core.tools.sanitization import sanitize_value

from .models import OutcomeAuthority, OutcomeVerdict, OutcomeVerification

_FAILURE_STATUSES = {"failed", "timed_out", "denied", "conflict"}


def _durable_tool_record(item: dict[str, Any]) -> dict[str, Any]:
    return sanitize_value(
        {
            "tool_id": item.get("tool_id"),
            "invocation_id": item.get("invocation_id"),
            "category": item.get("category"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
            "verification_target": item.get("verification_target"),
            "expectation_matched": item.get("expectation_matched"),
            "tool_contract_version": item.get("tool_contract_version"),
            "arguments": item.get("experience_safe_arguments", {}),
            "data": item.get("experience_safe_data", {}),
        }
    )


def _independent_observations(tool_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _durable_tool_record(item)
        for item in tool_history
        if item.get("category") in {"observe", "verify"}
        and item.get("status") == "succeeded"
        and item.get("data") is not None
    ]


class OutcomeVerifier:
    """Separate environment truth from plausible text and user satisfaction."""

    def verify(self, snapshot: CaseRunSnapshot) -> OutcomeVerification:
        m2 = dict(snapshot.m2 or {})
        tool_history = list(m2.get("tool_history", []))
        observations = _independent_observations(tool_history)
        failures = [
            _durable_tool_record(item)
            for item in tool_history
            if item.get("status") in _FAILURE_STATUSES
        ]
        trace_id = str(m2.get("trace_id", ""))
        verification_complete = bool(m2.get("verification_complete"))
        resolution_evidence = [
            str(sanitize_value(str(item), max_chars=240))
            for item in m2.get("resolution_evidence", [])
        ]
        required_criteria = success_criteria(
            dict(snapshot.m1.get("resolution_context", {}))
        )
        verified_criteria = {
            str(item.get("criterion"))
            for item in m2.get("criterion_verifications", [])
            if isinstance(item, dict) and item.get("criterion")
        }
        criteria_complete = not required_criteria or set(required_criteria).issubset(
            verified_criteria
        )

        if (
            snapshot.phase is CasePhase.RESOLVED
            and m2.get("status") == "resolved"
            and verification_complete
            and resolution_evidence
            and observations
            and criteria_complete
        ):
            return OutcomeVerification(
                case_id=snapshot.case_id,
                tenant_id=snapshot.tenant_id,
                case_revision=snapshot.case_revision,
                trace_id=trace_id,
                verdict=OutcomeVerdict.VERIFIED_SUCCESS,
                authority=OutcomeAuthority.INDEPENDENT_TOOL,
                eligible_for_experience=True,
                verification_evidence=[
                    {"resolution_evidence": resolution_evidence},
                    *observations,
                ],
                failure_evidence=failures,
            )

        if snapshot.phase is CasePhase.HUMAN_REQUIRED and any(
            item.get("status") == "denied" for item in failures
        ):
            return OutcomeVerification(
                case_id=snapshot.case_id,
                tenant_id=snapshot.tenant_id,
                case_revision=snapshot.case_revision,
                trace_id=trace_id,
                verdict=OutcomeVerdict.VERIFIED_BOUNDARY,
                authority=OutcomeAuthority.RUNTIME_BOUNDARY,
                eligible_for_experience=True,
                failure_evidence=failures,
            )

        if snapshot.phase in {CasePhase.ERROR, CasePhase.TIMED_OUT} and failures:
            return OutcomeVerification(
                case_id=snapshot.case_id,
                tenant_id=snapshot.tenant_id,
                case_revision=snapshot.case_revision,
                trace_id=trace_id,
                verdict=OutcomeVerdict.VERIFIED_FAILURE,
                authority=OutcomeAuthority.ENVIRONMENT,
                eligible_for_experience=True,
                failure_evidence=failures,
            )

        reasons: list[str] = []
        if snapshot.phase is not CasePhase.RESOLVED:
            reasons.append("CASE_NOT_RESOLVED")
        if not verification_complete:
            reasons.append("VERIFICATION_NOT_COMPLETE")
        if not resolution_evidence:
            reasons.append("NO_EXPLICIT_RESOLUTION_EVIDENCE")
        if not observations:
            reasons.append("NO_INDEPENDENT_TOOL_OBSERVATION")
        if not criteria_complete:
            reasons.append("SUCCESS_CRITERIA_NOT_COVERED")
        return OutcomeVerification(
            case_id=snapshot.case_id,
            tenant_id=snapshot.tenant_id,
            case_revision=snapshot.case_revision,
            trace_id=trace_id,
            verdict=OutcomeVerdict.UNVERIFIED,
            authority=(
                OutcomeAuthority.USER_REPORT
                if snapshot.response and snapshot.phase is CasePhase.WAITING_FOR_USER
                else OutcomeAuthority.MODEL_SELF_ASSESSMENT
                if m2
                else OutcomeAuthority.NONE
            ),
            eligible_for_experience=False,
            verification_evidence=observations,
            failure_evidence=failures,
            rejected_reasons=reasons,
        )


__all__ = ["OutcomeVerifier"]
