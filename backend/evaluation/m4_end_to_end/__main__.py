from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .catalog import load_episodes
from .report import save_report_bundle
from .runner import evaluate_episodes


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
    runs = await evaluate_episodes(
        episodes,
        base_url=args.base_url,
        variant=args.variant,
        concurrency=args.concurrency,
        run_id=args.run_id,
    )
    output = Path(args.output_dir).resolve()
    save_report_bundle(output, runs)
    passed = sum(item.passed for item in runs)
    print(f"M4: {passed}/{len(runs)} passed; artifacts={output}")


if __name__ == "__main__":
    asyncio.run(main())
