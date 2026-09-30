"""System-level M3 controls that separate gating from team-shape effects."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from uuid import uuid4

from evaluation.m4_end_to_end.catalog import load_episodes
from evaluation.m4_end_to_end.report import write_jsonl
from evaluation.m4_end_to_end.runner import M4EpisodeRun, evaluate_episodes

from .runner import M5_EPISODE_IDS

VARIANTS = [
    "v2_m2_only",
    "v2_always_on_fixed_three",
    "v2_gated_fixed_three",
    "v2_full",
]


def _summary(variant: str, runs: list[M4EpisodeRun]) -> dict:
    total = len(runs)
    environment_goal_met = sum(
        not any(
            failure in {"GOAL_NOT_VERIFIED", "UNVERIFIED_SUCCESS_CLAIM"}
            or failure.startswith(("CHAT_HTTP_", "EXCEPTION:"))
            for failure in item.failures
        )
        for item in runs
    )
    regressed_after_resolution = 0
    for item in runs:
        events = item.case_snapshot.get("events", [])
        resolved_before_final = any(
            event.get("event_type")
            in {"m2_run_completed", "m2_resumed_after_collaboration"}
            and event.get("status") == "resolved"
            for event in events
        )
        if resolved_before_final and item.final_status != "resolved":
            regressed_after_resolution += 1
    return {
        "variant": variant,
        "episodes": total,
        "governance_passed": sum(item.passed for item in runs),
        "environment_goal_met": environment_goal_met,
        "regressed_after_resolution": regressed_after_resolution,
        "formulation_errors": sum(
            item.final_status == "formulation_error" for item in runs
        ),
        "m3_reached": sum(bool(item.case_snapshot.get("m3")) for item in runs),
        "model_calls": sum(
            item.m1_model_calls + item.m2_model_calls + item.m3_model_calls
            for item in runs
        ),
        "tool_calls": sum(item.tool_calls for item in runs),
        "latency_ms": round(sum(item.latency_ms for item in runs) / max(total, 1), 2),
    }


def _write_summaries(output: Path, summaries: list[dict]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "topology_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "topology_summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    lines = [
        "# M5 M3 System-Level Topology Comparison",
        "",
        (
            "`environment goal` and `governance pass` are deliberately separate: "
            "a tool action may solve the simulated environment while later orchestration "
            "regresses the Case state or remains unfinished."
        ),
        "",
        (
            "| variant | governance pass | environment goal | regressed after resolved | "
            "M1 errors | M3 reached | model calls | tool calls | avg latency ms |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['variant']} | {row['governance_passed']}/{row['episodes']} | "
            f"{row['environment_goal_met']}/{row['episodes']} | "
            f"{row['regressed_after_resolution']} | {row['formulation_errors']} | "
            f"{row['m3_reached']} | {row['model_calls']} | {row['tool_calls']} | "
            f"{row['latency_ms']:.1f} |"
        )
    lines.extend(
        [
            "",
            (
                "`always_on_fixed_three → gated_fixed_three` isolates collaboration-gate "
                "savings. `gated_fixed_three → v2_full` isolates dynamic team-shape and "
                "stopping. `m2_only → v2_full` measures sparse recovery value."
            ),
            "",
        ]
    )
    (output / "M5_M3_TOPOLOGY_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


async def run_comparison(
    *, base_url: str, output_dir: str | Path, concurrency: int
) -> list[dict]:
    episodes = [
        item for item in load_episodes() if item.episode_id in M5_EPISODE_IDS
    ]
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    session_id = uuid4().hex[:8]
    for variant in VARIANTS:
        runs = await evaluate_episodes(
            episodes,
            base_url=base_url,
            variant=variant,
            concurrency=concurrency,
            run_id=f"m5-topology-{variant}-{session_id}",
        )
        write_jsonl(output / f"{variant}.jsonl", runs)
        summaries.append(_summary(variant, runs))
    _write_summaries(output, summaries)
    return summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run corrected M5 M3 controls")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output-dir", default="evaluation/m5_long_horizon/results/topology"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Rebuild summaries from existing per-variant JSONL without model calls.",
    )
    return parser.parse_args()


def summarize_existing(output_dir: str | Path) -> list[dict]:
    output = Path(output_dir).resolve()
    summaries: list[dict] = []
    for variant in VARIANTS:
        path = output / f"{variant}.jsonl"
        runs = [
            M4EpisodeRun.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        summaries.append(_summary(variant, runs))
    _write_summaries(output, summaries)
    return summaries


if __name__ == "__main__":
    args = parse_args()
    if args.summarize_only:
        summarize_existing(args.output_dir)
    else:
        asyncio.run(
            run_comparison(
                base_url=args.base_url,
                output_dir=args.output_dir,
                concurrency=args.concurrency,
            )
        )
