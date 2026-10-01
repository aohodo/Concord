"""Policy-controlled, audited and traceable execution of agent-selected tools."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from core.tools import (
    NullTraceSink,
    RuntimeToolProfile,
    ToolContext,
    ToolInvocation,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
    ToolStatus,
    TraceSink,
)
from infrastructure.simulation import (
    AuditEvent,
    AuditLog,
    FaultKind,
    FaultPlan,
    IdempotencyStore,
    ScenarioRuntime,
    SimulatedFault,
)

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|cookie|credential)",
    re.IGNORECASE,
)


@dataclass
class RuntimeToolStats:
    total: int = 0
    success: int = 0
    failed: int = 0
    total_latency_ms: float = 0.0
    consecutive_fails: int = 0
    circuit_state: str = "closed"
    circuit_opened_at: float | None = None

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0


class AdaptiveToolRuntime:
    """Execute stable tool contracts against read-only or simulated adapters."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        scenarios: ScenarioRuntime | None = None,
        audit_log: AuditLog | None = None,
        idempotency: IdempotencyStore | None = None,
        fault_plan: FaultPlan | None = None,
        trace_sink: TraceSink | None = None,
        circuit_failure_threshold: int = 5,
        circuit_recovery_s: float = 60.0,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.policy = policy or ToolPolicy()
        self.scenarios = scenarios or ScenarioRuntime()
        self.audit_log = audit_log or AuditLog()
        self.idempotency = idempotency or IdempotencyStore()
        self.fault_plan = fault_plan or FaultPlan()
        self.trace_sink = trace_sink or NullTraceSink()
        self._stats: dict[str, RuntimeToolStats] = {}
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_recovery_s = circuit_recovery_s

    def describe_authorized_tools(self, context: ToolContext) -> RuntimeToolProfile:
        """Return the request's real executable surface for planning and reuse.

        This is derived from registered contracts, availability callbacks and the
        same deterministic policy used by ``execute``. It therefore cannot be
        fabricated by an LLM or inferred from an empty permission list.
        """

        authorized: set[str] = set()
        capabilities: set[str] = set()
        versions: dict[str, str] = {}
        denied: dict[str, str] = {}
        for spec in self.registry.list():
            if not spec.is_available(context):
                denied[spec.tool_id] = "UNAVAILABLE"
                continue
            decision = self.policy.authorize(spec, context)
            if not decision.allowed:
                denied[spec.tool_id] = decision.code
                continue
            authorized.add(spec.tool_id)
            capabilities.update(spec.capabilities)
            versions[spec.tool_id] = spec.contract_version
        return RuntimeToolProfile(
            authorized_tool_ids=authorized,
            available_capabilities=capabilities,
            granted_permissions=set(context.permissions),
            tool_contract_versions=versions,
            denied_tools=denied,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        started = time.perf_counter()
        spec = self.registry.get(invocation.tool_id)
        if spec is None:
            return self._result(
                invocation,
                ToolStatus.FAILED,
                error_code="UNKNOWN_TOOL",
                error=f"unknown tool: {invocation.tool_id}",
            )

        span = await self.trace_sink.start_span(
            f"tool:{spec.tool_id}",
            run_type="tool",
            inputs={"arguments": invocation.arguments},
            metadata={
                "case_id": invocation.context.case_id,
                "thread_id": invocation.context.thread_id,
                "invocation_id": invocation.invocation_id,
                "category": spec.category.value,
                "operation_mode": spec.operation_mode.value,
                "synthetic": invocation.context.synthetic,
            },
            tags=["concord", "tool", spec.category.value],
            trace_id=invocation.context.trace_id,
        )
        state_before = await self._snapshot(invocation.context.case_id)
        circuit = self._stats.setdefault(spec.tool_id, RuntimeToolStats())
        if circuit.circuit_state == "open":
            if (
                circuit.circuit_opened_at is not None
                and time.monotonic() - circuit.circuit_opened_at >= self._circuit_recovery_s
            ):
                circuit.circuit_state = "half_open"
            else:
                result = self._result(
                    invocation,
                    ToolStatus.FAILED,
                    error_code="TOOL_CIRCUIT_OPEN",
                    error=f"tool circuit is open: {spec.tool_id}",
                    retryable=True,
                )
                return await self._finish(
                    invocation,
                    result,
                    started,
                    "blocked by circuit breaker",
                    state_before,
                    span,
                )

        decision = self.policy.authorize(spec, invocation.context)
        if not decision.allowed:
            result = self._result(
                invocation,
                ToolStatus.DENIED,
                error_code=decision.code,
                error=decision.reason,
            )
            return await self._finish(
                invocation,
                result,
                started,
                decision.reason,
                state_before,
                span,
            )

        validation_error = _validate_json_schema(invocation.arguments, spec.input_schema)
        if validation_error:
            result = self._result(
                invocation,
                ToolStatus.FAILED,
                error_code="INVALID_ARGUMENTS",
                error=validation_error,
            )
            return await self._finish(
                invocation,
                result,
                started,
                decision.reason,
                state_before,
                span,
            )

        if spec.idempotency_key_required and not invocation.idempotency_key:
            result = self._result(
                invocation,
                ToolStatus.FAILED,
                error_code="IDEMPOTENCY_KEY_REQUIRED",
                error="this state-changing tool requires an idempotency key",
            )
            return await self._finish(
                invocation,
                result,
                started,
                decision.reason,
                state_before,
                span,
            )

        replay = await self._idempotent_replay(spec.idempotent, invocation)
        if replay is not None:
            return await self._finish(
                invocation,
                replay,
                started,
                decision.reason,
                state_before,
                span,
            )

        cached = self._get_cache(spec.cache_ttl_s, invocation)
        if cached is not None:
            return await self._finish(
                invocation,
                cached,
                started,
                decision.reason,
                state_before,
                span,
            )

        before_fault = await self.fault_plan.take(
            invocation.context.case_id, invocation.tool_id, "before"
        )
        if before_fault is not None:
            result = self._fault_result(invocation, before_fault)
            return await self._finish(
                invocation,
                result,
                started,
                decision.reason,
                state_before,
                span,
            )

        try:
            produced = spec.handler(invocation)
            if inspect.isawaitable(produced):
                produced = await asyncio.wait_for(produced, timeout=spec.timeout_s)
            if not isinstance(produced, ToolResult):
                raise TypeError(f"tool {spec.tool_id} returned {type(produced).__name__}, expected ToolResult")
            result = produced.model_copy(
                update={
                    "tool_id": invocation.tool_id,
                    "invocation_id": invocation.invocation_id,
                    "idempotency_key": invocation.idempotency_key,
                }
            )
        except TimeoutError:
            result = self._result(
                invocation,
                ToolStatus.TIMED_OUT,
                error_code="TOOL_TIMEOUT",
                error=f"tool exceeded timeout of {spec.timeout_s}s",
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001 - tool boundary converts provider failures
            result = self._result(
                invocation,
                ToolStatus.FAILED,
                error_code="TOOL_EXECUTION_ERROR",
                error=str(exc),
                retryable=False,
            )

        committed_result = result.model_copy(deep=True)
        if self._should_record_idempotency(spec.idempotent, invocation, committed_result):
            await self.idempotency.put(
                invocation.context.case_id,
                invocation.tool_id,
                invocation.idempotency_key or "",
                invocation.arguments,
                committed_result.model_dump(mode="json"),
            )

        self._put_cache(spec.cache_ttl_s, invocation, committed_result)

        # An after-call transport fault models a response that was lost only
        # after the provider accepted (or partially accepted) the operation.
        # A rejected/failed call never crossed that commit boundary, so it must
        # not consume or be masked by an after-commit fault scheduled for the
        # next valid invocation.
        after_fault = None
        if committed_result.status in {
            ToolStatus.SUCCEEDED,
            ToolStatus.PENDING,
            ToolStatus.PARTIAL,
        }:
            after_fault = await self.fault_plan.take(
                invocation.context.case_id, invocation.tool_id, "after"
            )
        if after_fault is not None:
            result = self._fault_result(invocation, after_fault)
            if after_fault.kind is FaultKind.NETWORK_LOST_AFTER_COMMIT:
                result = result.model_copy(
                    update={
                        "verification_required": committed_result.verification_required,
                        "verification_requests": committed_result.verification_requests,
                        "metadata": {
                            **result.metadata,
                            "committed_before_transport_failure": True,
                            "commit_outcome_uncertain": True,
                        },
                    }
                )

        return await self._finish(
            invocation,
            result,
            started,
            decision.reason,
            state_before,
            span,
        )

    async def _idempotent_replay(
        self,
        idempotent: bool,
        invocation: ToolInvocation,
    ) -> ToolResult | None:
        if not idempotent or not invocation.idempotency_key:
            return None
        record = await self.idempotency.get(
            invocation.context.case_id,
            invocation.tool_id,
            invocation.idempotency_key,
        )
        if record is None:
            return None
        canonical = self.idempotency.canonicalize(invocation.arguments)
        if record.canonical_arguments != canonical:
            return self._result(
                invocation,
                ToolStatus.CONFLICT,
                error_code="IDEMPOTENCY_CONFLICT",
                error="the idempotency key was already used with different arguments",
            )
        replay = ToolResult.model_validate(record.result)
        metadata = dict(replay.metadata)
        metadata["idempotent_replay"] = True
        return replay.model_copy(
            update={
                "invocation_id": invocation.invocation_id,
                "latency_ms": 0.0,
                "metadata": metadata,
            }
        )

    @staticmethod
    def _should_record_idempotency(
        idempotent: bool,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> bool:
        return bool(
            idempotent
            and invocation.idempotency_key
            and (
                result.changed
                or result.status
                in {ToolStatus.SUCCEEDED, ToolStatus.PENDING, ToolStatus.PARTIAL}
            )
        )

    async def _finish(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        started: float,
        permission_decision: str,
        state_before: dict[str, Any] | None,
        span: Any,
    ) -> ToolResult:
        latency_ms = (time.perf_counter() - started) * 1000
        state_after = await self._snapshot(invocation.context.case_id)
        event = AuditEvent(
            case_id=invocation.context.case_id,
            trace_id=invocation.context.trace_id,
            invocation_id=invocation.invocation_id,
            actor_id=invocation.context.actor_id,
            tenant_id=invocation.context.tenant_id,
            tool_id=invocation.tool_id,
            arguments=_redact_arguments(invocation.arguments),
            permission_decision=permission_decision,
            state_before=state_before,
            state_after=state_after,
            result_status=result.status.value,
            error_code=result.error_code,
            latency_ms=latency_ms,
            synthetic=invocation.context.synthetic,
        )
        await self.audit_log.append(event)
        result = result.model_copy(
            update={"audit_event_id": event.event_id, "latency_ms": latency_ms}
        )
        await span.end(
            outputs={
                "status": result.status.value,
                "changed": result.changed,
                "error_code": result.error_code,
                "audit_event_id": event.event_id,
            },
            error=result.error,
            metadata={"latency_ms": latency_ms},
        )
        self._record_stats(invocation.tool_id, result)
        return result

    def _record_stats(self, tool_id: str, result: ToolResult) -> None:
        stats = self._stats.setdefault(tool_id, RuntimeToolStats())
        stats.total += 1
        stats.total_latency_ms += result.latency_ms
        if result.success:
            stats.success += 1
            stats.consecutive_fails = 0
            stats.circuit_state = "closed"
            stats.circuit_opened_at = None
            return
        stats.failed += 1
        if result.error_code in {
            "TOOL_TIMEOUT",
            "TOOL_EXECUTION_ERROR",
            "SIMULATED_TIMEOUT",
            "SIMULATED_RATE_LIMIT",
            "SIMULATED_SERVICE_UNAVAILABLE",
            "SIMULATED_NETWORK_LOST_AFTER_COMMIT",
        }:
            stats.consecutive_fails += 1
            if stats.consecutive_fails >= self._circuit_failure_threshold:
                stats.circuit_state = "open"
                stats.circuit_opened_at = time.monotonic()

    def get_stats(self) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for spec in self.registry.list():
            stats = self._stats.setdefault(spec.tool_id, RuntimeToolStats())
            output[spec.tool_id] = {
                "total": stats.total,
                "success": stats.success,
                "failed": stats.failed,
                "success_rate": stats.success_rate,
                "avg_latency_ms": stats.avg_latency_ms,
                "consecutive_fails": stats.consecutive_fails,
                "circuit_state": stats.circuit_state,
            }
        return output

    def _cache_key(self, invocation: ToolInvocation) -> tuple[str, str, str]:
        return (
            invocation.tool_id,
            invocation.context.case_id,
            json.dumps(invocation.arguments, ensure_ascii=False, sort_keys=True, default=str),
        )

    def _get_cache(self, ttl_s: float, invocation: ToolInvocation) -> ToolResult | None:
        if ttl_s <= 0:
            return None
        key = self._cache_key(invocation)
        cached = self._cache.get(key)
        if cached is None:
            return None
        expires_at, raw = cached
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        result = ToolResult.model_validate(raw)
        metadata = dict(result.metadata)
        metadata["cached"] = True
        return result.model_copy(
            update={"invocation_id": invocation.invocation_id, "metadata": metadata}
        )

    def _put_cache(
        self,
        ttl_s: float,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> None:
        if ttl_s <= 0 or not result.success:
            return
        self._cache[self._cache_key(invocation)] = (
            time.monotonic() + ttl_s,
            result.model_dump(mode="json"),
        )

    async def _snapshot(self, case_id: str) -> dict[str, Any] | None:
        if not self.scenarios.has_case(case_id):
            return None
        return await self.scenarios.snapshot(case_id)

    @staticmethod
    def _result(
        invocation: ToolInvocation,
        status: ToolStatus,
        *,
        error_code: str | None = None,
        error: str | None = None,
        retryable: bool = False,
    ) -> ToolResult:
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=status,
            error_code=error_code,
            error=error,
            retryable=retryable,
            idempotency_key=invocation.idempotency_key,
        )

    @staticmethod
    def _fault_result(invocation: ToolInvocation, fault: SimulatedFault) -> ToolResult:
        mapping = {
            FaultKind.TIMEOUT: (ToolStatus.TIMED_OUT, "SIMULATED_TIMEOUT"),
            FaultKind.RATE_LIMIT: (ToolStatus.FAILED, "SIMULATED_RATE_LIMIT"),
            FaultKind.AUTHENTICATION: (ToolStatus.DENIED, "SIMULATED_AUTHENTICATION_FAILURE"),
            FaultKind.SERVICE_UNAVAILABLE: (ToolStatus.FAILED, "SIMULATED_SERVICE_UNAVAILABLE"),
            FaultKind.MALFORMED_RESPONSE: (ToolStatus.FAILED, "SIMULATED_MALFORMED_RESPONSE"),
            FaultKind.PARTIAL_SUCCESS: (ToolStatus.PARTIAL, "SIMULATED_PARTIAL_SUCCESS"),
            FaultKind.NETWORK_LOST_AFTER_COMMIT: (
                ToolStatus.TIMED_OUT,
                "SIMULATED_NETWORK_LOST_AFTER_COMMIT",
            ),
            FaultKind.CONFLICT: (ToolStatus.CONFLICT, "SIMULATED_CONFLICT"),
        }
        status, code = mapping[fault.kind]
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=status,
            error_code=code,
            error=fault.message or fault.kind.value,
            retryable=fault.retryable,
            idempotency_key=invocation.idempotency_key,
        )


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in arguments.items():
        if _SECRET_KEY.search(str(key)):
            output[str(key)] = "[REDACTED_SECRET]"
        elif isinstance(value, dict):
            output[str(key)] = _redact_arguments(value)
        else:
            output[str(key)] = value
    return output


def _validate_json_schema(value: Any, schema: dict[str, Any], path: str = "arguments") -> str | None:
    if not schema:
        return None
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if expected in type_map:
        target = type_map[expected]
        if expected == "integer" and isinstance(value, bool):
            return f"{path} must be an integer"
        if expected == "number" and isinstance(value, bool):
            return f"{path} must be a number"
        if not isinstance(value, target):
            return f"{path} must be {expected}"
    if expected == "object" and isinstance(value, dict):
        required = schema.get("required", [])
        missing = [item for item in required if item not in value]
        if missing:
            return f"{path} missing required fields: {', '.join(missing)}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                return f"{path} contains unsupported fields: {', '.join(unknown)}"
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema:
                error = _validate_json_schema(item, child_schema, f"{path}.{key}")
                if error:
                    return error
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    return None
