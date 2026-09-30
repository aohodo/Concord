"""Create transparent M3 comparison and ablation artifacts from raw JSONL."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text("utf-8").splitlines()
        if line.strip()
    ]


def merge_longitudinal_runs(
    runs: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Keep the latest auditable run for each episode without mutating raw runs."""

    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for rows in runs:
        for row in rows:
            episode_id = str(row["episode_id"])
            if episode_id not in latest:
                order.append(episode_id)
            latest[episode_id] = row
    return [latest[episode_id] for episode_id in order]


def _average(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(mean(values), 2) if values else 0.0


def build_report(
    baseline_rows: list[dict[str, Any]],
    longitudinal_rows: list[dict[str, Any]],
    vertical_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in baseline_rows:
        grouped[str(row.get("topology", "unknown"))].append(row)
    summary = []
    for topology, rows in sorted(grouped.items()):
        summary.append(
            {
                "topology": topology,
                "cases": len(rows),
                "quality_boundary_passes": sum(
                    bool(row.get("quality_boundary_pass")) for row in rows
                ),
                "judge_passes": sum(
                    row.get("judge", {}).get("label") == "PASS" for row in rows
                ),
                "judge_partials": sum(
                    row.get("judge", {}).get("label") == "PARTIAL" for row in rows
                ),
                "judge_failures": sum(
                    row.get("judge", {}).get("label") == "FAIL" for row in rows
                ),
                "average_model_calls": _average(rows, "model_calls"),
                "average_agents": round(
                    mean(len(row.get("agents_recruited", [])) for row in rows), 2
                ),
                "average_latency_ms": _average(rows, "latency_ms"),
                "average_input_tokens": _average(rows, "input_tokens"),
                "average_output_tokens": _average(rows, "output_tokens"),
                "average_measured_cost": _average(
                    rows, "measured_coordination_cost"
                ),
            }
        )

    longitudinal_passes = sum(bool(row.get("passed")) for row in longitudinal_rows)
    stale_rows = [
        row
        for row in longitudinal_rows
        if row.get("stale_initial_discarded") is not None
    ]
    stale_passes = sum(
        row.get("stale_initial_discarded") is True for row in stale_rows
    )
    lines = [
        "# M3 Phase 2 Comparison and Ablation Report",
        "",
        "## Scope",
        "",
        "This report compares an M2-only control, an actually executed fixed three-agent topology, and adaptive M3. Boundary pass counts are based on declared synthetic case expectations; they are not human expert ground truth. Raw rows remain the source of truth.",
        "",
        "## Topology ablation",
        "",
        "| topology | boundary passes | judge P/Pt/F | avg model calls | avg agents | avg latency ms | avg input tokens | avg output tokens | measured cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            "| {topology} | {quality_boundary_passes}/{cases} | "
            "{judge_passes}/{judge_partials}/{judge_failures} | "
            "{average_model_calls} | {average_agents} | {average_latency_ms} | "
            "{average_input_tokens} | {average_output_tokens} | "
            "{average_measured_cost} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Longitudinal episodes",
            "",
            f"- Episodes: {len(longitudinal_rows)}",
            f"- Boundary passes: {longitudinal_passes}/{len(longitudinal_rows)}",
            f"- Mid-flight revision invalidation: {stale_passes}/{len(stale_rows)}",
            f"- Verified/simulated outcome events: {sum(int(row.get('feedback_events', 0)) for row in longitudinal_rows)}",
            f"- Total model calls: {sum(int(row.get('model_calls', 0)) for row in longitudinal_rows)}",
            f"- Total input tokens: {sum(int(row.get('input_tokens') or 0) for row in longitudinal_rows)}",
            f"- Total output tokens: {sum(int(row.get('output_tokens') or 0) for row in longitudinal_rows)}",
            f"- Average model calls: {_average(longitudinal_rows, 'model_calls')}",
            f"- Average latency: {_average(longitudinal_rows, 'latency_ms')} ms",
            "",
            "## Interpretation limits",
            "",
            "- The longitudinal catalog is synthetic and intentionally contains fragmented, mistaken, urgent, and corrected reports.",
            "- `novelty_proxy` measures structural non-duplication, not truth. Truth credit is only written after a downstream verification event.",
            "- Shared-source agreement is flagged as correlated evidence and is not treated as independent corroboration.",
            "- M3 advice remains `advisory_until_m2_verifies`; M3 never executes environment writes.",
            "- The comparison isolates collaboration topology. It does not prove universal superiority across domains or models.",
            "",
        ]
    )
    if vertical_rows:
        lines.extend(
            [
                "## Product-chain smoke tests",
                "",
                "| case | passed | M3 status | M2 status | M3 model calls | verified feedback |",
                "|---|---:|---|---|---:|---:|",
            ]
        )
        for row in vertical_rows:
            lines.append(
                "| {case_id} | {passed} | {m3_status} | {m2_status} | {calls} | {feedback} |".format(
                    case_id=row.get("case_id", "unknown"),
                    passed=str(bool(row.get("passed"))).lower(),
                    m3_status=row.get("m3_status", "unknown"),
                    m2_status=row.get("m2_resumed_status") or row.get("m2_status") or "not_resumed",
                    calls=row.get("m3_metrics", {}).get("model_calls", 0),
                    feedback=row.get("verified_feedback_events", 0),
                )
            )
        lines.append("")
    return summary, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--longitudinal", required=True, nargs="+")
    parser.add_argument("--vertical", nargs="*", default=[])
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    longitudinal_rows = merge_longitudinal_runs(
        [read_jsonl(path) for path in args.longitudinal]
    )
    vertical_rows = [
        row for path in args.vertical for row in read_jsonl(path)
    ]
    summary, report = build_report(
        read_jsonl(args.baseline), longitudinal_rows, vertical_rows
    )
    (output / "M3_PHASE2_REPORT.md").write_text(report, encoding="utf-8")
    (output / "topology_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "m3_phase2_longitudinal_final.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for row in longitudinal_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output / "topology_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]) if summary else [])
        writer.writeheader()
        writer.writerows(summary)


if __name__ == "__main__":
    main()


__all__ = ["build_report", "merge_longitudinal_runs", "read_jsonl"]
