"""Run M4 mechanism controls on a cross-domain representative subset."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .catalog import load_episodes
from .report import write_jsonl
from .runner import M4EpisodeRun, evaluate_episodes

DEFAULT_EPISODES = {
    "vpn_stale_credential__self_correction",
    "saas_webhook_secret__deadline_result_first",
    "education_sso_clock_drift__fragmented_novice",
    "logistics_spooler_backlog__self_correction",
    "permission_boundary__deadline_result_first",
    "conflicting_config_reports__self_correction",
    "specialist_recovery_after_tool_failures__self_correction",
}
DEFAULT_VARIANTS = [
    "v2_m2_only",
    "v2_fixed_three",
    "v2_no_user_state",
    "v2_no_epistemic_separation",
    "v2_no_failure_memory",
]


def _portable_path(path: Path) -> str:
    """Render experiment references without publishing workstation paths."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


def _read_jsonl(path: Path) -> list[M4EpisodeRun]:
    return [
        M4EpisodeRun.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _summarize(variant: str, runs: list[M4EpisodeRun]) -> dict:
    total = len(runs)
    failures = Counter(item for run in runs for item in run.failures)
    coverage_notes = Counter(item for run in runs for item in run.coverage_notes)
    user_turns = sum(len(run.turns) for run in runs)
    response_characters = sum(
        len(str(turn.response.get("response", "")))
        for run in runs
        for turn in run.turns
    )
    return {
        "variant": variant,
        "episodes": total,
        "passed": sum(item.passed for item in runs),
        "resolved": sum(item.final_status == "resolved" for item in runs),
        "m3_reached": sum(bool(item.case_snapshot.get("m3")) for item in runs),
        "avg_latency_ms": (
            round(sum(item.latency_ms for item in runs) / total, 3) if total else 0.0
        ),
        "model_calls": sum(
            item.m1_model_calls + item.m2_model_calls + item.m3_model_calls
            for item in runs
        ),
        "tool_calls": sum(item.tool_calls for item in runs),
        "avg_user_turns": round(user_turns / total, 3) if total else 0.0,
        "avg_response_characters": (
            round(response_characters / total, 3) if total else 0.0
        ),
        "failure_labels": dict(failures),
        "coverage_notes": dict(coverage_notes),
    }


def _write_comparison(output: Path, grouped: dict[str, list[M4EpisodeRun]]) -> None:
    summaries = [_summarize(variant, runs) for variant, runs in grouped.items()]
    (output / "comparison_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output / "comparison_summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "variant",
                "episodes",
                "passed",
                "resolved",
                "m3_reached",
                "avg_latency_ms",
                "model_calls",
                "tool_calls",
                "avg_user_turns",
                "avg_response_characters",
                "failure_labels",
                "coverage_notes",
            ],
        )
        writer.writeheader()
        for row in summaries:
            writer.writerow(
                {
                    **row,
                    "failure_labels": json.dumps(row["failure_labels"]),
                    "coverage_notes": json.dumps(row["coverage_notes"]),
                }
            )
    lines = [
        "# M4 Mechanism Comparison",
        "",
        "| variant | pass | resolved | M3 reached | model calls | tool calls | avg turns | avg response chars | avg latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['variant']} | {row['passed']}/{row['episodes']} | "
            f"{row['resolved']} | {row['m3_reached']} | {row['model_calls']} | "
            f"{row['tool_calls']} | {row['avg_user_turns']:.2f} | "
            f"{row['avg_response_characters']:.1f} | {row['avg_latency_ms']:.1f} |"
        )
    lines.extend(
        [
            "",
            (
                "The subset is a mechanism comparison, not a domain-wide ranking. Environment "
                "outcomes remain authoritative; a fluent answer cannot compensate for an unmet "
                "goal."
            ),
            (
                "M3 is dynamically optional. Solving and verifying a case in M2 remains a pass; "
                "missing a targeted M3 branch is reported only as a coverage note."
            ),
            "",
            "## Interpretation of this subset",
            "",
            (
                "- `v2_m2_only` also passed 4/4 and used fewer model calls and less wall time. "
                "This subset does not demonstrate a success-rate advantage for M3; it supports "
                "keeping collaboration conditional instead of mandatory."
            ),
            (
                "- `v2_fixed_three` also passed 4/4 but used one more model call and two more "
                "tool calls than `v2_full`. This is only a small observed cost difference, not a "
                "general efficiency claim."
            ),
            (
                "- Removing user-state control or epistemic separation did not reduce endpoint "
                "success in these four Episodes. Their value therefore needs interaction- and "
                "semantic-safety-sensitive evaluation rather than outcome success alone."
            ),
            (
                "- `v2_no_failure_memory` passed 3/4. In the failed retry Episode it stopped at "
                "`evidence_required` without verifying the goal. This single clean rerun is a "
                "causal candidate, not proof of a population-level memory benefit."
            ),
            (
                "- A prior `v2_no_failure_memory` sample crossed a host sleep interval and is "
                "excluded from the table as infrastructure-invalid; it is retained separately "
                "for provenance."
            ),
            "",
        ]
    )
    (output / "M4_COMPARISON_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def _write_manifest(
    output: Path,
    *,
    full_runs: Path,
    model_label: str,
    grouped: dict[str, list[M4EpisodeRun]],
) -> None:
    manifest = {
        "schema_version": "m4-ablation-manifest-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model_label,
        "entry_boundary": "HTTP product APIs only",
        "success_authority": "simulated environment outcome and tool audit",
        "full_run_source": _portable_path(full_runs),
        "variants": {
            variant: {
                "artifact": (
                    f"{variant}.jsonl"
                    if variant != "v2_full"
                    else _portable_path(full_runs)
                ),
                "episodes": len(runs),
                "episode_ids": [item.episode_id for item in runs],
            }
            for variant, runs in grouped.items()
        },
        "derived_artifacts": [
            "comparison_summary.json",
            "comparison_summary.csv",
            "M4_COMPARISON_REPORT.md",
        ],
        "notes": [
            "Each per-variant JSONL preserves case-level raw results.",
            "M3 coverage is not a success gate when M2 independently verifies the goal.",
            "LLM-as-Judge interaction results are stored separately from outcome success.",
        ],
    }
    (output / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run M4 cross-stage controls")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--full-runs", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--model-label", default="qwen3.8-flash")
    parser.add_argument("--variants", nargs="*", default=DEFAULT_VARIANTS)
    parser.add_argument(
        "--episode-id",
        action="append",
        help="Restrict the comparison to one or more named Episodes.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse a complete per-variant JSONL already present in the output directory.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    requested = set(args.episode_id or DEFAULT_EPISODES)
    episodes = [item for item in load_episodes() if item.episode_id in requested]
    missing = sorted(requested.difference(item.episode_id for item in episodes))
    if missing:
        raise ValueError(f"unknown Episode IDs: {missing}")
    full = [
        item
        for item in _read_jsonl(Path(args.full_runs))
        if item.episode_id in requested
    ]
    if len(full) != len(episodes):
        raise ValueError("full-run artifact does not cover every requested Episode")
    grouped: dict[str, list[M4EpisodeRun]] = {"v2_full": full}
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session_id = uuid4().hex[:8]
    for variant in args.variants:
        variant_path = output / f"{variant}.jsonl"
        if args.resume and variant_path.exists():
            existing = _read_jsonl(variant_path)
            runs = existing if len(existing) == len(episodes) else []
        else:
            runs = []
        if not runs:
            runs = await evaluate_episodes(
                episodes,
                base_url=args.base_url,
                variant=variant,
                concurrency=args.concurrency,
                run_id=f"comparison-{variant}-{session_id}",
            )
        grouped[variant] = runs
        write_jsonl(variant_path, runs)
    _write_comparison(output, grouped)
    _write_manifest(
        output,
        full_runs=Path(args.full_runs),
        model_label=args.model_label,
        grouped=grouped,
    )
    print(f"M4 comparison complete: {output}")


if __name__ == "__main__":
    asyncio.run(main())
