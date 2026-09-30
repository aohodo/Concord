"""Three-way M7 evaluation: none, coarse reflection, verified structured memory."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from core.continual_improvement import (
    ExperienceApplicability,
    ExperienceMatcher,
    OutcomeAuthority,
    OutcomeVerdict,
    OutcomeVerification,
    StructuredExperience,
)

from .catalog import M7Scenario

VARIANTS = ("no_memory", "coarse_structured", "structured_verified")


class M7Run(BaseModel):
    scenario_id: str
    domain: str
    scenario_type: str
    variant: str
    should_retrieve: bool
    retrieved: bool
    safe: bool
    correct: bool
    match_score: float = 0.0
    explanation: list[str]


def _experience(scenario: M7Scenario) -> StructuredExperience:
    verdict = {
        "successful_procedure": OutcomeVerdict.VERIFIED_SUCCESS,
        "failure_avoidance": OutcomeVerdict.VERIFIED_FAILURE,
        "boundary_handoff": OutcomeVerdict.VERIFIED_BOUNDARY,
    }[scenario.source_kind.value]
    if not scenario.source_verified:
        verdict = OutcomeVerdict.UNVERIFIED
    return StructuredExperience(
        tenant_id="m7-evaluation",
        source_case_id=f"source:{scenario.scenario_id}",
        source_trace_id=f"trace:{scenario.scenario_id}",
        kind=scenario.source_kind,
        status=scenario.source_status,
        applicability=ExperienceApplicability(
            domain=scenario.source_domain,
            goal_kind="restore_service",
            situation_keys=scenario.source_state_keys,
            evidence_keys={"current_state", "target_state"},
            operation_modes={"read_only"},
            required_capabilities=scenario.source_required_capabilities,
            permission_requirements=scenario.source_required_permissions,
            tool_contract_versions=scenario.source_tool_versions,
            state_constraints=scenario.source_state_constraints,
        ),
        valid_until=scenario.source_valid_until,
        outcome=OutcomeVerification(
            case_id=f"source:{scenario.scenario_id}",
            tenant_id="m7-evaluation",
            trace_id=f"trace:{scenario.scenario_id}",
            verdict=verdict,
            authority=(
                OutcomeAuthority.INDEPENDENT_TOOL
                if scenario.source_verified
                else OutcomeAuthority.MODEL_SELF_ASSESSMENT
            ),
            eligible_for_experience=scenario.source_verified,
        ),
    )


def _query(scenario: M7Scenario) -> ExperienceApplicability:
    return ExperienceApplicability(
        domain=scenario.domain,
        goal_kind="restore_service",
        situation_keys=scenario.query_state_keys,
        evidence_keys={"current_state", "target_state"},
        operation_modes={"read_only"},
        required_capabilities=scenario.query_available_capabilities,
        permission_requirements=scenario.query_available_permissions,
        tool_contract_versions=scenario.query_tool_versions,
        state_constraints=scenario.query_state_constraints,
        capabilities_known=True,
        permissions_known=True,
        contract_versions_known=True,
    )


def evaluate_scenarios(scenarios: list[M7Scenario]) -> list[M7Run]:
    matcher = ExperienceMatcher()
    runs: list[M7Run] = []
    for scenario in scenarios:
        source = _experience(scenario)
        query = _query(scenario)
        for variant in VARIANTS:
            score = 0.0
            explanation: list[str] = []
            if variant == "no_memory":
                retrieved = False
                explanation = ["memory disabled"]
            elif variant == "coarse_structured":
                eligible = (
                    source.status.value == "active"
                    and source.outcome.eligible_for_experience
                )
                retrieved = (
                    eligible
                    and source.applicability.domain == query.domain
                    and bool(
                        source.applicability.situation_keys
                        & query.situation_keys
                    )
                )
                score = 0.75 if retrieved else 0.0
                explanation = (
                    ["eligible source with matching domain and state keys"]
                    if retrieved
                    else []
                )
            else:
                eligible = (
                    source.status.value == "active"
                    and source.outcome.eligible_for_experience
                )
                match = matcher.match(query, source)
                retrieved = eligible and match.score >= 0.5
                score = match.score if eligible else 0.0
                explanation = (
                    match.why_applicable
                    if eligible
                    else ["source rejected before retrieval"]
                )
            correct = retrieved == scenario.should_retrieve
            runs.append(
                M7Run(
                    scenario_id=scenario.scenario_id,
                    domain=scenario.domain,
                    scenario_type=scenario.scenario_type,
                    variant=variant,
                    should_retrieve=scenario.should_retrieve,
                    retrieved=retrieved,
                    safe=not (retrieved and not scenario.should_retrieve),
                    correct=correct,
                    match_score=round(score, 4),
                    explanation=explanation,
                )
            )
    return runs


def write_results(output_dir: Path, runs: list[M7Run]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "raw_runs.jsonl").open("w", encoding="utf-8") as handle:
        for item in runs:
            handle.write(item.model_dump_json() + "\n")
    with (output_dir / "result_matrix.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "scenario_id",
                "domain",
                "scenario_type",
                "variant",
                "should_retrieve",
                "retrieved",
                "safe",
                "correct",
                "match_score",
            ]
        )
        for item in runs:
            writer.writerow(
                [
                    item.scenario_id,
                    item.domain,
                    item.scenario_type,
                    item.variant,
                    item.should_retrieve,
                    item.retrieved,
                    item.safe,
                    item.correct,
                    item.match_score,
                ]
            )
    (output_dir / "M7_EVALUATION_REPORT.md").write_text(
        build_report(runs), encoding="utf-8"
    )


def build_report(runs: list[M7Run]) -> str:
    lines = [
        "# M7 Verified Experience Evaluation",
        "",
        (
            "This control-plane evaluation uses injected verified, unverified, deprecated, "
            "cross-domain and state-mismatched experiences. It measures retrieval safety, not "
            "real-world task-resolution uplift."
        ),
        "",
        "| variant | correct | useful hits | unsafe retrievals | safe rejection rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        items = [item for item in runs if item.variant == variant]
        positives = [item for item in items if item.should_retrieve]
        negatives = [item for item in items if not item.should_retrieve]
        correct = sum(item.correct for item in items)
        useful = sum(item.retrieved for item in positives)
        unsafe = sum(not item.safe for item in negatives)
        safe_rejection = sum(not item.retrieved for item in negatives)
        lines.append(
            f"| {variant} | {correct}/{len(items)} | {useful}/{len(positives)} | "
            f"{unsafe}/{len(negatives)} | {safe_rejection}/{len(negatives)} |"
        )
    failures = Counter(
        (item.variant, item.scenario_type) for item in runs if not item.correct
    )
    lines.extend(["", "## Failure distribution", ""])
    if not failures:
        lines.append("No contract failures.")
    else:
        for (variant, scenario_type), count in sorted(failures.items()):
            lines.append(f"- `{variant}` / `{scenario_type}`: {count}")
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "A structured-memory pass proves that the retention and retrieval controls "
                "reject the injected hazards. It does not prove that experience improves "
                "end-to-end resolution success; that claim requires real longitudinal Episodes "
                "with independent outcome verification."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def dump_catalog(path: Path, scenarios: list[M7Scenario]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in scenarios],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


__all__ = [
    "VARIANTS",
    "M7Run",
    "build_report",
    "dump_catalog",
    "evaluate_scenarios",
    "write_results",
]
