import asyncio

import pytest

from core.tools import (
    OperationMode,
    ResourceRationalToolSelector,
    RiskLevel,
    SideEffectScope,
    ToolCategory,
    ToolContext,
    ToolInvocation,
    ToolNeed,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from infrastructure.observability.tracing import build_trace_sink_from_env, sanitize_trace_value
from infrastructure.retrieval import FilesystemRetrievalAdapter, build_filesystem_tools
from infrastructure.simulation import (
    AuditEvent,
    AuditLog,
    DurableScenarioRuntime,
    FaultKind,
    ScenarioActionRule,
    ScenarioCondition,
    ScenarioEffect,
    ScenarioObservationRule,
    ScenarioRuntime,
    SimulatedFault,
    SqliteFaultPlan,
    SqliteIdempotencyStore,
    build_simulation_tools,
)
from infrastructure.tool_runtime import AdaptiveToolRuntime


def run(awaitable):
    return asyncio.run(awaitable)


def build_scenario_runtime():
    scenarios = ScenarioRuntime()
    runtime = AdaptiveToolRuntime(scenarios=scenarios, circuit_failure_threshold=2)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    rule = ScenarioActionRule(
        action_id="sync_credentials",
        description="Synchronize the cached credential version",
        preconditions=[
            ScenarioCondition(path="vpn.status", operator="eq", value="offline"),
            ScenarioCondition(path="credential_mismatch", source="hidden", value=True),
        ],
        effects=[
            ScenarioEffect(path="vpn.status", operation="set", value="online"),
            ScenarioEffect(path="vpn.sync_count", operation="increment", value=1),
        ],
        success_observation="credential synchronization was accepted",
        verification_targets=["vpn.status"],
    )
    run(
        scenarios.create_case(
            "case-vpn",
            visible_state={"vpn": {"status": "offline", "sync_count": 0}},
            hidden_state={"credential_mismatch": True},
            action_rules=[rule],
        )
    )
    return runtime, scenarios


def test_runtime_profile_uses_the_same_policy_as_execution():
    async def handler(invocation):
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
        )

    runtime = AdaptiveToolRuntime()
    runtime.registry.register(
        ToolSpec(
            tool_id="read_status",
            name="Read status",
            description="Read status",
            category=ToolCategory.OBSERVE,
            handler=handler,
            capabilities=frozenset({"status:read"}),
            contract_version="2",
        )
    )
    runtime.registry.register(
        ToolSpec(
            tool_id="repair_status",
            name="Repair status",
            description="Repair status",
            category=ToolCategory.ACT,
            handler=handler,
            capabilities=frozenset({"status:repair"}),
            required_permissions=frozenset({"status:write"}),
        )
    )

    denied = runtime.describe_authorized_tools(ToolContext(case_id="case-1"))
    allowed = runtime.describe_authorized_tools(
        ToolContext(case_id="case-1", permissions={"status:write"})
    )

    assert denied.authorized_tool_ids == {"read_status"}
    assert denied.available_capabilities == {"status:read"}
    assert denied.denied_tools == {"repair_status": "PERMISSION_DENIED"}
    assert denied.tool_contract_versions == {"read_status": "2"}
    assert allowed.authorized_tool_ids == {"read_status", "repair_status"}
    assert allowed.available_capabilities == {"status:read", "status:repair"}


def test_simulation_state_and_idempotency_survive_runtime_restart(tmp_path):
    async def scenario():
        path = tmp_path / "runtime.sqlite3"
        environments = DurableScenarioRuntime(path)
        idempotency = SqliteIdempotencyStore(path)
        await environments.setup()
        await idempotency.setup()
        runtime = AdaptiveToolRuntime(
            scenarios=environments,
            idempotency=idempotency,
        )
        for spec in build_simulation_tools(environments):
            runtime.registry.register(spec)
        await environments.create_case(
            "restart-case",
            visible_state={"service": {"status": "down", "writes": 0}},
            action_rules=[
                ScenarioActionRule(
                    action_id="restore",
                    description="restore service once",
                    effects=[
                        ScenarioEffect(path="service.status", value="up"),
                        ScenarioEffect(
                            path="service.writes", operation="increment", value=1
                        ),
                    ],
                    success_observation="restored",
                    verification_targets=["service.status"],
                )
            ],
        )
        invocation = ToolInvocation(
            tool_id="simulation_execute_action",
            arguments={"action_id": "restore"},
            idempotency_key="restore-once",
            context=ToolContext(
                case_id="restart-case", permissions={"simulation:act"}
            ),
        )
        first = await runtime.execute(invocation)
        assert first.changed is True
        await idempotency.close()
        await environments.close()

        restored_env = DurableScenarioRuntime(path)
        restored_idempotency = SqliteIdempotencyStore(path)
        await restored_env.setup()
        await restored_idempotency.setup()
        restored_runtime = AdaptiveToolRuntime(
            scenarios=restored_env,
            idempotency=restored_idempotency,
        )
        for spec in build_simulation_tools(restored_env):
            restored_runtime.registry.register(spec)
        replay = await restored_runtime.execute(
            invocation.model_copy(update={"invocation_id": "after-restart"})
        )
        snapshot = await restored_env.snapshot("restart-case")
        await restored_idempotency.close()
        await restored_env.close()
        assert replay.metadata["idempotent_replay"] is True
        assert snapshot["visible_state"]["service"]["writes"] == 1

    run(scenario())


