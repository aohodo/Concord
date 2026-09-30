"""Longitudinal M3 episodes: revisions, collaboration, and verified learning."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.adaptive_resolution import CollaborationHandoff
from core.multi_agent_collaboration import (
    AdaptiveCollaborationService,
    AgentOutcomeFeedback,
    CollaborationTopology,
)

from .catalog import M3EvaluationCase

DEFAULT_LONGITUDINAL_CATALOG = (
    Path(__file__).with_name("data") / "m3_longitudinal_episodes.json"
)


class M3LongitudinalEpisode(BaseModel):
    episode_id: str
    description: str
    initial_case: M3EvaluationCase
    updated_handoff: CollaborationHandoff | None = None
    verified_capabilities: set[str] = Field(default_factory=set)
    verification_success: bool = True


def load_longitudinal_episodes(
    path: str | Path | None = None,
) -> list[M3LongitudinalEpisode]:
    source = Path(path) if path else DEFAULT_LONGITUDINAL_CATALOG
    return [
        M3LongitudinalEpisode.model_validate(item)
        for item in json.loads(source.read_text("utf-8"))
    ]


async def run_longitudinal_episode(
    service: AdaptiveCollaborationService,
    episode: M3LongitudinalEpisode,
) -> dict[str, Any]:
    first = await service.collaborate(
        handoff=episode.initial_case.handoff,
        topology=CollaborationTopology.ADAPTIVE,
    )
    stale_initial_discarded = None
    final = first
    if episode.updated_handoff is not None:
        stale_initial_discarded = not service.result_is_current(
            first,
            current_case_revision=episode.updated_handoff.case_revision,
        )
        final = await service.collaborate(
            handoff=episode.updated_handoff,
            topology=CollaborationTopology.ADAPTIVE,
        )

    feedback_count = 0
    for plan in final.plans:
        for assignment in plan.assignments:
            relevant = bool(
                assignment.required_capabilities & episode.verified_capabilities
            )
            await service.record_outcome(
                AgentOutcomeFeedback(
                    agent_id=assignment.agent_id,
                    successful=episode.verification_success and relevant,
                    latency_ms=next(
                        (
                            item.latency_ms
                            for item in final.contributions
                            if item.assignment_id == assignment.assignment_id
                        ),
                        0.0,
                    ),
                    collaboration_id=final.collaboration_id,
                    assignment_id=assignment.assignment_id,
                    capabilities=assignment.required_capabilities,
                    task_type=final.mode.value,
                    verified_claims=1 if relevant else 0,
                    disproven_claims=0 if relevant else 1,
                    progress_delta=1.0 if relevant else -0.5,
                    source="synthetic_episode_ground_truth",
                )
            )
            feedback_count += 1

    case = episode.initial_case
    passed = (
        final.status in case.allowed_statuses
        and len(final.agents_recruited) <= case.maximum_agents
        and final.error is None
        and (
            stale_initial_discarded is True
            if episode.updated_handoff is not None
            else stale_initial_discarded is None
        )
    )
    failure_tags = []
    if final.status not in case.allowed_statuses:
        failure_tags.append("STATUS_BOUNDARY_MISMATCH")
    if len(final.agents_recruited) > case.maximum_agents:
        failure_tags.append("UNNECESSARY_COORDINATION")
    if final.error:
        failure_tags.append("MODEL_OR_RUNTIME_FAILURE")
    if episode.updated_handoff is not None and stale_initial_discarded is not True:
        failure_tags.append("STALE_RESULT_ACCEPTED")
    if final.metrics.shared_source_groups and not (
        final.synthesis and final.synthesis.shared_source_warnings
    ):
        failure_tags.append("CORRELATED_CONSENSUS_UNMARKED")
    return {
        "episode_id": episode.episode_id,
        "description": episode.description,
        "turns": 2 if episode.updated_handoff is not None else 1,
        "initial_status": first.status.value,
        "final_status": final.status.value,
        "mode": final.mode.value,
        "agents_recruited": final.agents_recruited,
        "stale_initial_discarded": stale_initial_discarded,
        "feedback_events": feedback_count,
        "model_calls": first.metrics.model_calls + (
            final.metrics.model_calls if final is not first else 0
        ),
        "input_tokens": (
            (first.metrics.input_tokens or 0)
            + (final.metrics.input_tokens or 0 if final is not first else 0)
            if first.metrics.tokens_available
            else None
        ),
        "output_tokens": (
            (first.metrics.output_tokens or 0)
            + (final.metrics.output_tokens or 0 if final is not first else 0)
            if first.metrics.tokens_available
            else None
        ),
        "latency_ms": first.metrics.wall_time_ms
        + (final.metrics.wall_time_ms if final is not first else 0),
        "measured_coordination_cost": round(
            first.metrics.measured_coordination_cost
            + (final.metrics.measured_coordination_cost if final is not first else 0),
            4,
        ),
        "shared_source_groups": final.metrics.shared_source_groups,
        "passed": passed,
        "failure_tags": failure_tags,
        "raw_final": final.model_dump(mode="json"),
    }


async def run_longitudinal_catalog(
    service: AdaptiveCollaborationService,
    episodes: list[M3LongitudinalEpisode],
    *,
    concurrency: int = 3,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(episode: M3LongitudinalEpisode) -> dict[str, Any]:
        async with semaphore:
            try:
                return await run_longitudinal_episode(service, episode)
            except Exception as exc:  # noqa: BLE001 - preserve every experiment row
                return {
                    "episode_id": episode.episode_id,
                    "error": type(exc).__name__,
                    "passed": False,
                }

    return await asyncio.gather(*(guarded(episode) for episode in episodes))


__all__ = [
    "DEFAULT_LONGITUDINAL_CATALOG",
    "M3LongitudinalEpisode",
    "load_longitudinal_episodes",
    "run_longitudinal_catalog",
    "run_longitudinal_episode",
]
