"""Deterministic M4 summaries; raw JSONL remains the authoritative artifact."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .runner import M4EpisodeRun


def _interaction_metrics(run: M4EpisodeRun) -> dict[str, float | int | None]:
    user_chars = sum(len(item.raw_user_input) for item in run.turns)
    response_chars = sum(
        len(str(item.response.get("response", ""))) for item in run.turns
    )
    first_action_ms: float | None = None
    events = run.case_snapshot.get("events", [])
    tool_events = run.tool_trace.get("trace", {}).get("events", [])
    if events and tool_events:
        try:
            formulation_event = next(
                item
                for item in events
                if item.get("event_type") == "m1_turn_completed"
            )
            formulated_at = datetime.fromisoformat(str(formulation_event["created_at"]))
            acted_at = datetime.fromisoformat(str(tool_events[0]["timestamp"]))
            first_action_ms = max(
                0.0,
                round((acted_at - formulated_at).total_seconds() * 1000, 3),
            )
        except (KeyError, TypeError, ValueError):
            first_action_ms = None
    return {
        "turns": len(run.turns),
        "user_chars": user_chars,
        "response_chars": response_chars,
        "post_formulation_first_tool_ms": first_action_ms,
    }


def write_jsonl(path: Path, runs: list[M4EpisodeRun]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(run.model_dump_json() + "\n")


def write_matrix(path: Path, runs: list[M4EpisodeRun]) -> None:
    conditions = sorted({item.user_condition for item in runs})
    domains = sorted({item.domain for item in runs})
    grouped: dict[tuple[str, str], list[M4EpisodeRun]] = defaultdict(list)
    for run in runs:
        grouped[(run.domain, run.user_condition)].append(run)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["domain", *conditions])
        for domain in domains:
            row = [domain]
            for condition in conditions:
                items = grouped.get((domain, condition), [])
                row.append(
                    "NOT_RUN"
                    if not items
                    else "PASS"
                    if all(item.passed for item in items)
                    else "FAIL"
                )
            writer.writerow(row)


def build_report(runs: list[M4EpisodeRun]) -> str:
    total = len(runs)
    passed = sum(item.passed for item in runs)
    failure_counts = Counter(failure for item in runs for failure in item.failures)
    coverage_counts = Counter(note for item in runs for note in item.coverage_notes)
    status_counts = Counter(item.final_status for item in runs)
    avg_latency = sum(item.latency_ms for item in runs) / total if total else 0.0
    total_calls = sum(
        item.m1_model_calls + item.m2_model_calls + item.m3_model_calls for item in runs
    )
    by_condition: dict[str, list[M4EpisodeRun]] = defaultdict(list)
    by_domain: dict[str, list[M4EpisodeRun]] = defaultdict(list)
    for item in runs:
        by_condition[item.user_condition].append(item)
        by_domain[item.domain].append(item)
    reached_m2 = sum(
        any(
            event.get("event_type")
            in {"m2_run_completed", "m2_resumed_after_collaboration"}
            for event in item.case_snapshot.get("events", [])
        )
        for item in runs
    )
    reached_m3 = sum(bool(item.case_snapshot.get("m3")) for item in runs)
    first_turn_ready = sum(
        bool(item.turns and item.turns[0].response.get("case_ready")) for item in runs
    )
    interaction = [_interaction_metrics(item) for item in runs]
    measured_first_actions = [
        float(item["post_formulation_first_tool_ms"])
        for item in interaction
        if item["post_formulation_first_tool_ms"] is not None
    ]
    average_turns = sum(int(item["turns"]) for item in interaction) / total if total else 0
    average_user_chars = (
        sum(int(item["user_chars"]) for item in interaction) / total if total else 0
    )
    average_response_chars = (
        sum(int(item["response_chars"]) for item in interaction) / total if total else 0
    )
    lines = [
        "# Concord M4 End-to-End Evaluation Report",
        "",
        "## Scope",
        "",
        (
            "Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. "
            "The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff."
        ),
        "",
        "## Summary",
        "",
        f"- Episodes: {total}",
        f"- Passed: {passed}",
        f"- Failed: {total - passed}",
        f"- Pass rate: {(passed / total * 100 if total else 0):.1f}%",
        f"- Average wall time: {avg_latency / 1000:.2f}s",
        f"- Recorded model decisions: {total_calls}",
        f"- Reached M2: {reached_m2}/{total}",
        f"- Reached M3: {reached_m3}/{total}",
        f"- M1 entered M2 on the first turn: {first_turn_ready}/{total}",
        f"- Average user turns: {average_turns:.2f}",
        f"- Average user input characters: {average_user_chars:.1f}",
        f"- Average assistant response characters: {average_response_chars:.1f}",
        (
            "- Mean post-formulation time to first tool: "
            + (
                f"{sum(measured_first_actions) / len(measured_first_actions) / 1000:.2f}s "
                f"({len(measured_first_actions)}/{total} measured)"
                if measured_first_actions
                else "unavailable"
            )
        ),
        "",
        "## User-condition results",
        "",
        "| condition | pass | average latency s | model decisions |",
        "|---|---:|---:|---:|",
    ]
    for condition, items in sorted(by_condition.items()):
        condition_calls = sum(
            item.m1_model_calls + item.m2_model_calls + item.m3_model_calls
            for item in items
        )
        lines.append(
            f"| {condition} | {sum(item.passed for item in items)}/{len(items)} | "
            f"{sum(item.latency_ms for item in items) / len(items) / 1000:.2f} | "
            f"{condition_calls} |"
        )
    lines.extend(
        [
            "",
            "## Domain results",
            "",
            "| domain | pass | terminal statuses |",
            "|---|---:|---|",
        ]
    )
    for domain, items in sorted(by_domain.items()):
        domain_statuses = Counter(item.final_status for item in items)
        lines.append(
            f"| {domain} | {sum(item.passed for item in items)}/{len(items)} | "
            f"{dict(domain_statuses)} |"
        )
    lines.extend(
        [
            "",
            "## Terminal status",
            "",
        ]
    )
    lines.extend(f"- `{key}`: {value}" for key, value in sorted(status_counts.items()))
    lines.extend(["", "## Failure labels", ""])
    if failure_counts:
        lines.extend(f"- `{key}`: {value}" for key, value in failure_counts.most_common())
    else:
        lines.append("- None")
    lines.extend(["", "## Non-failing coverage notes", ""])
    if coverage_counts:
        lines.extend(f"- `{key}`: {value}" for key, value in coverage_counts.most_common())
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "A PASS proves the declared simulated outcome and structural chain checks for "
                "that Episode. It does not prove universal domain competence. LLM-as-Judge "
                "results, when added, must remain separate from environment-grounded success."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def save_report_bundle(output_dir: Path, runs: list[M4EpisodeRun]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "raw_runs.jsonl", runs)
    write_matrix(output_dir / "result_matrix.csv", runs)
    (output_dir / "M4_END_TO_END_REPORT.md").write_text(
        build_report(runs),
        encoding="utf-8",
    )
    summary = {
        "episodes": len(runs),
        "passed": sum(item.passed for item in runs),
        "failed": sum(not item.passed for item in runs),
        "failure_labels": dict(
            Counter(failure for item in runs for failure in item.failures)
        ),
        "coverage_notes": dict(
            Counter(note for item in runs for note in item.coverage_notes)
        ),
        "interaction": {
            "average_turns": (
                sum(len(item.turns) for item in runs) / len(runs) if runs else 0.0
            ),
            "average_user_input_characters": (
                sum(
                    int(_interaction_metrics(item)["user_chars"]) for item in runs
                )
                / len(runs)
                if runs
                else 0.0
            ),
            "average_assistant_response_characters": (
                sum(
                    int(_interaction_metrics(item)["response_chars"]) for item in runs
                )
                / len(runs)
                if runs
                else 0.0
            ),
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = ["build_report", "save_report_bundle", "write_jsonl", "write_matrix"]