def test_fault_queue_survives_restart_and_is_consumed_once(tmp_path):
    async def scenario():
        path = tmp_path / "runtime.sqlite3"
        first = SqliteFaultPlan(path)
        await first.setup()
        await first.add(
            "case-1",
            "tool-1",
            SimulatedFault(kind="timeout", phase="before", retryable=True),
        )
        await first.close()

        restored = SqliteFaultPlan(path)
        await restored.setup()
        fault = await restored.take("case-1", "tool-1", "before")
        consumed = await asyncio.gather(
            *(restored.take("case-1", f"tool-{index}", "before") for index in range(6))
        )
        await restored.close()
        assert fault is not None and fault.kind is FaultKind.TIMEOUT
        assert consumed == [None] * 6

    run(scenario())


def test_tool_audit_log_reloads_committed_events_after_restart(tmp_path):
    async def scenario():
        path = tmp_path / "audit.jsonl"
        first = AuditLog(path)
        await first.append(
            AuditEvent(
                case_id="case-1",
                trace_id="trace",
                invocation_id="invoke",
                actor_id="actor",
                tenant_id="local",
                tool_id="tool",
                permission_decision="allowed",
                result_status="succeeded",
            )
        )
        restored = AuditLog(path)
        events = await restored.list("case-1")
        assert len(events) == 1
        assert events[0].invocation_id == "invoke"

    run(scenario())


def test_simulated_write_enforces_permission_and_audits_denial():
    runtime, scenarios = build_scenario_runtime()
    result = run(
        runtime.execute(
            ToolInvocation(
                tool_id="simulation_execute_action",
                arguments={"action_id": "sync_credentials"},
                idempotency_key="sync-1",
                context=ToolContext(case_id="case-vpn"),
            )
        )
    )

    assert result.status is ToolStatus.DENIED
    assert result.error_code == "PERMISSION_DENIED"
    assert run(scenarios.observe("case-vpn", "vpn.status")) == "offline"
    events = run(runtime.audit_log.list("case-vpn"))
    assert len(events) == 1
    assert events[0].result_status == "denied"


def test_simulated_write_is_idempotent_and_requires_independent_verification():
    runtime, scenarios = build_scenario_runtime()
    invocation = ToolInvocation(
        tool_id="simulation_execute_action",
        arguments={"action_id": "sync_credentials"},
        idempotency_key="sync-1",
        context=ToolContext(case_id="case-vpn", permissions={"simulation:act"}),
    )
    first = run(runtime.execute(invocation))
    second = run(
        runtime.execute(
            invocation.model_copy(update={"invocation_id": "second-invocation"})
        )
    )

    assert first.status is ToolStatus.SUCCEEDED
    assert first.verification_required is True
    assert second.status is ToolStatus.SUCCEEDED
    assert second.metadata["idempotent_replay"] is True
    assert run(scenarios.observe("case-vpn", "vpn.sync_count")) == 1


def test_network_loss_after_commit_is_recovered_by_idempotent_retry():
    runtime, scenarios = build_scenario_runtime()
    run(
        runtime.fault_plan.add(
            "case-vpn",
            "simulation_execute_action",
            SimulatedFault(kind=FaultKind.NETWORK_LOST_AFTER_COMMIT, phase="after"),
        )
    )
    original = ToolInvocation(
        tool_id="simulation_execute_action",
        arguments={"action_id": "sync_credentials"},
        idempotency_key="sync-network-loss",
        context=ToolContext(case_id="case-vpn", permissions={"simulation:act"}),
    )
    first = run(runtime.execute(original))
    retry = run(
        runtime.execute(original.model_copy(update={"invocation_id": "retry-invocation"}))
    )

    assert first.status is ToolStatus.TIMED_OUT
    assert first.metadata["committed_before_transport_failure"] is True
    assert first.metadata["commit_outcome_uncertain"] is True
    assert first.verification_required is True
    assert first.verification_requests
    assert run(scenarios.observe("case-vpn", "vpn.status")) == "online"
    assert retry.status is ToolStatus.SUCCEEDED
    assert retry.metadata["idempotent_replay"] is True
    assert run(scenarios.observe("case-vpn", "vpn.sync_count")) == 1


