"""Summarize M6 signals from real-model M4-compatible longitudinal runs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _metrics(run: dict[str, Any]) -> dict[str, Any]:
    turns = run.get("turns", [])
    snapshot = run.get("case_snapshot", {})
    collaboration = snapshot.get("human_collaboration", {})
    usage = collaboration.get("usage", {})
    events = snapshot.get("events", [])
    event_types = [str(item.get("event_type", "")) for item in events]
    responses = [item.get("response", {}) for item in turns]
    tool_events = run.get("tool_trace", {}).get("trace", {}).get("events", [])
    effective_tool = next(
        (
            item
            for item in tool_events
            if item.get("state_before") != item.get("state_after")
        ),
        tool_events[0] if tool_events else None,
    )
    first_event = events[0] if events else None
    first_effective_action_ms: float | None = None
    if first_event and effective_tool:
        try:
            first_effective_action_ms = max(
                0.0,
                (
                    datetime.fromisoformat(str(effective_tool["timestamp"]))
                    - datetime.fromisoformat(str(first_event["created_at"]))
                ).total_seconds()
                * 1000,
            )
        except (KeyError, TypeError, ValueError):
            first_effective_action_ms = None
    action_fingerprints = [
        (
            str(item.get("tool_id", "")),
            json.dumps(item.get("arguments", {}), ensure_ascii=False, sort_keys=True),
        )
        for item in tool_events
        if item.get("tool_id") == "simulation_execute_action"
    ]
    goal_revisions = snapshot.get("m1", {}).get("problem_state", {}).get("goal", {}).get(
        "revision_history", []
    )
    return {
        "episode_id": run.get("episode_id", ""),
        "domain": run.get("domain", ""),
        "user_condition": run.get("user_condition", ""),
        "passed": bool(run.get("passed")),
        "final_status": run.get("final_status", ""),
        "turns": len(turns),
        "user_chars": sum(len(str(item.get("raw_user_input", ""))) for item in turns),
        "latency_ms": run.get("latency_ms", 0),
        "mean_turn_latency_ms": (
            sum(float(item.get("latency_ms", 0)) for item in turns) / len(turns)
            if turns
            else 0.0
        ),
        "first_effective_action_ms": first_effective_action_ms,
        "model_calls": sum(
            int(run.get(key, 0))
            for key in ("m1_model_calls", "m2_model_calls", "m3_model_calls")
        ),
        "tool_calls": int(run.get("tool_calls", 0)),
        "expression_depth": collaboration.get("expression_depth", "unavailable"),
        "primary_chars": sum(len(str(item.get("response", ""))) for item in responses),
        "deferred_chars": sum(len(str(item.get("response_details", ""))) for item in responses),
        "mean_primary_chars_per_turn": (
            sum(len(str(item.get("response", ""))) for item in responses) / len(responses)
            if responses
            else 0.0
        ),
        "repeated_execute_actions": len(action_fingerprints) - len(set(action_fingerprints)),
        "progress_events": len(events),
        "turn_received_visible": "user_turn_received" in event_types,
        "effort_accounted": "human_effort_accounted" in event_types,
        "goal_revisions": len(goal_revisions),
        "goal_correction_recovered": bool(goal_revisions and run.get("passed")),
        "recorded_primary_chars": int(usage.get("primary_response_chars", 0)),
        "failures": run.get("failures", []),
    }


def write_real_report(raw_path: Path, output_dir: Path) -> None:
    rows = [_metrics(item) for item in _load(raw_path)]
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = [key for key in rows[0] if key != "failures"] if rows else []
    with (output_dir / "m6_real_metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if key in fields} for row in rows)

    statuses = Counter(str(row["final_status"]) for row in rows)
    depths = Counter(str(row["expression_depth"]) for row in rows)
    total = len(rows)
    first_actions = [
        float(row["first_effective_action_ms"])
        for row in rows
        if row["first_effective_action_ms"] is not None
    ]
    lines = [
        "# M6 Real-model Human Collaboration Report",
        "",
        "## Scope",
        "",
        (
            "These longitudinal Episodes entered through the public HTTP boundary and used "
            "the configured real model. Simulated environment outcomes remain the success "
            "authority; interaction metrics do not substitute for task completion."
        ),
        "",
        "## Results",
        "",
        f"- Episodes: {total}",
        f"- Outcome + chain pass: {sum(bool(row['passed']) for row in rows)}/{total}",
        f"- Terminal statuses: {dict(statuses)}",
        f"- Expression depths: {dict(depths)}",
        f"- Visible turn acknowledgement: {sum(bool(row['turn_received_visible']) for row in rows)}/{total}",
        f"- Human-effort accounting present: {sum(bool(row['effort_accounted']) for row in rows)}/{total}",
        f"- Mean wall time: {(sum(float(row['latency_ms']) for row in rows) / total / 1000 if total else 0):.2f}s",
        f"- Mean turn wait: {(sum(float(row['mean_turn_latency_ms']) for row in rows) / total / 1000 if total else 0):.2f}s",
        (
            "- Mean time to first effective environment action: "
            + (
                f"{sum(first_actions) / len(first_actions) / 1000:.2f}s "
                f"({len(first_actions)}/{total} measured)"
                if first_actions
                else "unavailable"
            )
        ),
        f"- Mean user turns: {(sum(int(row['turns']) for row in rows) / total if total else 0):.2f}",
        f"- Mean user input characters: {(sum(int(row['user_chars']) for row in rows) / total if total else 0):.1f}",
        f"- Mean primary reading burden per turn: {(sum(float(row['mean_primary_chars_per_turn']) for row in rows) / total if total else 0):.1f} characters",
        f"- Repeated executable actions: {sum(int(row['repeated_execute_actions']) for row in rows)}",
        f"- Goal-correction recovery: {sum(bool(row['goal_correction_recovered']) for row in rows)}/{sum(bool(row['goal_revisions']) for row in rows)} applicable Episodes",
        f"- Model decisions: {sum(int(row['model_calls']) for row in rows)}",
        f"- Tool calls: {sum(int(row['tool_calls']) for row in rows)}",
        "",
        "| Episode | condition | outcome | depth | turns | latency s | first action s | primary chars | repeats | goal recovered |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['episode_id']} | {row['user_condition']} | {row['final_status']} | "
            f"{row['expression_depth']} | {row['turns']} | "
            f"{float(row['latency_ms']) / 1000:.2f} | "
            f"{(float(row['first_effective_action_ms']) / 1000 if row['first_effective_action_ms'] is not None else 0):.2f} | "
            f"{row['primary_chars']} | {row['repeated_execute_actions']} | "
            f"{row['goal_correction_recovered']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "This small targeted run verifies wiring and representative longitudinal "
                "behavior. It does not estimate population-level satisfaction, accessibility, "
                "or universal domain competence. Those claims require larger M7 datasets."
            ),
            "",
        ]
    )
    (output_dir / "M6_REAL_MODEL_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize real-model M6 episodes")
    parser.add_argument("raw_runs")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    raw_path = Path(args.raw_runs).resolve()
    output = Path(args.output_dir).resolve() if args.output_dir else raw_path.parent
    write_real_report(raw_path, output)


if __name__ == "__main__":
    main()
