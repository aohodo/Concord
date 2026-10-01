from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from .catalog import load_episodes
from .report import save_report_bundle
from .runner import M4EpisodeRun, evaluate_episodes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Concord M4 through HTTP APIs")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--catalog")
    parser.add_argument("--output-dir", default="evaluation/m4_end_to_end/results/latest")
    parser.add_argument("--variant", default="v2_full")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--condition")
    parser.add_argument("--domain")
    parser.add_argument("--episode-id", action="append")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="Persist results after each bounded batch; zero runs one batch.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep completed Episode rows already present in raw_runs.jsonl.",
    )
    parser.add_argument(
        "--rerun-episode-id",
        action="append",
        help="Replace a named checkpoint row, for example after an invalid host suspension.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    episodes = load_episodes(args.catalog)
    if args.condition:
        episodes = [item for item in episodes if item.user_condition == args.condition]
    if args.domain:
        episodes = [item for item in episodes if item.domain == args.domain]
    if args.episode_id:
        requested = set(args.episode_id)
        episodes = [item for item in episodes if item.episode_id in requested]
    if args.limit:
        episodes = episodes[: args.limit]
    output = Path(args.output_dir).resolve()
    raw_path = output / "raw_runs.jsonl"
    runs: list[M4EpisodeRun] = []
    if args.resume and raw_path.exists():
        runs = [
            M4EpisodeRun.model_validate_json(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    rerun_ids = set(args.rerun_episode_id or [])
    unknown_reruns = rerun_ids.difference(item.episode_id for item in episodes)
    if unknown_reruns:
        raise ValueError(f"unknown rerun Episode IDs: {sorted(unknown_reruns)}")
    if rerun_ids:
        runs = [item for item in runs if item.episode_id not in rerun_ids]
    completed = {item.episode_id for item in runs}
    pending = [item for item in episodes if item.episode_id not in completed]
    batch_size = len(pending) if args.batch_size <= 0 else max(1, args.batch_size)
    run_id = args.run_id or uuid4().hex[:10]
    order = {item.episode_id: index for index, item in enumerate(episodes)}
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        batch_runs = await evaluate_episodes(
            batch,
            base_url=args.base_url,
            variant=args.variant,
            concurrency=min(args.concurrency, len(batch)),
            run_id=f"{run_id}-b{offset // batch_size + 1:02d}",
        )
        runs.extend(batch_runs)
        runs.sort(key=lambda item: order.get(item.episode_id, len(order)))
        save_report_bundle(output, runs)
        print(
            f"M4 checkpoint: {len(runs)}/{len(episodes)} Episodes persisted; "
            f"batch={offset // batch_size + 1}"
        )
    if not pending:
        save_report_bundle(output, runs)
    passed = sum(item.passed for item in runs)
    print(f"M4: {passed}/{len(runs)} passed; artifacts={output}")


if __name__ == "__main__":
    asyncio.run(main())
