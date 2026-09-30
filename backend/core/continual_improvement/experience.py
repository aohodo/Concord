"""Structured experience extraction, matching and prompt-safe rendering."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from core.case_orchestration import CaseRunSnapshot
from core.tools.sanitization import sanitize_value

from .models import (
    ExperienceAction,
    ExperienceApplicability,
    ExperienceKind,
    ExperienceMatch,
    OutcomeVerdict,
    OutcomeVerification,
    StructuredExperience,
)

_FAILURE_STATUSES = {"failed", "timed_out", "denied", "conflict"}


def applicability_from_context(context: dict[str, Any]) -> ExperienceApplicability:
    goal = dict(context.get("goal", {}))
    situation = dict(context.get("situation", {}))
    evidence = dict(context.get("evidence_handoff", {}))
    constraints = dict(context.get("action_constraints", {}))
    whitelist = dict(constraints.get("pre_resolution_whitelist", {}))
    conditional = dict(constraints.get("conditional_operation_modes", {}))
    evidence_keys = {
        str(item.get("key"))
        for bucket in ("resolved", "open", "deferred", "unavailable")
        for item in evidence.get(bucket, [])
        if isinstance(item, dict) and item.get("key")
    }
    situation_keys = {
        str(key)
        for key, value in situation.items()
        if value is not None
        and value != ""
        and value != "unknown"
        and value != []
        and value != {}
    }
    modes = {str(item) for item in whitelist.get("operation_modes", [])}
    modes.update(str(item) for item in conditional)
    return ExperienceApplicability(
        domain=str(context.get("domain_prior") or "other"),
        goal_kind=str(goal.get("goal_kind") or "unknown"),
        situation_keys=situation_keys,
        evidence_keys=evidence_keys,
        operation_modes=modes,
        required_capabilities={
            str(item) for item in context.get("available_tool_capabilities", [])
        },
        permission_requirements={
            str(item) for item in context.get("available_permissions", [])
        },
        tool_contract_versions={
            str(key): str(value)
            for key, value in context.get("tool_contract_versions", {}).items()
        },
        state_constraints=sanitize_value(
            context.get("experience_state_constraints", {})
        ),
        capabilities_known=bool(context.get("runtime_capabilities_known", False)),
        permissions_known=bool(context.get("runtime_permissions_known", False)),
        contract_versions_known=bool(
            context.get("runtime_contract_versions_known", False)
        ),
    )


def _action(item: dict[str, Any]) -> ExperienceAction:
    safe_evidence = [
        sanitize_value(
            {
                key: evidence.get(key)
                for key in ("key", "source", "source_type", "reliability")
                if key in evidence
            }
        )
        for evidence in item.get("evidence", [])
        if isinstance(evidence, dict)
    ]
    return ExperienceAction(
        tool_id=str(item.get("tool_id", "unknown")),
        category=str(item.get("category", "")),
        arguments=sanitize_value(
            dict(item.get("experience_safe_arguments", {})), max_chars=240
        ),
        expected_observation=str(
            sanitize_value(item.get("expected_observation", ""), max_chars=240)
        ),
        actual_observation=json.dumps(
            sanitize_value(item.get("experience_safe_data", {})),
            ensure_ascii=False,
            sort_keys=True,
        ),
        status=str(item.get("status", "unknown")),
        evidence=safe_evidence,
    )


class ExperienceExtractor:
    def extract(
        self,
        snapshot: CaseRunSnapshot,
        outcome: OutcomeVerification,
    ) -> StructuredExperience | None:
        if not outcome.eligible_for_experience:
            return None
        m2 = dict(snapshot.m2 or {})
        context = dict(snapshot.m1.get("resolution_context", {}))
        applicability = applicability_from_context(context)
        history = list(m2.get("tool_history", []))
        successful = [
            _action(item)
            for item in history
            if item.get("status") == "succeeded"
        ]
        failed = [_action(item) for item in history if item.get("status") in _FAILURE_STATUSES]
        successful_history = [
            item for item in history if item.get("status") == "succeeded"
        ]
        applicability.required_capabilities = {
            str(capability)
            for item in successful_history
            for capability in item.get("tool_capabilities", [])
        }
        applicability.permission_requirements = {
            str(permission)
            for item in successful_history
            for permission in item.get("required_permissions", [])
        }
        applicability.tool_contract_versions = {
            str(item.get("tool_id")): str(item.get("tool_contract_version", "1"))
            for item in successful_history
            if item.get("tool_id")
        }
        restricted = context.get("action_constraints", {}).get(
            "restricted_operation_modes", []
        )
        contraindications = [f"operation_mode:{item}" for item in restricted]
        contraindications.extend(
            "failed_call:"
            + json.dumps(
                {"tool_id": item.tool_id, "arguments": item.arguments},
                ensure_ascii=False,
                sort_keys=True,
            )
            for item in failed
        )
        kind = {
            OutcomeVerdict.VERIFIED_SUCCESS: ExperienceKind.SUCCESSFUL_PROCEDURE,
            OutcomeVerdict.VERIFIED_FAILURE: ExperienceKind.FAILURE_AVOIDANCE,
            OutcomeVerdict.VERIFIED_BOUNDARY: ExperienceKind.BOUNDARY_HANDOFF,
        }.get(outcome.verdict)
        if kind is None:
            return None
        return StructuredExperience(
            tenant_id=snapshot.tenant_id,
            source_case_id=snapshot.case_id,
            source_case_revision=snapshot.case_revision,
            source_trace_id=outcome.trace_id,
            kind=kind,
            applicability=applicability,
            strategy=successful,
            failed_actions=failed,
            outcome=outcome,
            provisional_explanations=sanitize_value(
                list(m2.get("provisional_explanations", [])),
                max_items=8,
                max_chars=240,
            ),
            contraindications=contraindications,
            valid_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
            sanitization={
                "policy": "tool_contract_allowlist",
                "pii_redaction": True,
                "bounded": True,
            },
        )


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class ExperienceMatcher:
    """Match structured Case state, never raw user wording."""

    def match(
        self,
        query: ExperienceApplicability,
        experience: StructuredExperience,
    ) -> ExperienceMatch:
        source = experience.applicability
        why: list[str] = []
        mismatches: list[str] = []
        if experience.valid_until:
            try:
                if datetime.fromisoformat(experience.valid_until) <= datetime.now(UTC):
                    return ExperienceMatch(
                        experience=experience,
                        score=0,
                        mismatches=["EXPERIENCE_EXPIRED"],
                    )
            except ValueError:
                return ExperienceMatch(
                    experience=experience,
                    score=0,
                    mismatches=["INVALID_FRESHNESS_METADATA"],
                )
        if (
            query.domain not in {"", "other"}
            and source.domain not in {"", "other"}
            and query.domain != source.domain
        ):
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["DOMAIN_MISMATCH"],
            )
        if query.situation_keys and source.situation_keys and not (
            query.situation_keys & source.situation_keys
        ):
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["STATE_SIGNATURE_DISJOINT"],
            )
        conflicting_state = {
            key
            for key in query.state_constraints.keys() & source.state_constraints.keys()
            if query.state_constraints[key] != source.state_constraints[key]
        }
        if conflicting_state:
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["STATE_VALUE_CONFLICT"],
            )
        if query.capabilities_known and not source.required_capabilities.issubset(
            query.required_capabilities
        ):
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["CAPABILITY_CONTRACT_MISMATCH"],
            )
        if query.permissions_known and not source.permission_requirements.issubset(
            query.permission_requirements
        ):
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["PERMISSION_CONTEXT_MISMATCH"],
            )
        version_conflicts = {
            tool_id
            for tool_id, source_version in source.tool_contract_versions.items()
            if tool_id not in query.tool_contract_versions
            or query.tool_contract_versions[tool_id] != source_version
        }
        if query.contract_versions_known and version_conflicts:
            return ExperienceMatch(
                experience=experience,
                score=0,
                mismatches=["TOOL_CONTRACT_VERSION_MISMATCH"],
            )

        score = 0.0
        if query.domain == source.domain and query.domain not in {"", "other"}:
            score += 0.30
            why.append("same structured domain")
        elif source.domain in {"", "other"}:
            score += 0.10
            why.append("domain-agnostic source")
        if query.goal_kind == source.goal_kind and query.goal_kind != "unknown":
            score += 0.15
            why.append("same goal kind")
        situation_score = _jaccard(query.situation_keys, source.situation_keys)
        evidence_score = _jaccard(query.evidence_keys, source.evidence_keys)
        mode_score = _jaccard(query.operation_modes, source.operation_modes)
        score += 0.20 * situation_score
        score += 0.15 * evidence_score
        score += 0.10 * mode_score
        if situation_score:
            why.append("overlapping situation structure")
        if evidence_score:
            why.append("overlapping evidence contract")
        if mode_score:
            why.append("compatible operation modes")
        if experience.outcome.eligible_for_experience:
            score += 0.10
            why.append("source outcome independently verified")
        if experience.reuse_count:
            contradiction_rate = (
                experience.contradicted_reuse_count / experience.reuse_count
            )
            score -= min(0.20, 0.20 * contradiction_rate)
            if contradiction_rate:
                mismatches.append("HISTORICAL_REUSE_CONTRADICTIONS")
        return ExperienceMatch(
            experience=experience,
            score=max(0.0, min(1.0, round(score, 4))),
            why_applicable=why,
            mismatches=mismatches,
        )

    def retrieve(
        self,
        query: ExperienceApplicability,
        experiences: list[StructuredExperience],
        *,
        limit: int = 3,
        minimum_score: float = 0.50,
    ) -> list[ExperienceMatch]:
        matches = [self.match(query, item) for item in experiences]
        matches = [item for item in matches if item.score >= minimum_score]
        return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]


def render_matches(matches: list[ExperienceMatch]) -> list[dict[str, Any]]:
    """Prompt-safe projection: past observations remain candidates, never current facts."""

    return [
        {
            "experience_id": item.experience.experience_id,
            "kind": item.experience.kind.value,
            "match_score": item.score,
            "why_applicable": item.why_applicable,
            "past_verified_actions": [
                action.model_dump(mode="json") for action in item.experience.strategy
            ],
            "past_failed_actions": [
                action.model_dump(mode="json") for action in item.experience.failed_actions
            ],
            "contraindications": item.experience.contraindications,
            "source_outcome": item.experience.outcome.verdict.value,
            "must_reverify_in_current_case": True,
        }
        for item in matches
    ]


__all__ = [
    "ExperienceExtractor",
    "ExperienceMatcher",
    "applicability_from_context",
    "render_matches",
]
