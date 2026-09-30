"""CLI for a one-pass real-model M2 scenario evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from core.adaptive_resolution import AdaptiveResolutionService
from core.llm_client import LLMConfig, create_llm_client
from infrastructure.simulation import ScenarioRuntime, build_simulation_tools
from infrastructure.tool_runtime import AdaptiveToolRuntime

from .catalog import load_scenario_catalog
from .humanity import HumanityJudge
from .longitudinal import (
    evaluate_longitudinal_scenarios,
    load_longitudinal_catalog,
)
from .runner import evaluate_scenarios


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--ids", nargs="*", default=[])
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--longitudinal", action="store_true")
    parser.add_argument("--judge-humanity", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    backend_root = Path(__file__).resolve().parents[2]
    load_dotenv(backend_root / ".env")
    scenarios = (
        load_longitudinal_catalog(args.catalog)
        if args.longitudinal
        else load_scenario_catalog(args.catalog)
    )
    if args.ids:
        wanted = set(args.ids)
        id_field = "trajectory_id" if args.longitudinal else "scenario_id"
        scenarios = [item for item in scenarios if getattr(item, id_field) in wanted]
        missing = wanted - {getattr(item, id_field) for item in scenarios}
        if missing:
            raise ValueError(f"unknown scenario ids: {', '.join(sorted(missing))}")

    config = LLMConfig.from_env()
    client = create_llm_client(config)
    scenario_runtime = ScenarioRuntime()
    tool_runtime = AdaptiveToolRuntime(scenarios=scenario_runtime)
    for spec in build_simulation_tools(scenario_runtime):
        tool_runtime.registry.register(spec)
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=tool_runtime,
        client=client,
        model=config.model,
    )
    try:
        if args.longitudinal:
            results = await evaluate_longitudinal_scenarios(
                scenarios,
                service=service,
                tool_runtime=tool_runtime,
                concurrency=args.concurrency,
            )
        else:
            results = await evaluate_scenarios(
                scenarios,
                service=service,
                tool_runtime=tool_runtime,
                concurrency=args.concurrency,
            )
        if args.judge_humanity:
            judge = HumanityJudge(client, config.model)
            semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 6)))

            async def judge_one(item, scenario):
                async with semaphore:
                    try:
                        judgment = await judge.judge(
                            case_id=item.case_id,
                            user_trajectory=scenario.model_dump(mode="json"),
                            runtime_result=item.raw_result,
                        )
                        payload = judgment.model_dump(mode="json")
                    except Exception as exc:  # noqa: BLE001 - keep technical result
                        payload = {
                            "case_id": item.case_id,
                            "error": f"{type(exc).__name__}:{exc}",
                        }
                    return item.model_copy(update={"humanity_judgment": payload})

            results = list(
                await asyncio.gather(
                    *(judge_one(item, scenario) for item, scenario in zip(results, scenarios, strict=True))
                )
            )
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()

    if args.output:
        target = args.output.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(item.model_dump_json() + "\n" for item in results),
            encoding="utf-8",
        )
    if args.longitudinal:
        summary = {
            "mode": "longitudinal",
            "model": config.model,
            "cases": len(results),
            "passed": sum(item.passed for item in results),
            "failed": sum(not item.passed for item in results),
            "total_model_calls": sum(item.total_model_calls for item in results),
            "humanity_judged": sum(item.humanity_judgment is not None for item in results),
            "items": [
                {
                    "trajectory_id": item.trajectory_id,
                    "passed": item.passed,
                    "status": item.status,
                    "episodes": len(item.episodes),
                    "total_model_calls": item.total_model_calls,
                    "failures": item.failures,
                }
                for item in results
            ],
        }
    else:
        summary = {
        "model": config.model,
        "cases": len(results),
        "passed": sum(item.passed for item in results),
        "failed": sum(not item.passed for item in results),
        "mean_latency_ms": round(
            sum(item.latency_ms for item in results) / max(len(results), 1), 3
        ),
        "total_model_calls": sum(item.model_calls for item in results),
        "humanity_judged": sum(item.humanity_judgment is not None for item in results),
        "items": [
            {
                "scenario_id": item.scenario_id,
                "passed": item.passed,
                "status": item.status,
                "latency_ms": item.latency_ms,
                "model_calls": item.model_calls,
                "tool_calls": item.tool_calls,
                "failures": item.failures,
            }
            for item in results
        ],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(item.passed for item in results) else 1


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
