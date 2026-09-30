"""Generic development tools over the rule-driven ScenarioRuntime."""

from __future__ import annotations

from core.tools import (
    DataSource,
    Evidence,
    OperationMode,
    RiskLevel,
    SideEffectScope,
    ToolCategory,
    ToolInvocation,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

from .runtime import ScenarioRuntime


def build_simulation_tools(runtime: ScenarioRuntime) -> list[ToolSpec]:
    async def diagnostic_observation(
        invocation: ToolInvocation,
        *,
        path: str,
        key: str,
    ) -> ToolResult:
        observed = await runtime.observe_with_metadata(invocation.context.case_id, path)
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data={"path": path, "value": observed["value"]},
            observation=f"observed {key} from synthetic environment",
            evidence=[
                Evidence(
                    key=key,
                    value=observed["value"],
                    source=observed["source"],
                    source_type="simulated_diagnostic",
                    reliability=observed["reliability"],
                    metadata={
                        "synthetic": True,
                        "stale": observed["stale"],
                        "observation_index": observed["observation_index"],
                    },
                )
            ],
        )

    async def observe(invocation: ToolInvocation) -> ToolResult:
        path = str(invocation.arguments.get("path", ""))
        observed = await runtime.observe_with_metadata(invocation.context.case_id, path)
        value = observed["value"]
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data={"path": path, "value": value},
            observation=f"observed simulated state at {path or '<root>'}",
            evidence=[
                Evidence(
                    key=path or "visible_state",
                    value=value,
                    source=observed["source"],
                    source_type="simulated_observation",
                    reliability=observed["reliability"],
                    metadata={
                        "synthetic": True,
                        "stale": observed["stale"],
                        "observation_index": observed["observation_index"],
                    },
                )
            ],
        )

    async def list_actions(invocation: ToolInvocation) -> ToolResult:
        actions = await runtime.list_actions(invocation.context.case_id)
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data=actions,
            observation=f"scenario exposes {len(actions)} allowed actions",
        )

    async def execute_action(invocation: ToolInvocation) -> ToolResult:
        outcome = await runtime.execute_action(
            invocation.context.case_id,
            str(invocation.arguments["action_id"]),
            dict(invocation.arguments.get("parameters", {})),
        )
        status = ToolStatus(outcome["status"])
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=status,
            changed=bool(outcome.get("changed", False)),
            data=outcome,
            observation=outcome.get("observation"),
            error_code=outcome.get("error_code"),
            error=outcome.get("observation") if status is ToolStatus.FAILED else None,
            retryable=False,
            verification_required=status in {ToolStatus.SUCCEEDED, ToolStatus.PENDING},
            verification_requests=outcome.get("verification_requests", []),
        )

    async def wait_for_environment(invocation: ToolInvocation) -> ToolResult:
        seconds = float(invocation.arguments["seconds"])
        if seconds <= 0:
            raise ValueError("seconds must be greater than zero")
        await runtime.advance_time(seconds)
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            changed=False,
            data={"advanced_seconds": seconds},
            observation=f"advanced simulated environment time by {seconds:g} seconds",
            metadata={"retry_condition_changed": True},
        )

    async def inspect_health(invocation: ToolInvocation) -> ToolResult:
        return await diagnostic_observation(
            invocation,
            path=str(invocation.arguments.get("path", "health")),
            key="service_health",
        )

    async def inspect_logs(invocation: ToolInvocation) -> ToolResult:
        return await diagnostic_observation(
            invocation,
            path=str(invocation.arguments.get("path", "logs")),
            key="diagnostic_logs",
        )

    async def compare_config(invocation: ToolInvocation) -> ToolResult:
        return await diagnostic_observation(
            invocation,
            path=str(invocation.arguments.get("path", "configuration")),
            key="configuration_snapshot",
        )

    async def trace_dependencies(invocation: ToolInvocation) -> ToolResult:
        return await diagnostic_observation(
            invocation,
            path=str(invocation.arguments.get("path", "dependencies")),
            key="dependency_state",
        )

    async def poll_job(invocation: ToolInvocation) -> ToolResult:
        job_id = str(invocation.arguments["job_id"])
        return await diagnostic_observation(
            invocation,
            path=f"jobs.{job_id}",
            key=f"job:{job_id}",
        )

    diagnostic_path_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    }

    return [
        ToolSpec(
            tool_id="simulation_observe_state",
            name="Observe simulated case state",
            description=(
                "Read an observable path from the current synthetic Case. Hidden ground truth is never returned."
            ),
            category=ToolCategory.OBSERVE,
            handler=observe,
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            capabilities=frozenset({"scenario_observation", "state_inspection"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            side_effect_scope=SideEffectScope.NONE,
            risk_level=RiskLevel.LOW,
            expected_latency_ms=5,
            expected_information_gain=0.8,
            expected_progress=0.5,
            bootstrap_safe=True,
            bootstrap_arguments={"path": ""},
            experience_argument_fields=frozenset({"path"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
        ToolSpec(
            tool_id="simulation_list_actions",
            name="List simulated actions",
            description="List actions that the current synthetic Case environment exposes.",
            category=ToolCategory.OBSERVE,
            handler=list_actions,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            capabilities=frozenset({"scenario_action_discovery"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            side_effect_scope=SideEffectScope.NONE,
            risk_level=RiskLevel.LOW,
            expected_latency_ms=5,
            expected_information_gain=0.5,
            expected_progress=0.4,
            bootstrap_safe=True,
            bootstrap_arguments={},
            experience_result_fields=frozenset({"available_actions"}),
        ),
        ToolSpec(
            tool_id="simulation_execute_action",
            name="Execute simulated action",
            description=(
                "Execute one declared action in the current resettable Case sandbox. "
                "A successful call still requires an independent observation to verify resolution."
            ),
            category=ToolCategory.ACT,
            handler=execute_action,
            input_schema={
                "type": "object",
                "properties": {
                    "action_id": {"type": "string"},
                    "parameters": {"type": "object"},
                },
                "required": ["action_id"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"scenario_action", "state_change"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.SIMULATED_WRITE,
            side_effect_scope=SideEffectScope.CASE_SANDBOX,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({"simulation:act"}),
            idempotent=True,
            idempotency_key_required=True,
            expected_latency_ms=10,
            expected_information_gain=0.6,
            expected_progress=0.9,
            experience_argument_fields=frozenset({"action_id"}),
            experience_result_fields=frozenset(
                {"action_id", "status", "verification_targets"}
            ),
        ),
        ToolSpec(
            tool_id="simulation_wait",
            name="Wait for simulated environment",
            description=(
                "Advance virtual time after an asynchronous action explicitly reports "
                "that it is pending. This is audited and only affects the resettable "
                "simulation environment."
            ),
            category=ToolCategory.ACT,
            handler=wait_for_environment,
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
                "required": ["seconds"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"scenario_wait", "time_advance"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.SIMULATED_WRITE,
            side_effect_scope=SideEffectScope.CASE_SANDBOX,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({"simulation:act"}),
            idempotent=True,
            idempotency_key_required=True,
            expected_latency_ms=1,
            expected_information_gain=0.1,
            expected_progress=0.7,
            experience_argument_fields=frozenset({"seconds"}),
        ),
        ToolSpec(
            tool_id="simulation_inspect_health",
            name="Inspect service health",
            description="Read a synthetic health/status snapshot without changing the Case.",
            category=ToolCategory.OBSERVE,
            handler=inspect_health,
            input_schema=diagnostic_path_schema,
            capabilities=frozenset({"health_inspection", "state_inspection"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            risk_level=RiskLevel.LOW,
            expected_information_gain=0.75,
            availability=lambda context: runtime.has_observable_path(
                context.case_id, "health"
            ),
            experience_argument_fields=frozenset({"path"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
        ToolSpec(
            tool_id="simulation_inspect_logs",
            name="Inspect diagnostic logs",
            description="Read synthetic logs; entries may be incomplete, stale, or conflicting.",
            category=ToolCategory.OBSERVE,
            handler=inspect_logs,
            input_schema=diagnostic_path_schema,
            capabilities=frozenset({"log_inspection", "evidence_collection"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            risk_level=RiskLevel.LOW,
            expected_information_gain=0.8,
            availability=lambda context: runtime.has_observable_path(
                context.case_id, "logs"
            ),
            experience_argument_fields=frozenset({"path"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
        ToolSpec(
            tool_id="simulation_compare_config",
            name="Inspect configuration snapshot",
            description="Read synthetic desired/actual configuration data for comparison.",
            category=ToolCategory.OBSERVE,
            handler=compare_config,
            input_schema=diagnostic_path_schema,
            capabilities=frozenset({"configuration_inspection", "configuration_diff"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            risk_level=RiskLevel.LOW,
            expected_information_gain=0.8,
            availability=lambda context: runtime.has_observable_path(
                context.case_id, "configuration"
            ),
            experience_argument_fields=frozenset({"path"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
        ToolSpec(
            tool_id="simulation_trace_dependencies",
            name="Inspect dependency state",
            description="Read synthetic upstream/downstream dependency state.",
            category=ToolCategory.OBSERVE,
            handler=trace_dependencies,
            input_schema=diagnostic_path_schema,
            capabilities=frozenset({"dependency_inspection", "topology_inspection"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            risk_level=RiskLevel.LOW,
            expected_information_gain=0.7,
            availability=lambda context: runtime.has_observable_path(
                context.case_id, "dependencies"
            ),
            experience_argument_fields=frozenset({"path"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
        ToolSpec(
            tool_id="simulation_poll_job",
            name="Poll asynchronous job",
            description="Read one synthetic provider job by ID without advancing time.",
            category=ToolCategory.VERIFY,
            handler=poll_job,
            input_schema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"job_polling", "asynchronous_status"}),
            data_source=DataSource.SYNTHETIC,
            operation_mode=OperationMode.READ_ONLY,
            risk_level=RiskLevel.LOW,
            expected_information_gain=0.7,
            availability=lambda context: runtime.has_observable_path(
                context.case_id, "jobs"
            ),
            experience_argument_fields=frozenset({"job_id"}),
            experience_result_fields=frozenset({"path", "value"}),
        ),
    ]


__all__ = ["build_simulation_tools"]
