"""Fill missing or failed humanity judgments without rerunning M2 trajectories."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.llm_client import LLMConfig, create_llm_client

from .humanity import HumanityJudge
from .longitudinal import LongitudinalEvaluationResult, load_longitudinal_catalog


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    backend_root = Path(__file__).resolve().parents[2]
    load_dotenv(backend_root / ".env")
    results = [
        LongitudinalEvaluationResult.model_validate_json(line)
        for line in args.input.resolve().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trajectories = {item.trajectory_id: item for item in load_longitudinal_catalog()}
    config = LLMConfig.from_env()
    client = create_llm_client(config)
    judge = HumanityJudge(client, config.model)
    semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 8)))

    async def fill(item: LongitudinalEvaluationResult) -> LongitudinalEvaluationResult:
        existing = item.humanity_judgment or {}
        if existing and not existing.get("error"):
            return item
        trajectory = trajectories.get(item.trajectory_id)
        if trajectory is None:
            return item.model_copy(
                update={
                    "humanity_judgment": {
                        "case_id": item.case_id,
                        "error": "trajectory definition not found",
                    }
                }
            )
        async with semaphore:
            try:
                judgment = await judge.judge(
                    case_id=item.case_id,
                    user_trajectory=trajectory.model_dump(mode="json"),
                    runtime_result=item.raw_result,
                )
                payload: dict[str, Any] = judgment.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001 - preserve prior M2 result
                payload = {
                    "case_id": item.case_id,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            return item.model_copy(update={"humanity_judgment": payload})

    try:
        completed = await asyncio.gather(*(fill(item) for item in results))
    finally:
        await client.close()
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(item.model_dump_json() + "\n" for item in completed),
        encoding="utf-8",
    )
    remaining = sum(
        bool((item.humanity_judgment or {}).get("error")) for item in completed
    )
    print(f"judged={len(completed) - remaining} errors={remaining} output={target}")
    return 1 if remaining else 0


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
