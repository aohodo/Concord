"""Run M3 cases and expose dynamic-vs-fixed coordination measurements."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from core.multi_agent_collaboration import (
    AdaptiveCollaborationService,
    CollaborationTopology,
)

from .catalog import M3EvaluationCase


async def run_case(
    service: AdaptiveCollaborationService,
    case: M3EvaluationCase,
    *,
    topology: CollaborationTopology = CollaborationTopology.ADAPTIVE,
) -> dict[str, Any]:
    started = time.monotonic()
    result = await service.collaborate(handoff=case.handoff, topology=topology)
    dynamic_agents = len(result.agents_recruited)
    return {
        "case_id": case.case_id,
        "description": case.description,
        "topology": topology.value,
        "status": result.status.value,
        "mode": result.mode.value,
        "agents_recruited": result.agents_recruited,
        "rounds": result.rounds,
        "coordination_cost": result.coordination_cost,
        "measured_coordination_cost": result.metrics.measured_coordination_cost,
        "model_calls": result.metrics.model_calls,
        "input_tokens": result.metrics.input_tokens,
        "output_tokens": result.metrics.output_tokens,
        "novelty_proxy": result.metrics.novelty_proxy,
        "worker_failures": result.metrics.worker_failures,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "dynamic_agent_calls": dynamic_agents,
        "configured_fixed_baseline_agent_calls": case.fixed_baseline_agents,
        "agent_calls_avoided_vs_configured_fixed": (
            case.fixed_baseline_agents - dynamic_agents
        ),
        "status_acceptable": result.status in case.allowed_statuses,
        "mode_acceptable": (
            result.mode in case.expected_modes
            if topology is CollaborationTopology.ADAPTIVE
            else True
        ),
        "agent_count_acceptable": dynamic_agents <= case.maximum_agents,
        "conflicts": (
            [item.model_dump(mode="json") for item in result.synthesis.conflicts]
            if result.synthesis
            else []
        ),
        "error": result.error,
        "raw_result": result.model_dump(mode="json"),
        "quality_boundary_pass": (
            result.status in case.allowed_statuses
            and result.error is None
            and dynamic_agents <= case.maximum_agents
            if topology is CollaborationTopology.ADAPTIVE
            else result.status in case.allowed_statuses and result.error is None
        ),
    }


async def run_catalog(
    service: AdaptiveCollaborationService,
    cases: list[M3EvaluationCase],
    *,
    concurrency: int = 2,
    topologies: list[CollaborationTopology] | None = None,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    selected_topologies = topologies or [CollaborationTopology.ADAPTIVE]

    async def guarded(case: M3EvaluationCase, topology: CollaborationTopology):
        async with semaphore:
            try:
                return await run_case(service, case, topology=topology)
            except Exception as exc:  # noqa: BLE001 - preserve per-case failure
                return {
                    "case_id": case.case_id,
                    "topology": topology.value,
                    "error": type(exc).__name__,
                    "status_acceptable": False,
                    "mode_acceptable": False,
                    "agent_count_acceptable": False,
                }

    return await asyncio.gather(
        *(guarded(case, topology) for case in cases for topology in selected_topologies)
    )


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in rows),
        encoding="utf-8",
    )


__all__ = ["run_case", "run_catalog", "write_jsonl"]
