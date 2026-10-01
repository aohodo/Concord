"""Run M4 mechanism controls on a cross-domain representative subset."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from uuid import uuid4

from .catalog import load_episodes
from .report import write_jsonl
from .runner import M4EpisodeRun, evaluate_episodes

DEFAULT_EPISODES = {
    "vpn_stale_credential__fragmented_novice",
    "vpn_stale_credential__deadline_result_first",
    "saas_webhook_secret__self_correction",
    "education_sso_clock_drift__fragmented_novice",
    "logistics_spooler_backlog__deadline_result_first",
    "inventory_cache_stale__self_correction",
    "dependency_failover__deadline_result_first",
    "write_committed_response_lost__fragmented_novice",
    "asynchronous_recovery__self_correction",
    "permission_boundary__deadline_result_first",
    "conflicting_config_reports__self_correction",
    "specialist_recovery_after_tool_failures__self_correction",
    "vpn_stale_credential__low_control",
    "saas_webhook_secret__frustrated_repeat",
    "education_sso_clock_drift__procedural_only",
    "logistics_spooler_backlog__multi_issue_dump",
    "inventory_cache_stale__low_patience_stream",
    "dependency_failover__expert_precise",
    "write_committed_response_lost__half_expert_hypothesis",
    "conflicting_config_reports__cross_domain_transfer",
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
        len(str(turn.response.get("response", ""))) for run in runs for turn in run.turns
    )
    first_progress = [
        float(turn.first_progress_ms)
        for run in runs
        for turn in run.turns
        if turn.first_progress_ms is not None
    ]
    m1_latencies = [
        float(turn.response.get("stage_latency_ms", {}).get("m1_formulation", 0))
        for run in runs
        for turn in run.turns
        if turn.response.get("stage_latency_ms", {}).get("m1_formulation") is not None
    ]
    m2_latencies = [
        float(turn.response.get("stage_latency_ms", {}).get("m2_resolution", 0))
        for run in runs
        for turn in run.turns
        if turn.response.get("stage_latency_ms", {}).get("m2_resolution") is not None
    ]
    usages = [run.case_snapshot.get("human_collaboration", {}).get("usage", {}) for run in runs]
    mechanism_events = [
        event
        for run in runs
        for event in run.case_snapshot.get("events", [])
        if event.get("event_type") == "control_mechanism_evaluated"
    ]
    repeated_failed_calls = 0
    for run in runs:
        failed_fingerprints: list[str] = []
        for event in run.tool_trace.get("trace", {}).get("events", []):
            if event.get("result_status") == "succeeded":
                continue
            failed_fingerprints.append(
                f"{event.get('tool_id')}:{json.dumps(event.get('arguments', {}), sort_keys=True)}"
            )
        repeated_failed_calls += len(failed_fingerprints) - len(set(failed_fingerprints))
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
            item.m1_model_calls + item.m2_model_calls + item.m3_model_calls for item in runs
        ),
        "tool_calls": sum(item.tool_calls for item in runs),
        "avg_user_turns": round(user_turns / total, 3) if total else 0.0,
        "avg_response_characters": (round(response_characters / total, 3) if total else 0.0),
        "median_first_progress_ms": round(median(first_progress), 3) if first_progress else None,
        "first_progress_measured": len(first_progress),
        "avg_m1_latency_ms": round(sum(m1_latencies) / len(m1_latencies), 3)
        if m1_latencies
        else 0.0,
        "avg_m2_latency_ms": round(sum(m2_latencies) / len(m2_latencies), 3)
        if m2_latencies
        else 0.0,
        "questions_asked": sum(int(item.get("questions_asked", 0)) for item in usages),
        "user_actions_requested": sum(
            int(item.get("user_actions_requested", 0)) for item in usages
        ),
        "visible_progress_updates": sum(
            int(item.get("visible_progress_updates", 0)) for item in usages
        ),
        "mechanism_evaluations": len(mechanism_events),
        "mechanism_activations": sum(
            event.get("status") == "activated" for event in mechanism_events
        ),
        "mechanism_behavior_changes": sum(
            bool(event.get("data", {}).get("behavior_changes"))
            for event in mechanism_events
            if event.get("status") == "activated"
        ),
        "repeated_failed_tool_calls": repeated_failed_calls,
        "failure_labels": dict(failures),
        "coverage_notes": dict(coverage_notes),
    }


def _write_stratified_results(
    output: Path,
    grouped: dict[str, list[M4EpisodeRun]],
) -> None:
    rows = []
    for variant, runs in grouped.items():
        for dimension in ("domain", "user_condition"):
            buckets: dict[str, list[M4EpisodeRun]] = defaultdict(list)
            for run in runs:
                buckets[str(getattr(run, dimension))].append(run)
            for value, items in sorted(buckets.items()):
                rows.append(
                    {
                        "variant": variant,
                        "dimension": dimension,
                        "value": value,
                        "episodes": len(items),
                        "passed": sum(item.passed for item in items),
                        "resolved": sum(item.final_status == "resolved" for item in items),
                        "avg_latency_ms": round(
                            sum(item.latency_ms for item in items) / len(items), 3
                        ),
                        "model_calls": sum(
                            item.m1_model_calls + item.m2_model_calls + item.m3_model_calls
                            for item in items
                        ),
                        "tool_calls": sum(item.tool_calls for item in items),
                    }
                )
    with (output / "stratified_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_paired_results(
    output: Path,
    grouped: dict[str, list[M4EpisodeRun]],
) -> list[dict]:
    full = {item.episode_id: item for item in grouped.get("v2_full", [])}
    rows: list[dict] = []
    for variant, runs in grouped.items():
        if variant == "v2_full":
            continue
        for run in runs:
            baseline = full.get(run.episode_id)
            if baseline is None:
                continue
            run_chars = sum(len(str(item.response.get("response", ""))) for item in run.turns)
            base_chars = sum(len(str(item.response.get("response", ""))) for item in baseline.turns)
            rows.append(
                {
                    "variant": variant,
                    "episode_id": run.episode_id,
                    "domain": run.domain,
                    "user_condition": run.user_condition,
                    "pass_delta": int(run.passed) - int(baseline.passed),
                    "latency_delta_ms": round(run.latency_ms - baseline.latency_ms, 3),
                    "model_call_delta": (
                        run.m1_model_calls
                        + run.m2_model_calls
                        + run.m3_model_calls
                        - baseline.m1_model_calls
                        - baseline.m2_model_calls
                        - baseline.m3_model_calls
                    ),
                    "tool_call_delta": run.tool_calls - baseline.tool_calls,
                    "response_character_delta": run_chars - base_chars,
                }
            )
    with (output / "paired_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _write_comparison(output: Path, grouped: dict[str, list[M4EpisodeRun]]) -> None:
    summaries = [_summarize(variant, runs) for variant, runs in grouped.items()]
    paired = _write_paired_results(output, grouped)
    _write_stratified_results(output, grouped)
    (output / "comparison_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output / "comparison_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
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
                "median_first_progress_ms",
                "first_progress_measured",
                "avg_m1_latency_ms",
                "avg_m2_latency_ms",
                "questions_asked",
                "user_actions_requested",
                "visible_progress_updates",
                "mechanism_evaluations",
                "mechanism_activations",
                "mechanism_behavior_changes",
                "repeated_failed_tool_calls",
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
        "| variant | pass | M3 | model calls | tools | avg turns | first progress ms | M1 ms | M2 ms | avg episode ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['variant']} | {row['passed']}/{row['episodes']} | "
            f"{row['m3_reached']} | {row['model_calls']} | "
            f"{row['tool_calls']} | {row['avg_user_turns']:.2f} | "
            f"{row['median_first_progress_ms'] or 0:.1f} | "
            f"{row['avg_m1_latency_ms']:.1f} | {row['avg_m2_latency_ms']:.1f} | "
            f"{row['avg_latency_ms']:.1f} |"
        )
    paired_by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in paired:
        paired_by_variant[row["variant"]].append(row)
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
                "Interpret effects as paired observations on the same Episodes. Endpoint success, "
                "latency, model/tool cost, interaction burden and call-chain activation are kept "
                "separate; an untriggered mechanism is not labelled ineffective."
            ),
            "",
            "## Paired observations versus full",
            "",
            "| variant | pairs | success worse/same/better | latency faster/slower | calls lower/higher |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for variant, rows in sorted(paired_by_variant.items()):
        worse = sum(row["pass_delta"] < 0 for row in rows)
        better = sum(row["pass_delta"] > 0 for row in rows)
        same = len(rows) - worse - better
        faster = sum(row["latency_delta_ms"] < 0 for row in rows)
        slower = sum(row["latency_delta_ms"] > 0 for row in rows)
        fewer_calls = sum(row["model_call_delta"] < 0 for row in rows)
        more_calls = sum(row["model_call_delta"] > 0 for row in rows)
        lines.append(
            f"| {variant} | {len(rows)} | {worse}/{same}/{better} | "
            f"{faster}/{slower} | {fewer_calls}/{more_calls} |"
        )
    lines.extend(
        [
            "",
            (
                "`stratified_results.csv` exposes domain and user-condition slices; "
                "`paired_results.csv` preserves every same-Episode delta."
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
                    f"{variant}.jsonl" if variant != "v2_full" else _portable_path(full_runs)
                ),
                "episodes": len(runs),
                "episode_ids": [item.episode_id for item in runs],
            }
            for variant, runs in grouped.items()
        },
        "derived_artifacts": [
            "comparison_summary.json",
            "comparison_summary.csv",
            "paired_results.csv",
            "stratified_results.csv",
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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Checkpoint each variant after this many Episodes.",
    )
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
    full = [item for item in _read_jsonl(Path(args.full_runs)) if item.episode_id in requested]
    if len(full) != len(episodes):
        raise ValueError("full-run artifact does not cover every requested Episode")
    grouped: dict[str, list[M4EpisodeRun]] = {"v2_full": full}
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session_id = uuid4().hex[:8]
    for variant in args.variants:
        variant_path = output / f"{variant}.jsonl"
        existing = _read_jsonl(variant_path) if args.resume and variant_path.exists() else []
        runs_by_episode = {
            item.episode_id: item for item in existing if item.episode_id in requested
        }
        pending = [item for item in episodes if item.episode_id not in runs_by_episode]
        batch_size = max(1, args.batch_size)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            completed = await evaluate_episodes(
                batch,
                base_url=args.base_url,
                variant=variant,
                concurrency=args.concurrency,
                run_id=f"comparison-{variant}-{session_id}-b{start // batch_size + 1:02d}",
            )
            runs_by_episode.update({item.episode_id: item for item in completed})
            checkpoint = [
                runs_by_episode[item.episode_id]
                for item in episodes
                if item.episode_id in runs_by_episode
            ]
            write_jsonl(variant_path, checkpoint)
        runs = [runs_by_episode[item.episode_id] for item in episodes]
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
