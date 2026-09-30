"""Longitudinal M2 evaluation with interleaved user evidence bursts."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from core.adaptive_resolution import AdaptiveResolutionService, ResolutionStatus

from .catalog import (
    M2ScenarioDefinition,
    build_resolution_context,
    load_scenario_catalog,
    seed_scenario,
)

_DEFAULT_TRAJECTORIES = Path(__file__).with_name("data") / "m2_longitudinal_cases.json"


class UserEvidenceBurst(BaseModel):
    after_episode: int = Field(ge=1)
    raw_messages: list[str]
    interarrival_ms: list[int] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    user_state_update: dict[str, Any] = Field(default_factory=dict)
    goal_update: str | None = None
    success_criteria_update: list[str] = Field(default_factory=list)
    permissions_update: list[str] | None = None
    image_evidence: dict[str, Any] | None = None


class LongitudinalExpectedResult(BaseModel):
    allowed_statuses: set[ResolutionStatus]
    required_decision_events: set[str] = Field(
        default_factory=lambda: {"user_evidence_updated"}
    )
    max_total_model_calls: int = Field(default=6, ge=1)
    require_single_bootstrap: bool = True
    require_user_progress: bool = True


class LongitudinalScenarioDefinition(BaseModel):
    trajectory_id: str
    base_scenario_id: str
    initial_user_report: str | None = None
    initial_user_hypothesis: str | None = None
    episode_decision_budgets: list[int] = Field(default_factory=lambda: [1, 4])
    user_bursts: list[UserEvidenceBurst]
    expected: LongitudinalExpectedResult


class LongitudinalEpisodeResult(BaseModel):
    episode: int
    status: str
    episode_model_calls: int
    total_model_calls: int
    injected_messages: list[str] = Field(default_factory=list)
    response: str


class LongitudinalEvaluationResult(BaseModel):
    trajectory_id: str
    case_id: str
    passed: bool
    status: str
    episodes: list[LongitudinalEpisodeResult]
    total_model_calls: int
    bootstrap_tool_calls: int
    decision_events: list[str]
    progress_stages: list[str]
    failures: list[str] = Field(default_factory=list)
    raw_result: dict[str, Any] = Field(default_factory=dict)
    humanity_judgment: dict[str, Any] | None = None


def load_longitudinal_catalog(
    path: str | Path | None = None,
) -> list[LongitudinalScenarioDefinition]:
    source = Path(path).resolve() if path else _DEFAULT_TRAJECTORIES
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("M2 longitudinal catalog must be a JSON array")
    trajectories = [
        LongitudinalScenarioDefinition.model_validate(item) for item in payload
    ]
    ids = [item.trajectory_id for item in trajectories]
    if len(ids) != len(set(ids)):
        raise ValueError("M2 longitudinal catalog contains duplicate trajectory_id values")
    return trajectories


def _base_cases() -> dict[str, M2ScenarioDefinition]:
    return {item.scenario_id: item for item in load_scenario_catalog()}


async def evaluate_longitudinal_scenario(
    trajectory: LongitudinalScenarioDefinition,
    *,
    service: AdaptiveResolutionService,
    tool_runtime: Any,
    run_id: str | None = None,
) -> LongitudinalEvaluationResult:
    base = _base_cases().get(trajectory.base_scenario_id)
    if base is None:
        raise ValueError(f"unknown base scenario: {trajectory.base_scenario_id}")
    scenario = base.model_copy(deep=True)
    if trajectory.initial_user_report is not None:
        scenario.user_report = trajectory.initial_user_report
    if trajectory.initial_user_hypothesis is not None:
        scenario.user_hypothesis = trajectory.initial_user_hypothesis
    suffix = run_id or uuid4().hex[:10]
    opaque_id = hashlib.sha256(
        f"{suffix}:{trajectory.trajectory_id}".encode()
    ).hexdigest()[:12]
    case_id = f"case-{suffix}-{opaque_id}"
    thread_id = f"m2-longitudinal:{case_id}"
    await seed_scenario(scenario, case_id=case_id, tool_runtime=tool_runtime)
    context = build_resolution_context(scenario, case_id=case_id)
    bursts_by_episode = {item.after_episode: item for item in trajectory.user_bursts}
    episode_results: list[LongitudinalEpisodeResult] = []
    latest = None
    active_permissions = list(scenario.permissions)

    for episode_number, budget in enumerate(
        trajectory.episode_decision_budgets, start=1
    ):
        burst = bursts_by_episode.get(episode_number - 1)
        additions: list[dict[str, Any]] = []
        user_update: dict[str, Any] = {}
        injected_messages: list[str] = []
        if burst is not None:
            injected_messages = list(burst.raw_messages)
            additions = copy.deepcopy(burst.evidence)
            additions.append(
                {
                    "key": f"user_message_burst_{episode_number - 1}",
                    "value": {
                        "messages": burst.raw_messages,
                        "interarrival_ms": burst.interarrival_ms,
                    },
                    "epistemic_status": "reported_observation",
                    "source": "user",
                }
            )
            user_update = copy.deepcopy(burst.user_state_update)
            if burst.permissions_update is not None:
                active_permissions = list(burst.permissions_update)
            if burst.image_evidence is not None:
                additions.append(
                    {
                        "key": f"image_evidence_{episode_number - 1}",
                        "value": copy.deepcopy(burst.image_evidence),
                        "epistemic_status": "unverified_visual_observation",
                        "source": "model_vision_extraction",
                    }
                )
            if burst.goal_update:
                context["goal"]["explicit_goal"] = burst.goal_update
            if burst.success_criteria_update:
                context["goal"]["success_criteria"] = [
                    {"value": item} for item in burst.success_criteria_update
                ]
        try:
            latest = await service.resolve(
                resolution_context=context,
                thread_id=thread_id,
                permissions=active_permissions,
                episode_decision_budget=budget,
                additional_evidence=additions,
                user_state_update=user_update,
            )
        except Exception as exc:  # noqa: BLE001 - preserve one Case failure
            return LongitudinalEvaluationResult(
                trajectory_id=trajectory.trajectory_id,
                case_id=case_id,
                passed=False,
                status="exception",
                episodes=episode_results,
                total_model_calls=(
                    episode_results[-1].total_model_calls if episode_results else 0
                ),
                bootstrap_tool_calls=0,
                decision_events=[],
                progress_stages=[],
                failures=[f"EXCEPTION:{type(exc).__name__}:{exc}"],
            )
        episode_results.append(
            LongitudinalEpisodeResult(
                episode=episode_number,
                status=latest.status.value,
                episode_model_calls=latest.episode_decisions,
                total_model_calls=latest.cycles,
                injected_messages=injected_messages,
                response=latest.response,
            )
        )
        remaining_bursts = any(
            item.after_episode >= episode_number for item in trajectory.user_bursts
        )
        if latest.status is ResolutionStatus.RESOLVED and not remaining_bursts:
            break
        if latest.status is ResolutionStatus.COLLABORATION_REQUIRED and not remaining_bursts:
            break

    if latest is None:
        raise ValueError("longitudinal scenario has no episode budgets")
    events = [item.get("event", "") for item in latest.decision_events]
    progress_stages = [item.stage for item in latest.progress_events]
    bootstrap_calls = sum(
        1 for item in latest.tool_history if item.get("bootstrap") is True
    )
    failures: list[str] = []
    if latest.status not in trajectory.expected.allowed_statuses:
        failures.append(f"UNEXPECTED_STATUS:{latest.status.value}")
    missing_events = trajectory.expected.required_decision_events - set(events)
    if missing_events:
        failures.append(f"MISSING_EVENTS:{','.join(sorted(missing_events))}")
    if latest.cycles > trajectory.expected.max_total_model_calls:
        failures.append(
            "MODEL_BUDGET_EXCEEDED:"
            f"{latest.cycles}>{trajectory.expected.max_total_model_calls}"
        )
    if trajectory.expected.require_single_bootstrap and bootstrap_calls != 2:
        failures.append(f"BOOTSTRAP_REPEATED_OR_MISSING:{bootstrap_calls}")
    if (
        trajectory.expected.require_user_progress
        and "evidence_update" not in progress_stages
    ):
        failures.append("MISSING_USER_PROGRESS")
    return LongitudinalEvaluationResult(
        trajectory_id=trajectory.trajectory_id,
        case_id=case_id,
        passed=not failures,
        status=latest.status.value,
        episodes=episode_results,
        total_model_calls=latest.cycles,
        bootstrap_tool_calls=bootstrap_calls,
        decision_events=events,
        progress_stages=progress_stages,
        failures=failures,
        raw_result=latest.model_dump(mode="json"),
    )


async def evaluate_longitudinal_scenarios(
    trajectories: list[LongitudinalScenarioDefinition],
    *,
    service: AdaptiveResolutionService,
    tool_runtime: Any,
    concurrency: int = 2,
) -> list[LongitudinalEvaluationResult]:
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 8)))

    async def limited(item: LongitudinalScenarioDefinition):
        async with semaphore:
            return await evaluate_longitudinal_scenario(
                item,
                service=service,
                tool_runtime=tool_runtime,
            )

    return await asyncio.gather(*(limited(item) for item in trajectories))
