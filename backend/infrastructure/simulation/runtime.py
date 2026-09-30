"""Stateful production-like simulation primitives for M2 tool evaluation."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ScenarioCondition(BaseModel):
    path: str
    operator: Literal["eq", "ne", "in", "exists"] = "eq"
    value: Any = None
    source: Literal["visible", "hidden", "arguments"] = "visible"


class ScenarioEffect(BaseModel):
    path: str
    operation: Literal["set", "increment", "append", "delete"] = "set"
    value: Any = None
    value_from_argument: str | None = None
    target: Literal["visible", "hidden"] = "visible"


class ScenarioActionRule(BaseModel):
    action_id: str
    description: str
    preconditions: list[ScenarioCondition] = Field(default_factory=list)
    effects: list[ScenarioEffect] = Field(default_factory=list)
    success_observation: str
    failed_observation: str = "action preconditions were not satisfied"
    verification_targets: list[str] = Field(default_factory=list)
    delay_seconds: float = Field(default=0.0, ge=0.0)


class ScenarioObservationRule(BaseModel):
    """A deterministic sequence of incomplete, stale, or noisy observations."""

    path: str
    values: list[Any]
    reliabilities: list[float] = Field(default_factory=list)
    source: str = "simulated_provider"
    stale_indices: set[int] = Field(default_factory=set)
    repeat_last: bool = True


class ScenarioCase(BaseModel):
    case_id: str
    visible_state: dict[str, Any] = Field(default_factory=dict)
    hidden_state: dict[str, Any] = Field(default_factory=dict)
    action_rules: dict[str, ScenarioActionRule] = Field(default_factory=dict)
    pending_events: list[dict[str, Any]] = Field(default_factory=list)
    observation_rules: dict[str, ScenarioObservationRule] = Field(default_factory=dict)
    observation_counts: dict[str, int] = Field(default_factory=dict)
    version: int = 0


class FaultKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    SERVICE_UNAVAILABLE = "service_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    PARTIAL_SUCCESS = "partial_success"
    NETWORK_LOST_AFTER_COMMIT = "network_lost_after_commit"
    CONFLICT = "conflict"


class SimulatedFault(BaseModel):
    kind: FaultKind
    phase: Literal["before", "after"] = "before"
    message: str | None = None
    retryable: bool = True


class FaultPlan:
    """Deterministic fault queue; a fault is consumed exactly once."""

    def __init__(self) -> None:
        self._faults: dict[tuple[str, str], list[SimulatedFault]] = {}
        self._lock = asyncio.Lock()

    async def add(self, case_id: str, tool_id: str, fault: SimulatedFault) -> None:
        async with self._lock:
            self._faults.setdefault((case_id, tool_id), []).append(fault)

    async def take(
        self,
        case_id: str,
        tool_id: str,
        phase: Literal["before", "after"],
    ) -> SimulatedFault | None:
        async with self._lock:
            items = self._faults.get((case_id, tool_id), [])
            for index, item in enumerate(items):
                if item.phase == phase:
                    return items.pop(index)
        return None


class IdempotencyRecord(BaseModel):
    tool_id: str
    key: str
    canonical_arguments: str
    result: dict[str, Any]


class IdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def canonicalize(arguments: dict[str, Any]) -> str:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)

    async def get(self, case_id: str, tool_id: str, key: str) -> IdempotencyRecord | None:
        async with self._lock:
            record = self._records.get((case_id, tool_id, key))
            return record.model_copy(deep=True) if record else None

    async def put(
        self,
        case_id: str,
        tool_id: str,
        key: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        async with self._lock:
            self._records[(case_id, tool_id, key)] = IdempotencyRecord(
                tool_id=tool_id,
                key=key,
                canonical_arguments=self.canonicalize(arguments),
                result=copy.deepcopy(result),
            )


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    case_id: str
    trace_id: str
    invocation_id: str
    actor_id: str
    tenant_id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission_decision: str
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    result_status: str
    error_code: str | None = None
    latency_ms: float = 0.0
    synthetic: bool = True


class AuditLog:
    """Append-only local audit sink used as the authoritative action record."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).resolve() if path else None
        self._events: list[AuditEvent] = self._load_sync()
        self._lock = asyncio.Lock()

    def _load_sync(self) -> list[AuditEvent]:
        if self._path is None or not self._path.exists():
            return []
        events: list[AuditEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(AuditEvent.model_validate_json(line))
            except ValueError:
                continue
        return events

    async def append(self, event: AuditEvent) -> None:
        async with self._lock:
            self._events.append(event.model_copy(deep=True))
            if self._path is not None:
                await asyncio.to_thread(self._append_sync, event)

    def _append_sync(self, event: AuditEvent) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    async def list(self, case_id: str | None = None) -> list[AuditEvent]:
        async with self._lock:
            items = self._events if case_id is None else [e for e in self._events if e.case_id == case_id]
            return [item.model_copy(deep=True) for item in items]


class VirtualClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime.now(UTC)

    @property
    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now


class ScenarioRuntime:
    """Resettable case state with hidden truth and declarative action rules."""

    def __init__(self, clock: VirtualClock | None = None) -> None:
        self._clock = clock or VirtualClock()
        self._cases: dict[str, ScenarioCase] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def create_case(
        self,
        case_id: str,
        *,
        visible_state: dict[str, Any] | None = None,
        hidden_state: dict[str, Any] | None = None,
        action_rules: list[ScenarioActionRule] | None = None,
        observation_rules: list[ScenarioObservationRule] | None = None,
        replace: bool = False,
    ) -> ScenarioCase:
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            if case_id in self._cases and not replace:
                raise ValueError(f"scenario case already exists: {case_id}")
            case = ScenarioCase(
                case_id=case_id,
                visible_state=copy.deepcopy(visible_state or {}),
                hidden_state=copy.deepcopy(hidden_state or {}),
                action_rules={item.action_id: item for item in action_rules or []},
                observation_rules={item.path: item for item in observation_rules or []},
            )
            self._cases[case_id] = case
            return case.model_copy(deep=True)

    def has_case(self, case_id: str) -> bool:
        return case_id in self._cases

    def has_observable_path(self, case_id: str, path: str) -> bool:
        case = self._cases.get(case_id)
        if case is None:
            return False
        if path in case.observation_rules:
            return True
        marker = object()
        return _get_path(case.visible_state, path, default=marker) is not marker

    async def snapshot(self, case_id: str, *, include_hidden: bool = False) -> dict[str, Any]:
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            case = self._require_case(case_id)
            self._apply_due_events(case)
            snapshot = {
                "case_id": case.case_id,
                "visible_state": copy.deepcopy(case.visible_state),
                "version": case.version,
                "pending_event_count": len(case.pending_events),
            }
            if include_hidden:
                snapshot["hidden_state"] = copy.deepcopy(case.hidden_state)
            return snapshot

    async def observe(self, case_id: str, path: str = "") -> Any:
        result = await self.observe_with_metadata(case_id, path)
        return result["value"]

    async def observe_with_metadata(
        self, case_id: str, path: str = ""
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            case = self._require_case(case_id)
            self._apply_due_events(case)
            rule = case.observation_rules.get(path)
            if rule is None:
                value = copy.deepcopy(_get_path(case.visible_state, path)) if path else copy.deepcopy(case.visible_state)
                return {
                    "value": value,
                    "reliability": 1.0,
                    "source": f"scenario:{case_id}",
                    "stale": False,
                    "observation_index": 0,
                }
            count = case.observation_counts.get(path, 0)
            if not rule.values:
                raise ValueError(f"observation rule has no values: {path}")
            if count >= len(rule.values) and not rule.repeat_last:
                raise RuntimeError(f"observation sequence exhausted: {path}")
            index = min(count, len(rule.values) - 1)
            case.observation_counts[path] = count + 1
            reliability = (
                rule.reliabilities[min(index, len(rule.reliabilities) - 1)]
                if rule.reliabilities
                else 1.0
            )
            return {
                "value": copy.deepcopy(rule.values[index]),
                "reliability": reliability,
                "source": rule.source,
                "stale": index in rule.stale_indices,
                "observation_index": index,
            }
        snapshot = await self.snapshot(case_id)
        state = snapshot["visible_state"]
        return copy.deepcopy(_get_path(state, path)) if path else state

    async def list_actions(self, case_id: str) -> list[dict[str, Any]]:
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            case = self._require_case(case_id)
            return [
                {"action_id": rule.action_id, "description": rule.description}
                for rule in sorted(case.action_rules.values(), key=lambda item: item.action_id)
            ]

    async def execute_action(
        self,
        case_id: str,
        action_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = arguments or {}
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            case = self._require_case(case_id)
            self._apply_due_events(case)
            rule = case.action_rules.get(action_id)
            if rule is None:
                return {
                    "status": "failed",
                    "changed": False,
                    "error_code": "UNKNOWN_SIMULATED_ACTION",
                    "observation": f"unknown simulated action: {action_id}",
                }
            if not all(self._condition_matches(case, item, args) for item in rule.preconditions):
                return {
                    "status": "failed",
                    "changed": False,
                    "error_code": "PRECONDITION_FAILED",
                    "observation": rule.failed_observation,
                }
            if rule.delay_seconds > 0:
                case.pending_events.append(
                    {
                        "due_at": (self._clock.now + timedelta(seconds=rule.delay_seconds)).isoformat(),
                        "effects": [item.model_dump(mode="json") for item in rule.effects],
                        "arguments": copy.deepcopy(args),
                    }
                )
                return {
                    "status": "pending",
                    "changed": False,
                    "observation": rule.success_observation,
                    "verification_targets": rule.verification_targets,
                    "verification_requests": self._verification_requests(rule, args),
                }
            self._apply_effects(case, rule.effects, args)
            case.version += 1
            return {
                "status": "succeeded",
                "changed": bool(rule.effects),
                "observation": rule.success_observation,
                "verification_targets": rule.verification_targets,
                "verification_requests": self._verification_requests(rule, args),
            }

    @staticmethod
    def _verification_requests(
        rule: ScenarioActionRule, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Translate declared effects into executable verification contracts.

        Only deterministic visible assignments get a machine-checkable expected
        value. Other effects still require an independent observation and are
        assessed by the planner at the next decision event.
        """

        requests: list[dict[str, Any]] = []
        for target in rule.verification_targets:
            expectation: dict[str, Any] | None = None
            matching_effects = [
                effect
                for effect in rule.effects
                if effect.target == "visible" and effect.path == target
            ]
            if matching_effects:
                effect = matching_effects[-1]
                if effect.operation == "set":
                    value = (
                        _get_path(arguments, effect.value_from_argument)
                        if effect.value_from_argument
                        else copy.deepcopy(effect.value)
                    )
                    expectation = {
                        "result_path": "data.value",
                        "operator": "eq",
                        "value": value,
                    }
            request: dict[str, Any] = {
                "target": target,
                "required_capabilities": [
                    "scenario_observation",
                    "state_inspection",
                ],
                "arguments": {"path": target},
                "expected_observation": f"independently observe {target}",
            }
            if expectation is not None:
                request["expectation"] = expectation
            requests.append(request)
        return requests

    async def advance_time(self, seconds: float) -> None:
        self._clock.advance(seconds)
        for case_id in list(self._cases):
            lock = self._locks.setdefault(case_id, asyncio.Lock())
            async with lock:
                self._apply_due_events(self._cases[case_id])

    def _apply_due_events(self, case: ScenarioCase) -> None:
        remaining: list[dict[str, Any]] = []
        for event in case.pending_events:
            if datetime.fromisoformat(event["due_at"]) <= self._clock.now:
                effects = [ScenarioEffect.model_validate(item) for item in event["effects"]]
                self._apply_effects(case, effects, event.get("arguments", {}))
                case.version += 1
            else:
                remaining.append(event)
        case.pending_events = remaining

    @staticmethod
    def _condition_matches(
        case: ScenarioCase,
        condition: ScenarioCondition,
        arguments: dict[str, Any],
    ) -> bool:
        source: dict[str, Any]
        if condition.source == "hidden":
            source = case.hidden_state
        elif condition.source == "arguments":
            source = arguments
        else:
            source = case.visible_state
        marker = object()
        actual = _get_path(source, condition.path, default=marker)
        if condition.operator == "exists":
            return (actual is not marker) is bool(condition.value if condition.value is not None else True)
        if actual is marker:
            return False
        if condition.operator == "eq":
            return actual == condition.value
        if condition.operator == "ne":
            return actual != condition.value
        if condition.operator == "in":
            return actual in condition.value
        return False

    @staticmethod
    def _apply_effects(
        case: ScenarioCase,
        effects: list[ScenarioEffect],
        arguments: dict[str, Any],
    ) -> None:
        for effect in effects:
            target = case.hidden_state if effect.target == "hidden" else case.visible_state
            value = (
                _get_path(arguments, effect.value_from_argument)
                if effect.value_from_argument
                else copy.deepcopy(effect.value)
            )
            _apply_effect(target, effect.path, effect.operation, value)

    def _require_case(self, case_id: str) -> ScenarioCase:
        case = self._cases.get(case_id)
        if case is None:
            raise KeyError(f"unknown scenario case: {case_id}")
        return case


def _get_path(data: dict[str, Any], path: str, *, default: Any = None) -> Any:
    if not path:
        return data
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _apply_effect(data: dict[str, Any], path: str, operation: str, value: Any) -> None:
    parts = [item for item in path.split(".") if item]
    if not parts:
        raise ValueError("effect path cannot be empty")
    parent: dict[str, Any] = data
    for part in parts[:-1]:
        child = parent.setdefault(part, {})
        if not isinstance(child, dict):
            raise TypeError(f"effect path crosses non-object field: {path}")
        parent = child
    key = parts[-1]
    if operation == "set":
        parent[key] = value
    elif operation == "increment":
        parent[key] = parent.get(key, 0) + value
    elif operation == "append":
        parent.setdefault(key, []).append(value)
    elif operation == "delete":
        parent.pop(key, None)
    else:
        raise ValueError(f"unsupported effect operation: {operation}")
