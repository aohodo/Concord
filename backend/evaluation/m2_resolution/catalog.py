"""Data contracts and loading for heterogeneous M2 evaluation scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.adaptive_resolution import ResolutionStatus
from infrastructure.simulation import (
    FaultKind,
    ScenarioActionRule,
    ScenarioObservationRule,
    SimulatedFault,
)

_DEFAULT_CATALOG = Path(__file__).with_name("data") / "m2_resolution_cases.json"


class M2FaultSpec(BaseModel):
    tool_id: str
    kind: FaultKind
    phase: str = "before"
    message: str | None = None
    retryable: bool = True


class M2ExpectedResult(BaseModel):
    allowed_statuses: set[ResolutionStatus]
    required_decision_events: set[str] = Field(default_factory=set)
    required_verification_targets: set[str] = Field(default_factory=set)
    max_model_calls: int = Field(default=4, ge=1, le=12)


class M2ScenarioDefinition(BaseModel):
    scenario_id: str
    domain: str
    title: str
    user_report: str
    user_hypothesis: str | None = None
    urgency: str = "normal"
    explanation_preference: str = "on_demand"
    visible_state: dict[str, Any]
    hidden_state: dict[str, Any] = Field(default_factory=dict)
    action_rules: list[ScenarioActionRule] = Field(default_factory=list)
    observation_rules: list[ScenarioObservationRule] = Field(default_factory=list)
    faults: list[M2FaultSpec] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=lambda: ["simulation:act"])
    goal: str
    success_criteria: list[str]
    tags: set[str] = Field(default_factory=set)
    expected: M2ExpectedResult


def load_scenario_catalog(path: str | Path | None = None) -> list[M2ScenarioDefinition]:
    source = Path(path).resolve() if path else _DEFAULT_CATALOG
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("M2 scenario catalog must be a JSON array")
    cases = [M2ScenarioDefinition.model_validate(item) for item in payload]
    ids = [item.scenario_id for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("M2 scenario catalog contains duplicate scenario_id values")
    return cases


def build_resolution_context(
    scenario: M2ScenarioDefinition,
    *,
    case_id: str,
) -> dict[str, Any]:
    resolved_evidence = [
        {
            "key": "user_report",
            "value": scenario.user_report,
            "epistemic_status": "reported_observation",
            "source": "user",
        }
    ]
    if scenario.user_hypothesis:
        resolved_evidence.append(
            {
                "key": "user_hypothesis",
                "value": scenario.user_hypothesis,
                "epistemic_status": "user_hypothesis",
                "source": "user",
            }
        )
    return {
        "handoff_contract": {"name": "concord_m1_to_m2", "version": "1.0"},
        "case_id": case_id,
        # Evaluator labels (title/tags/expected outcome) deliberately stay out
        # of the agent context; otherwise the benchmark leaks its diagnosis.
        "scenario": {"domain": scenario.domain},
        "goal": {
            "explicit_goal": scenario.goal,
            "current_outcome": scenario.user_report,
            "success_criteria": [
                {"value": item} for item in scenario.success_criteria
            ],
        },
        "evidence_handoff": {
            "open": [],
            "resolved": resolved_evidence,
            "unavailable": [],
            "deferred": [],
        },
        "user_state_snapshot": {
            "urgency": scenario.urgency,
            "explanation_preference": scenario.explanation_preference,
        },
        "action_constraints": {
            "case_entry_allowed": True,
            "evidence_complete": False,
            "pre_resolution_whitelist": {
                "tool_categories": ["observe", "verify", "handoff"],
                "operation_modes": ["read_only"],
            },
            "conditional_operation_modes": {
                "simulated_write": [
                    "runtime_authorized",
                    "resettable_case_environment",
                    "expected_observation_declared",
                ]
            },
            "restricted_operation_modes": ["production_write"],
            "evidence_constraints": [],
            "runtime_policy_remains_authoritative": True,
        },
        "provisional_resolution_policy": {
            "explanation_mode": scenario.explanation_preference,
            "forbidden_retries": [],
        },
    }


async def seed_scenario(
    scenario: M2ScenarioDefinition,
    *,
    case_id: str,
    tool_runtime: Any,
) -> None:
    await tool_runtime.scenarios.create_case(
        case_id,
        visible_state=scenario.visible_state,
        hidden_state=scenario.hidden_state,
        action_rules=scenario.action_rules,
        observation_rules=scenario.observation_rules,
    )
    for fault in scenario.faults:
        await tool_runtime.fault_plan.add(
            case_id,
            fault.tool_id,
            SimulatedFault(
                kind=fault.kind,
                phase=fault.phase,
                message=fault.message,
                retryable=fault.retryable,
            ),
        )
