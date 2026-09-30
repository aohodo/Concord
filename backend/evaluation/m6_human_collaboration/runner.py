"""Deterministic M6 policy evaluation with human-effort measurements."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from core.human_collaboration import (
    InteractionPreferences,
    derive_human_collaboration_state,
    layer_response,
)

from .catalog import load_cases


def evaluate_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in load_cases():
        preferences = InteractionPreferences.model_validate(case.overrides)
        state = derive_human_collaboration_state(
            {"user_state": case.user_state}, overrides=preferences
        )
        presentation = layer_response(case.response, state)
        reconstructed = presentation.primary_message + presentation.details
        failures: list[str] = []
        if state.expression_depth.value != case.expected_depth:
            failures.append("EXPRESSION_DEPTH_MISMATCH")
        if state.budget.max_user_actions_per_turn != case.expected_max_actions:
            failures.append("ACTION_BUDGET_MISMATCH")
        if state.progress_cadence.value != case.expected_progress:
            failures.append("PROGRESS_CADENCE_MISMATCH")
        if reconstructed != case.response:
            failures.append("DETAIL_LOSS")
        if len(presentation.primary_message) > state.budget.max_primary_response_chars:
            failures.append("READING_BUDGET_EXCEEDED")
        results.append(
            {
                "case_id": case.case_id,
                "domain": case.domain,
                "user_condition": case.user_condition,
                "expression_depth": state.expression_depth.value,
                "progress_cadence": state.progress_cadence.value,
                "primary_chars": len(presentation.primary_message),
                "deferred_chars": len(presentation.details),
                "max_user_actions": state.budget.max_user_actions_per_turn,
                "detail_preserved": reconstructed == case.response,
                "passed": not failures,
                "failures": failures,
            }
        )
    return results


def save_report(output_dir: str | Path, results: list[dict[str, Any]]) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "raw_policy_runs.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in results) + "\n",
        encoding="utf-8",
    )
    with (output / "interaction_matrix.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "domain",
                "user_condition",
                "expression_depth",
                "progress_cadence",
                "primary_chars",
                "deferred_chars",
                "max_user_actions",
                "detail_preserved",
                "passed",
            ],
        )
        writer.writeheader()
        writer.writerows(
            {key: value for key, value in item.items() if key != "failures"}
            for item in results
        )
    passed = sum(item["passed"] for item in results)
    average_primary = sum(item["primary_chars"] for item in results) / max(
        1, len(results)
    )
    report = [
        "# M6 Human Collaboration Policy Report",
        "",
        f"- Cases: {len(results)}",
        f"- Contract pass: {passed}/{len(results)}",
        f"- Average primary response characters: {average_primary:.1f}",
        "- Detail preservation: "
        + f"{sum(item['detail_preserved'] for item in results)}/{len(results)}",
        "",
        (
            "This deterministic layer verifies that grounded current-turn state changes "
            "interaction cost and presentation without relying on raw keyword matching. "
            "It is a contract evaluation, not evidence of user satisfaction or model quality."
        ),
    ]
    (output / "M6_POLICY_REPORT.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )


__all__ = ["evaluate_cases", "save_report"]
