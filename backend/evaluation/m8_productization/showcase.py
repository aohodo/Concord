"""Render a compact, auditable M8-B vertical-slice demonstration."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_EPISODE = "specialist_recovery_after_tool_failures__self_correction"


def _load_run(path: Path, episode_id: str) -> dict[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("episode_id") == episode_id:
            return row
    raise ValueError(f"Episode {episode_id!r} not found in {path}")


def _retained_experiences(database: Path | None, case_id: str) -> int | None:
    if database is None or not database.exists():
        return None
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM m7_experiences WHERE source_case_id = ?",
                (case_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()


def build_showcase(run: dict[str, Any], *, retained: int | None) -> dict[str, Any]:
    snapshot = run.get("case_snapshot", {})
    events = snapshot.get("events", [])
    event_types = [str(item.get("event_type", "")) for item in events]
    m3 = snapshot.get("m3") or {}
    m2 = snapshot.get("m2") or {}
    collaboration_applied = any(
        item.get("collaboration_source_ids")
        for item in m2.get("tool_history", [])
        if isinstance(item, dict)
    )
    latest_response = (
        run.get("turns", [])[-1].get("response", {}) if run.get("turns") else {}
    )
    tool_events = run.get("tool_trace", {}).get("trace", {}).get("events", [])
    return {
        "episode_id": run.get("episode_id"),
        "case_id": run.get("case_id"),
        "passed": bool(run.get("passed")),
        "final_status": run.get("final_status"),
        "latency_seconds": round(float(run.get("latency_ms", 0.0)) / 1000, 2),
        "user_turns": len(run.get("turns", [])),
        "model_calls": sum(
            int(run.get(key, 0))
            for key in ("m1_model_calls", "m2_model_calls", "m3_model_calls")
        ),
        "tool_calls": len(tool_events),
        "chain": {
            "m1_formulated": "m1_turn_completed" in event_types,
            "m2_executed": "m2_run_completed" in event_types,
            "m3_triggered": "m3_started" in event_types,
            "m3_agents": list(m3.get("agents_recruited", [])),
            "m2_resumed": (
                "m2_resumed_after_collaboration" in event_types
                or collaboration_applied
            ),
            "goal_verified": run.get("final_status") == "resolved"
            and not run.get("failures"),
            "outcome_verdict": latest_response.get("outcome_verdict", "unavailable"),
            "retained_experiences": retained,
        },
        "tool_statuses": [
            {
                "tool_id": item.get("tool_id"),
                "status": item.get("result_status"),
                "error_code": item.get("error_code"),
            }
            for item in tool_events
        ],
        "interpretation": (
            "This is one environment-grounded vertical slice, not a universal quality claim."
        ),
    }


def _render(summary: dict[str, Any]) -> str:
    chain = summary["chain"]
    retention = chain["retained_experiences"]
    rows = [
        ("M1 shared formulation", chain["m1_formulated"]),
        ("M2 evidence-action loop", chain["m2_executed"]),
        ("Conditional M3", chain["m3_triggered"]),
        ("M2 resumed", chain["m2_resumed"]),
        ("Goal independently verified", chain["goal_verified"]),
        ("Verified experience retained", retention if retention is not None else "unavailable"),
    ]
    lines = [
        "Concord M8-B vertical slice",
        f"Episode: {summary['episode_id']}",
        f"Result: {'PASS' if summary['passed'] else 'FAIL'} ({summary['final_status']})",
        (
            f"Cost: {summary['latency_seconds']:.2f}s, {summary['user_turns']} user turns, "
            f"{summary['model_calls']} model calls, {summary['tool_calls']} tool calls"
        ),
        f"M3 agents: {', '.join(chain['m3_agents']) or 'none'}",
        "",
    ]
    def marker(value: object) -> str:
        if value == "unavailable":
            return "?"
        return "x" if bool(value) else " "

    lines.extend(f"[{marker(value)}] {label}: {value}" for label, value in rows)
    lines.extend(["", summary["interpretation"]])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--episode-id", default=DEFAULT_EPISODE)
    parser.add_argument("--runtime-db", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    run = _load_run(args.artifact, args.episode_id)
    retained = _retained_experiences(args.runtime_db, str(run.get("case_id", "")))
    summary = build_showcase(run, retained=retained)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(_render(summary))
    return 1 if args.require_pass and not summary["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
