from __future__ import annotations

import argparse
import asyncio

from .runner import run_m5_episodes, save_m5_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Concord M5 long episodes")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output-dir", default="evaluation/m5_long_horizon/results/latest"
    )
    parser.add_argument("--variant", default="v2_full")
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    runs = await run_m5_episodes(
        base_url=args.base_url,
        concurrency=args.concurrency,
        variant=args.variant,
    )
    save_m5_report(args.output_dir, runs)
    passed = sum(item.episode.passed and not item.governance_failures for item in runs)
    print(f"M5: {passed}/{len(runs)} passed; artifacts={args.output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