def test_delayed_action_only_changes_state_after_virtual_time_advances():
    scenarios = ScenarioRuntime()
    rule = ScenarioActionRule(
        action_id="reload_service",
        description="Reload a service asynchronously",
        effects=[ScenarioEffect(path="service.status", value="ready")],
        success_observation="reload scheduled",
        delay_seconds=30,
    )
    run(
        scenarios.create_case(
            "case-delay",
            visible_state={"service": {"status": "reloading"}},
            action_rules=[rule],
        )
    )
    outcome = run(scenarios.execute_action("case-delay", "reload_service"))

    assert outcome["status"] == "pending"
    assert run(scenarios.observe("case-delay", "service.status")) == "reloading"
    run(scenarios.advance_time(30))
    assert run(scenarios.observe("case-delay", "service.status")) == "ready"


def test_wait_changes_retry_conditions_without_claiming_case_progress():
    runtime, _ = build_scenario_runtime()
    result = run(
        runtime.execute(
            ToolInvocation(
                tool_id="simulation_wait",
                arguments={"seconds": 5},
                idempotency_key="wait-5-seconds",
                context=ToolContext(
                    case_id="case-vpn", permissions={"simulation:act"}
                ),
            )
        )
    )

    assert result.status is ToolStatus.SUCCEEDED
    assert result.changed is False
    assert result.metadata["retry_condition_changed"] is True


def test_resource_rational_selector_uses_capabilities_and_costs_not_keywords():
    registry = ToolRegistry()

    async def handler(invocation):
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
        )

    registry.register(
        ToolSpec(
            tool_id="fast_exact",
            name="fast exact",
            description="",
            category=ToolCategory.OBSERVE,
            handler=handler,
            capabilities=frozenset({"error_evidence"}),
            expected_latency_ms=20,
            expected_information_gain=0.8,
            expected_progress=0.7,
        )
    )
    registry.register(
        ToolSpec(
            tool_id="slow_semantic",
            name="slow semantic",
            description="",
            category=ToolCategory.OBSERVE,
            handler=handler,
            capabilities=frozenset({"error_evidence"}),
            expected_latency_ms=2000,
            expected_information_gain=0.85,
            expected_progress=0.7,
        )
    )
    selector = ResourceRationalToolSelector(registry)
    selected = selector.select(
        ToolNeed(capabilities={"error_evidence"}, latency_budget_ms=500),
        ToolContext(case_id="case-select"),
    )

    assert selected is not None
    assert selected.tool_id == "fast_exact"


def test_resource_rational_selector_filters_real_writes_by_policy():
    registry = ToolRegistry()

    async def handler(invocation):
        raise AssertionError("must not execute")

    registry.register(
        ToolSpec(
            tool_id="real_write",
            name="real write",
            description="",
            category=ToolCategory.ACT,
            handler=handler,
            capabilities=frozenset({"change_state"}),
            operation_mode=OperationMode.PRODUCTION_WRITE,
            risk_level=RiskLevel.HIGH,
        )
    )
    selector = ResourceRationalToolSelector(registry)
    selected = selector.select(
        ToolNeed(
            capabilities={"change_state"},
            allowed_modes={OperationMode.PRODUCTION_WRITE},
            max_risk=RiskLevel.HIGH,
        ),
        ToolContext(case_id="case-select"),
    )
    assert selected is None
    assessment = selector.assess(
        ToolNeed(
            capabilities={"change_state"},
            allowed_modes={OperationMode.PRODUCTION_WRITE},
            max_risk=RiskLevel.HIGH,
        ),
        ToolContext(case_id="case-select"),
    )
    assert assessment.matching_tool_ids == ["real_write"]
    assert [item.code for item in assessment.exclusions] == [
        "EXTERNAL_WRITE_DISABLED"
    ]


