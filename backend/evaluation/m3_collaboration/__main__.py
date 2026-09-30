from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from core.llm_client import LLMConfig, create_llm_client
from core.multi_agent_collaboration import AdaptiveCollaborationService, CollaborationTopology
from infrastructure.agent_performance import InMemoryAgentPerformanceStore

from .catalog import load_cases
from .longitudinal import load_longitudinal_episodes, run_longitudinal_catalog
from .runner import run_catalog, write_jsonl


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Concord M3 collaboration eval")
    parser.add_argument("--catalog")
    parser.add_argument("--output", default="m3_collaboration.jsonl")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--ids", nargs="*", default=[])
    parser.add_argument("--longitudinal", action="store_true")
    parser.add_argument(
        "--topologies",
        nargs="+",
        choices=[item.value for item in CollaborationTopology],
        default=[CollaborationTopology.ADAPTIVE.value],
    )
    args = parser.parse_args()

    load_dotenv()
    cfg = LLMConfig.from_env()
    client = create_llm_client(cfg)
    try:
        service = AdaptiveCollaborationService(
            checkpointer=InMemorySaver(),
            performance_store=InMemoryAgentPerformanceStore(),
            client=client,
            model=cfg.model,
        )
        if args.longitudinal:
            episodes = load_longitudinal_episodes(args.catalog)
            if args.ids:
                selected = set(args.ids)
                episodes = [
                    episode for episode in episodes if episode.episode_id in selected
                ]
            rows = await run_longitudinal_catalog(
                service, episodes, concurrency=args.concurrency
            )
        else:
            cases = load_cases(args.catalog)
            if args.ids:
                selected = set(args.ids)
                cases = [case for case in cases if case.case_id in selected]
            rows = await run_catalog(
                service,
                cases,
                concurrency=args.concurrency,
                topologies=[CollaborationTopology(item) for item in args.topologies],
            )
        write_jsonl(args.output, rows)
        passed = sum(
            bool(item.get("passed", item.get("quality_boundary_pass"))) for item in rows
        )
        print(f"M3 evaluation: {passed}/{len(rows)} boundary checks passed")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
