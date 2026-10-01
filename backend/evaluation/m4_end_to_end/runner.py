"""Concurrency-bounded evaluator that only enters Concord through HTTP APIs."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from .catalog import M4Episode

TERMINAL_PHASES = {
    "resolved",
    "waiting_for_user",
    "waiting_external",
    "paused",
    "cancelled",
    "timed_out",
    "error",
}
TERMINAL_CASE_STATUSES = {
    "aligning_goal",
    "reconstructing_situation",
    "assessing_evidence",
    "seeking_information",
    "human_required",
    "evidence_required",
    "failed",
    "cancelled",
    "exhausted",
    "formulation_error",
}


class M4TurnRun(BaseModel):
    turn_index: int
    raw_user_input: str
    latency_ms: float
    first_progress_ms: float | None = None
    status_code: int
    response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class M4EpisodeRun(BaseModel):
    episode_id: str
    case_id: str
    domain: str
    user_condition: str
    variant: str
    passed: bool
    final_phase: str
    final_status: str
    latency_ms: float
    m1_model_calls: int = 0
    m2_model_calls: int = 0
    m3_model_calls: int = 0
    tool_calls: int = 0
    failures: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
    turns: list[M4TurnRun] = Field(default_factory=list)
    case_snapshot: dict[str, Any] = Field(default_factory=dict)
    environment_snapshot: dict[str, Any] = Field(default_factory=dict)
    tool_trace: dict[str, Any] = Field(default_factory=dict)


def _get_path(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _criteria_satisfied(visible: dict[str, Any], criteria: list[str]) -> bool:
    for item in criteria:
        if "=" not in item:
            continue
        path, raw_expected = item.split("=", 1)
        actual = _get_path(visible, path.strip())
        expected: Any = raw_expected.strip()
        if expected.casefold() == "true":
            expected = True
        elif expected.casefold() == "false":
            expected = False
        elif expected.isdigit():
            expected = int(expected)
        if actual != expected:
            return False
    return True


async def _seed_episode(client: httpx.AsyncClient, episode: M4Episode) -> None:
    response = await client.post(
        "/runtime/cases",
        json={
            "case_id": episode.case_id,
            "visible_state": episode.visible_state,
            "hidden_state": episode.hidden_state,
            "action_rules": [item.model_dump(mode="json") for item in episode.action_rules],
            "observation_rules": [
                item.model_dump(mode="json") for item in episode.observation_rules
            ],
            "replace": True,
        },
    )
    response.raise_for_status()
    for fault in episode.faults:
        injected = await client.post(
            "/runtime/faults",
            json={"case_id": episode.case_id, **fault.model_dump(mode="json")},
        )
        injected.raise_for_status()


async def _poll_case(
    client: httpx.AsyncClient,
    episode: M4Episode,
    *,
    timeout_s: float,
    additional_terminal_statuses: set[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    latest: dict[str, Any] = {}
    terminal_statuses = TERMINAL_CASE_STATUSES | (additional_terminal_statuses or set())
    while time.monotonic() < deadline:
        response = await client.get(
            f"/cases/{episode.case_id}",
            params={"user_id": episode.user_id, "tenant_id": "local"},
        )
        if response.status_code == 200:
            latest = response.json()
            phase = str(latest.get("phase", ""))
            status = str(latest.get("status", ""))
            terminal = phase in TERMINAL_PHASES or status in terminal_statuses
            if terminal:
                jobs_response = await client.get(
                    f"/cases/{episode.case_id}/jobs",
                    params={"user_id": episode.user_id, "tenant_id": "local"},
                )
                if jobs_response.status_code == 200:
                    jobs = jobs_response.json().get("jobs", [])
                    durable_work_pending = any(
                        item.get("status") in {"pending", "running", "waiting"}
                        for item in jobs
                    )
                    if not durable_work_pending:
                        return latest
        await asyncio.sleep(0.25)
    return latest


async def _event_sequence(client: httpx.AsyncClient, episode: M4Episode) -> int:
    """Return the last visible Case event without treating a new Case as an error."""

    response = await client.get(
        f"/cases/{episode.case_id}",
        params={"user_id": episode.user_id, "tenant_id": "local"},
    )
    if response.status_code != 200:
        return 0
    events = response.json().get("events", [])
    return max((int(item.get("sequence", 0)) for item in events), default=0)


async def _post_chat_with_progress(
    client: httpx.AsyncClient,
    episode: M4Episode,
    payload: dict[str, Any],
) -> tuple[httpx.Response, float | None]:
    """Measure backend-visible acknowledgement while the synchronous POST runs."""

    prior_sequence = await _event_sequence(client, episode)
    started = time.perf_counter()
    request = asyncio.create_task(client.post("/chat", json=payload))
    first_progress_ms: float | None = None
    while not request.done():
        try:
            response = await client.get(
                f"/cases/{episode.case_id}",
                params={"user_id": episode.user_id, "tenant_id": "local"},
            )
            if response.status_code == 200:
                events = response.json().get("events", [])
                if any(
                    int(item.get("sequence", 0)) > prior_sequence
                    and item.get("event_type")
                    in {"user_turn_received", "case_progress_heartbeat"}
                    for item in events
                ):
                    first_progress_ms = round(
                        (time.perf_counter() - started) * 1000,
                        3,
                    )
                    break
        except httpx.HTTPError:
            # The POST result remains authoritative; a transient console poll
            # must not turn an otherwise valid Episode into a failed run.
            pass
        await asyncio.sleep(0.05)
    return await request, first_progress_ms


async def evaluate_episode(
    episode: M4Episode,
    *,
    client: httpx.AsyncClient,
    variant: str = "v2_full",
    poll_timeout_s: float = 180.0,
) -> M4EpisodeRun:
    started = time.perf_counter()
    turns: list[M4TurnRun] = []
    failures: list[str] = []
    coverage_notes: list[str] = []
    try:
        await _seed_episode(client, episode)
        for index, turn in enumerate(episode.turns, start=1):
            turn_started = time.perf_counter()
            response, first_progress_ms = await _post_chat_with_progress(
                client,
                episode,
                {
                    "message": turn.message,
                    "user_id": episode.user_id,
                    "conv_id": episode.conv_id,
                    "case_id": episode.case_id,
                    "tenant_id": "local",
                    "evaluation_variant": variant,
                    "evaluation_context": episode.evaluation_context(),
                },
            )
            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {"raw_text": response.text[:2000]}
            turns.append(
                M4TurnRun(
                    turn_index=index,
                    raw_user_input=turn.message,
                    latency_ms=round((time.perf_counter() - turn_started) * 1000, 3),
                    first_progress_ms=first_progress_ms,
                    status_code=response.status_code,
                    response=body if response.status_code < 400 else {},
                    error=None if response.status_code < 400 else str(body),
                )
            )
            if response.status_code >= 400:
                failures.append(f"CHAT_HTTP_{response.status_code}:TURN_{index}")
                break
            if turn.wait_for_async_completion:
                settled = await _poll_case(
                    client,
                    episode,
                    timeout_s=poll_timeout_s,
                    additional_terminal_statuses=(
                        {"collaboration_required"} if variant == "v2_m2_only" else None
                    ),
                )
                if settled.get("phase") == "resolved":
                    break
        case_snapshot = await _poll_case(
            client,
            episode,
            timeout_s=poll_timeout_s,
            additional_terminal_statuses=(
                {"collaboration_required"} if variant == "v2_m2_only" else None
            ),
        )
        environment_response = await client.get(f"/runtime/cases/{episode.case_id}")
        environment = (
            environment_response.json() if environment_response.status_code == 200 else {}
        )
        trace_response = await client.get(
            f"/trace/tool/{episode.case_id}",
            params={"user_id": episode.user_id, "tenant_id": "local"},
        )
        tool_trace = trace_response.json() if trace_response.status_code == 200 else {}
    except Exception as exc:  # noqa: BLE001 - evaluator preserves raw failure
        failures.append(f"EXCEPTION:{type(exc).__name__}:{exc}")
        case_snapshot = {}
        environment = {}
        tool_trace = {}

    events = case_snapshot.get("events", [])
    event_types = [str(item.get("event_type", "")) for item in events]
    final_phase = str(case_snapshot.get("phase", "missing"))
    final_status = str(case_snapshot.get("status", "missing"))
    if "m1_turn_completed" not in event_types:
        failures.append("M1_BYPASSED_OR_UNOBSERVABLE")
    if episode.expected.require_m2 and not any(
        item in {"m2_run_completed", "m2_resumed_after_collaboration"}
        for item in event_types
    ):
        failures.append("M2_NOT_REACHED")
    if (
        episode.expected.require_m3
        and variant != "v2_m2_only"
        and "m3_started" not in event_types
    ):
        coverage_notes.append("M3_COVERAGE_NOT_REACHED")
    if case_snapshot and case_snapshot.get("case_id") != episode.case_id:
        failures.append("CASE_ID_DRIFT")

    visible = environment.get("visible_state", {})
    criteria_met = _criteria_satisfied(visible, episode.expected.success_criteria)
    if final_status == "resolved" and not criteria_met:
        failures.append("UNVERIFIED_SUCCESS_CLAIM")
    if "resolved" in episode.expected.allowed_case_statuses and not criteria_met:
        failures.append("GOAL_NOT_VERIFIED")
    if (
        final_status not in episode.expected.allowed_case_statuses
        and not (criteria_met and final_phase == "resolved")
    ):
        failures.append(f"UNEXPECTED_FINAL_STATUS:{final_status}")

    m1_calls = sum(int(item.response.get("m1_model_calls", 0)) for item in turns)
    m2_calls = sum(int(item.response.get("m2_model_calls", 0)) for item in turns)
    m3_metrics = case_snapshot.get("m3", {}).get("metrics", {})
    m3_calls = int(m3_metrics.get("model_calls", 0))
    tool_events = tool_trace.get("trace", {}).get("events", [])
    return M4EpisodeRun(
        episode_id=episode.episode_id,
        case_id=episode.case_id,
        domain=episode.domain,
        user_condition=episode.user_condition,
        variant=variant,
        passed=not failures,
        final_phase=final_phase,
        final_status=final_status,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        m1_model_calls=m1_calls,
        m2_model_calls=m2_calls,
        m3_model_calls=m3_calls,
        tool_calls=len(tool_events),
        failures=failures,
        coverage_notes=coverage_notes,
        turns=turns,
        case_snapshot=case_snapshot,
        environment_snapshot=environment,
        tool_trace=tool_trace,
    )


async def evaluate_episodes(
    episodes: list[M4Episode],
    *,
    base_url: str,
    variant: str = "v2_full",
    concurrency: int = 4,
    request_timeout_s: float = 600.0,
    poll_timeout_s: float = 180.0,
    run_id: str | None = None,
) -> list[M4EpisodeRun]:
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 12)))
    timeout = httpx.Timeout(request_timeout_s)
    resolved_run_id = run_id or uuid4().hex[:10]
    materialized = [
        item.model_copy(
            update={
                # Scenario labels stay evaluator-side. Runtime identifiers are
                # deliberately opaque so the model cannot infer the answer
                # from names such as permission_boundary or stale_credential.
                "case_id": f"case-{resolved_run_id}-{index:03d}",
                "conv_id": f"conv-{resolved_run_id}-{index:03d}",
                "user_id": f"user-{resolved_run_id}-{index:03d}",
            },
            deep=True,
        )
        for index, item in enumerate(episodes, start=1)
    ]
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:

        async def guarded(episode: M4Episode) -> M4EpisodeRun:
            async with semaphore:
                return await evaluate_episode(
                    episode,
                    client=client,
                    variant=variant,
                    poll_timeout_s=poll_timeout_s,
                )

        return await asyncio.gather(*(guarded(item) for item in materialized))


__all__ = ["M4EpisodeRun", "M4TurnRun", "evaluate_episode", "evaluate_episodes"]