def test_bootstrap_contract_rejects_tools_with_side_effects():
    async def handler(invocation):
        raise AssertionError("must not execute")

    with pytest.raises(ValueError, match="bootstrap_safe tools"):
        ToolSpec(
            tool_id="unsafe_bootstrap",
            name="unsafe bootstrap",
            description="",
            category=ToolCategory.ACT,
            handler=handler,
            operation_mode=OperationMode.SIMULATED_WRITE,
            side_effect_scope=SideEffectScope.CASE_SANDBOX,
            bootstrap_safe=True,
        )


def test_filesystem_adapter_returns_live_line_provenance(tmp_path):
    source = tmp_path / "service.log"
    source.write_text("ready\nerror TS-999 credential mismatch\n", encoding="utf-8")
    runtime = AdaptiveToolRuntime()
    for spec in build_filesystem_tools(
        FilesystemRetrievalAdapter(tmp_path, respect_ignore=False)
    ):
        runtime.registry.register(spec)
    result = run(
        runtime.execute(
            ToolInvocation(
                tool_id="workspace_search_text",
                arguments={"query": "TS-999"},
                context=ToolContext(case_id="case-files"),
            )
        )
    )

    assert result.status is ToolStatus.SUCCEEDED
    assert result.data[0]["path"] == "service.log"
    assert result.data[0]["line"] == 2
    assert result.evidence[0].metadata["retrieval_method"] == "ripgrep"


def test_langsmith_defaults_to_off_and_sanitizes_sensitive_content(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    sink = build_trace_sink_from_env()
    assert sink.enabled is False

    sanitized = sanitize_trace_value(
        {
            "api_key": "secret-value",
            "raw_user_input": "private request",
            "metadata": {"case_id": "case-1"},
        }
    )
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["raw_user_input"] == "[REDACTED_CONTENT]"
    assert sanitized["metadata"]["case_id"] == "case-1"


def test_repeated_transient_failures_open_circuit():
    runtime = AdaptiveToolRuntime(circuit_failure_threshold=2)

    async def failing(invocation):
        raise RuntimeError("provider down")

    runtime.registry.register(
        ToolSpec(
            tool_id="unstable_read",
            name="unstable",
            description="",
            category=ToolCategory.OBSERVE,
            handler=failing,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
    )
    context = ToolContext(case_id="case-circuit")
    for index in range(2):
        result = run(
            runtime.execute(
                ToolInvocation(
                    tool_id="unstable_read",
                    arguments={},
                    context=context,
                    invocation_id=f"failure-{index}",
                )
            )
        )
        assert result.error_code == "TOOL_EXECUTION_ERROR"
    blocked = run(
        runtime.execute(
            ToolInvocation(
                tool_id="unstable_read",
                arguments={},
                context=context,
                invocation_id="blocked",
            )
        )
    )
    assert blocked.error_code == "TOOL_CIRCUIT_OPEN"
    assert runtime.get_stats()["unstable_read"]["circuit_state"] == "open"
    assert len(run(runtime.audit_log.list(case_id="case-circuit"))) == 3


def test_partial_observability_preserves_reliability_and_staleness_metadata():
    scenarios = ScenarioRuntime()
    run(
        scenarios.create_case(
            "case-noisy",
            visible_state={"health": {"status": "degraded"}},
            observation_rules=[
                ScenarioObservationRule(
                    path="health",
                    values=[
                        {"status": "healthy", "sample_age_s": 600},
                        {"status": "degraded", "sample_age_s": 1},
                    ],
                    reliabilities=[0.3, 0.95],
                    stale_indices={0},
                    source="replica-monitor",
                )
            ],
        )
    )
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    context = ToolContext(case_id="case-noisy", synthetic=True)

    first = run(
        runtime.execute(
            ToolInvocation(
                tool_id="simulation_inspect_health",
                arguments={"path": "health"},
                context=context,
            )
        )
    )
    second = run(
        runtime.execute(
            ToolInvocation(
                tool_id="simulation_inspect_health",
                arguments={"path": "health"},
                context=context,
            )
        )
    )

    assert first.data["value"]["status"] == "healthy"
    assert first.evidence[0].reliability == 0.3
    assert first.evidence[0].metadata["stale"] is True
    assert second.data["value"]["status"] == "degraded"
    assert second.evidence[0].reliability == 0.95
    assert second.evidence[0].metadata["stale"] is False
    assert {
        "simulation_inspect_health",
        "simulation_inspect_logs",
        "simulation_compare_config",
        "simulation_trace_dependencies",
        "simulation_poll_job",
    }.issubset({item.tool_id for item in runtime.registry.list()})
