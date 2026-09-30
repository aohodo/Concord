"""One-pass, concurrency-bounded evaluator for the M2 control loop."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from core.adaptive_resolution import AdaptiveResolutionService

from .catalog import M2ScenarioDefinition, build_resolution_context, seed_scenario


class M2EvaluationResult(BaseModel):
    scenario_id: str
    case_id: str
    domain: str
    passed: bool
    status: str
    latency_ms: float
    model_calls: int
    tool_calls: int
    decision_events: list[str] = Field(default_factory=list)
    verified_targets: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    response: str = ""
    raw_result: dict[str, Any] = Field(default_factory=dict)
    humanity_judgment: dict[str, Any] | None = None


async def _evaluate_one(
    scenario: M2ScenarioDefinition,
    *,
    service: AdaptiveResolutionService,
    tool_runtime: Any,
    semaphore: asyncio.Semaphore,
    run_id: str,
    runtime_index: int,
) -> M2EvaluationResult:
    case_id = f"case-{run_id}-{runtime_index:03d}"
    async with semaphore:
        await seed_scenario(scenario, case_id=case_id, tool_runtime=tool_runtime)
        started = time.perf_counter()
        try:
            result = await service.resolve(
                resolution_context=build_resolution_context(scenario, case_id=case_id),
                permissions=scenario.permissions,
            )
        except Exception as exc:  # noqa: BLE001 - evaluator preserves raw failure
            return M2EvaluationResult(
                scenario_id=scenario.scenario_id,
                case_id=case_id,
                domain=scenario.domain,
                passed=False,
                status="exception",
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                model_calls=0,
                tool_calls=0,
                failures=[f"EXCEPTION:{type(exc).__name__}:{exc}"],
            )

    raw = result.model_dump(mode="json")
    events = [item.get("event", "") for item in result.decision_events]
    verified_targets = sorted(
        {
            str(item.get("verification_target"))
            for item in result.tool_history
            if item.get("automatic_verification")
            and item.get("expectation_matched") is True
        }
    )
    failures: list[str] = []
    if result.status not in scenario.expected.allowed_statuses:
        failures.append(f"UNEXPECTED_STATUS:{result.status.value}")
    missing_events = scenario.expected.required_decision_events - set(events)
    if missing_events:
        failures.append(f"MISSING_EVENTS:{','.join(sorted(missing_events))}")
    missing_targets = scenario.expected.required_verification_targets - set(
        verified_targets
    )
    if missing_targets:
        failures.append(f"MISSING_VERIFICATION:{','.join(sorted(missing_targets))}")
    if result.cycles > scenario.expected.max_model_calls:
        failures.append(
            f"MODEL_BUDGET_EXCEEDED:{result.cycles}>{scenario.expected.max_model_calls}"
        )
    return M2EvaluationResult(
        scenario_id=scenario.scenario_id,
        case_id=case_id,
        domain=scenario.domain,
        passed=not failures,
        status=result.status.value,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        model_calls=result.cycles,
        tool_calls=len(result.tool_history),
        decision_events=events,
        verified_targets=verified_targets,
        failures=failures,
        response=result.response,
        raw_result=raw,
    )


async def evaluate_scenarios(
    scenarios: list[M2ScenarioDefinition],
    *,
    service: AdaptiveResolutionService,
    tool_runtime: Any,
    concurrency: int = 4,
    run_id: str | None = None,
) -> list[M2EvaluationResult]:
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 16)))
    resolved_run_id = run_id or uuid4().hex[:10]
    return await asyncio.gather(
        *(
            _evaluate_one(
                scenario,
                service=service,
                tool_runtime=tool_runtime,
                semaphore=semaphore,
                run_id=resolved_run_id,
                runtime_index=index,
            )
            for index, scenario in enumerate(scenarios, start=1)
        )
    )
