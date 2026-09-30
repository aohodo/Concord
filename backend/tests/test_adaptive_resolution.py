import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from core.adaptive_resolution import (
    AdaptiveResolutionService,
    CaseEvent,
    CaseEventType,
    PredictionAssessment,
    ProvisionalExplanation,
    ResolutionDecision,
    ResolutionPlan,
    ResolutionStatus,
)
from core.adaptive_resolution.graph import AdaptiveResolutionGraph
from core.tools import Evidence, ToolResult, ToolStatus
from infrastructure.case_events import InMemoryCaseEventStore
from infrastructure.simulation import (
    ScenarioActionRule,
    ScenarioCondition,
    ScenarioEffect,
    ScenarioRuntime,
    SimulatedFault,
    build_simulation_tools,
)
from infrastructure.tool_runtime import AdaptiveToolRuntime


def run(awaitable):
    return asyncio.run(awaitable)


def test_repeated_identical_observation_is_not_counted_as_case_progress():
    evidence = Evidence(
        key="connector.status",
        value="degraded",
        source="simulation",
    )
    result = ToolResult(
        tool_id="simulation_observe_state",
        invocation_id="observation-2",
        status=ToolStatus.SUCCEEDED,
        evidence=[evidence],
    )
    history = [
        {
            "tool_id": "simulation_observe_state",
            "evidence": [evidence.model_dump(mode="json")],
        }
    ]

    assert not AdaptiveResolutionGraph._has_novel_evidence(result, history)
    changed = result.model_copy(
        update={
            "evidence": [
                evidence.model_copy(update={"value": "recovering"})
            ]
        }
    )
    assert AdaptiveResolutionGraph._has_novel_evidence(changed, history)


def test_retryable_failure_requires_a_changed_retry_condition_or_review():
    arguments = {"action_id": "recover_connector"}
    canonical = '{"action_id": "recover_connector"}'
    failure = {
        "tool_id": "simulation_execute_action",
        "canonical_arguments": canonical,
        "status": "timed_out",
        "error_code": "SIMULATED_TIMEOUT",
        "retryable": True,
    }

    assert AdaptiveResolutionGraph._same_failed_call(
        [failure], "simulation_execute_action", arguments
    )
    assert not AdaptiveResolutionGraph._same_failed_call(
        [
            failure,
            {
                "tool_id": "clock_advance",
                "status": "succeeded",
                "metadata": {"retry_condition_changed": True},
            },
        ],
        "simulation_execute_action",
        arguments,
    )
    assert not AdaptiveResolutionGraph._same_failed_call(
        [failure],
        "simulation_execute_action",
        arguments,
        ["independent-review-assignment"],
    )


def test_failure_memory_ablation_allows_immediate_identical_retry():
    case_id = "m2-no-failure-memory"
    runtime = build_runtime(case_id)
    run(
        runtime.fault_plan.add(
            case_id,
            "simulation_execute_action",
            SimulatedFault(kind="timeout", phase="before", retryable=True),
        )
    )
    repeated_action = ResolutionPlan(
        decision=ResolutionDecision.USE_TOOL,
        rationale="消融实验立即重复同一动作",
        required_capabilities={"scenario_action", "state_change"},
        tool_arguments={"action_id": "refresh_credential"},
    )
    planner = QueuedPlanner(
        repeated_action,
        repeated_action,
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="第二次动作后环境验证完成",
            verification_complete=True,
            resolution_evidence=["vpn=connected", "access=restored"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
            failure_memory_enabled=False,
        )
    )

    action_calls = [
        item
        for item in result.tool_history
        if item["tool_id"] == "simulation_execute_action"
    ]
    assert result.status is ResolutionStatus.RESOLVED
    assert len(action_calls) == 2
    assert all(
        item.get("error_code") != "REPEATED_FAILED_ACTION_BLOCKED"
        for item in action_calls
    )


class QueuedPlanner:
    def __init__(self, *plans: ResolutionPlan):
        self.plans = list(plans)
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.plans.pop(0)


def resolution_context(case_id: str) -> dict:
    return {
        "handoff_contract": {
            "name": "concord_m1_to_m2",
            "version": "1.0",
        },
        "case_id": case_id,
        "goal": {
            "current_outcome": "恢复内网访问",
            "success_criteria": [{"value": "access=restored"}],
        },
        "evidence_handoff": {
            "open": [],
            "resolved": [],
            "unavailable": [],
            "deferred": [],
        },
        "action_constraints": {
            "case_entry_allowed": True,
            "evidence_complete": True,
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
            "explanation_mode": "minimal",
            "forbidden_retries": [],
        },
    }


def build_runtime(case_id: str) -> AdaptiveToolRuntime:
    scenarios = ScenarioRuntime()
    run(
        scenarios.create_case(
            case_id,
            visible_state={"vpn": "authentication_failed", "access": "blocked"},
            hidden_state={"root_cause": "stale_credential"},
            action_rules=[
                ScenarioActionRule(
                    action_id="refresh_credential",
                    description="刷新凭据",
                    preconditions=[
                        ScenarioCondition(path="vpn", value="authentication_failed")
                    ],
                    effects=[
                        ScenarioEffect(path="vpn", value="connected"),
                        ScenarioEffect(path="access", value="restored"),
                    ],
                    success_observation="凭据刷新请求已执行",
                    verification_targets=["vpn", "access"],
                )
            ],
        )
    )
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    return runtime


def test_langgraph_m2_closes_action_observation_verification_loop():
    case_id = "m2-loop"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="执行低风险、可重置的凭据刷新",
            provisional_explanations=[
                ProvisionalExplanation(
                    statement="凭据可能没有同步",
                    supporting_evidence=["环境暴露 refresh_credential"],
                )
            ],
            previous_observation_assessment=PredictionAssessment.MATCH,
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
            expected_observation="动作被接受，但仍需独立观察",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="独立观察满足成功判据",
            provisional_explanations=[
                ProvisionalExplanation(
                    statement="旧凭据状态导致认证失败",
                    strength="high",
                    supporting_evidence=["刷新后 access=restored"],
                )
            ],
            previous_observation_assessment=PredictionAssessment.MATCH,
            verification_complete=True,
            resolution_evidence=["vpn=connected", "access=restored"],
            response="内网访问已经恢复。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
            max_cycles=6,
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.response == "已经处理完成，并通过独立检查确认结果。"
    assert len(planner.calls) == 2
    assert [item["tool_id"] for item in result.tool_history] == [
        "simulation_list_actions",
        "simulation_observe_state",
        "simulation_execute_action",
        "simulation_observe_state",
        "simulation_observe_state",
    ]
    assert result.tool_history[2]["verification_required"] is True
    assert {
        item["verification_target"]: item["data"]["value"]
        for item in result.tool_history
        if item.get("automatic_verification")
    } == {"vpn": "connected", "access": "restored"}
    assert planner.calls[0]["decision_event"] == "bootstrap_complete"
    assert planner.calls[1]["decision_event"] == "verification_complete"
    assert len(run(runtime.audit_log.list(case_id))) == 5
    assert [item.stage for item in result.progress_events] == [
        "initial_observation",
        "tool_result",
        "verification",
        "resolved",
    ]
    assert result.guardrail_events == []


def test_case_user_state_changes_tool_ranking_control_weights():
    case_id = "m2-user-state-control"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="deadline 下优先低时延恢复动作",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="verified",
            verification_complete=True,
            resolution_evidence=["access=restored"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(), tool_runtime=runtime, planner=planner
    )
    context = resolution_context(case_id)
    context["user_state"] = {
        "deadline": "15分钟后演示",
        "patience": "low",
        "effort_willingness": "low",
        "frustration": "high",
    }

    result = run(
        service.resolve(
            resolution_context=context,
            permissions=["simulation:act"],
        )
    )

    event = next(
        item for item in result.control_events if item["mechanism"] == "case_user_state"
    )
    assert event["behavior_changes"] == {
        "latency_weight": 0.55,
        "burden_weight": 1.1,
    }
    assert result.response.startswith("已优先完成处理")


def test_unverified_resolution_is_rejected_and_replanned():
    case_id = "m2-verification-guard"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="过早宣布完成",
            verification_complete=False,
            response="已经解决。",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.COLLABORATE,
            rationale="当前没有形成可验证证据",
            collaboration_reason="verification evidence missing",
            response="需要保留当前证据并交给协作阶段。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )

    result = run(service.resolve(resolution_context=resolution_context(case_id)))

    assert result.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert result.cycles == 2
    assert result.guardrail_events == [
        {"cycle": 1, "code": "UNVERIFIED_RESOLUTION_REJECTED"}
    ]
    assert "independent verification is incomplete" in planner.calls[1][
        "guardrail_feedback"
    ]


def test_all_declared_verification_targets_are_observed_automatically():
    case_id = "m2-target-verification"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="执行可重置动作",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
            expected_observation="动作被接受",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="自动验证已经覆盖全部目标",
            previous_observation_assessment=PredictionAssessment.MATCH,
            verification_complete=True,
            resolution_evidence=["vpn=connected", "access=restored"],
            response="已经解决。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )
    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert len(planner.calls) == 2
    assert planner.calls[1]["decision_event"] == "verification_complete"
    assert {
        item["verification_target"]
        for item in result.tool_history
        if item.get("automatic_verification")
    } == {"vpn", "access"}
    assert result.guardrail_events == []


def test_runtime_authorization_boundary_is_preserved_for_m3_without_text_rules():
    case_id = "m2-authorization-handoff"
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=QueuedPlanner(
            ResolutionPlan(
                decision=ResolutionDecision.USE_TOOL,
                rationale="the next useful action changes simulated state",
                required_capabilities={"scenario_action", "state_change"},
                tool_arguments={"action_id": "refresh_credential"},
            )
        ),
    )

    result = run(service.resolve(resolution_context=resolution_context(case_id)))

    assert result.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert result.collaboration_handoff is not None
    structure = result.collaboration_handoff.coordination_structure
    assert structure.human_authority_required is True
    assert any("PERMISSION_DENIED" in item for item in structure.blocking_evidence)
    assert result.decision_events[-1]["trigger"] == "authorization_boundary"


def test_prediction_mismatch_interrupts_episode_and_replans():
    case_id = "m2-delayed-mismatch"
    scenarios = ScenarioRuntime()
    run(
        scenarios.create_case(
            case_id,
            visible_state={"vpn": "authentication_failed", "access": "blocked"},
            action_rules=[
                ScenarioActionRule(
                    action_id="delayed_refresh",
                    description="异步刷新凭据",
                    effects=[
                        ScenarioEffect(path="vpn", value="connected"),
                        ScenarioEffect(path="access", value="restored"),
                    ],
                    success_observation="凭据刷新已经排队",
                    verification_targets=["vpn", "access"],
                    delay_seconds=30,
                )
            ],
        )
    )
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="执行可逆的模拟刷新",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "delayed_refresh"},
            expected_observation="动作进入异步队列",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.COLLABORATE,
            rationale="验证与预期不一致，当前又没有等待能力",
            previous_observation_assessment=PredictionAssessment.MISMATCH,
            collaboration_reason="asynchronous completion requires a wait capability",
            response="动作尚未生效，需要等待能力或外部协作。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )
    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
        )
    )

    assert result.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert len(planner.calls) == 2
    assert planner.calls[1]["decision_event"] == "prediction_mismatch"
    assert result.decision_events[-1]["mismatched_targets"] == ["vpn", "access"]


def test_async_action_can_wait_then_reuse_pending_verification_requests():
    case_id = "m2-delayed-recovery"
    scenarios = ScenarioRuntime()
    run(
        scenarios.create_case(
            case_id,
            visible_state={"worker": {"status": "stopped"}},
            action_rules=[
                ScenarioActionRule(
                    action_id="start_worker",
                    description="30 秒后启动",
                    effects=[ScenarioEffect(path="worker.status", value="running")],
                    success_observation="启动任务已排队",
                    verification_targets=["worker.status"],
                    delay_seconds=30,
                )
            ],
        )
    )
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="启动 Worker",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "start_worker"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="动作已接受但尚未到生效时间，推进模拟时钟",
            previous_observation_assessment=PredictionAssessment.MISMATCH,
            required_capabilities={"scenario_wait", "time_advance"},
            tool_arguments={"seconds": 30},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="等待后独立验证已通过",
            previous_observation_assessment=PredictionAssessment.MATCH,
            verification_complete=True,
            resolution_evidence=["worker.status=running"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )
    context = resolution_context(case_id)
    context["goal"]["success_criteria"] = [{"value": "worker.status=running"}]

    result = run(
        service.resolve(
            resolution_context=context,
            permissions=["simulation:act"],
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.episode_decisions == 3
    assert "prediction_mismatch" in {
        item["event"] for item in result.decision_events
    }
    assert result.tool_history[-1]["verification_target"] == "worker.status"
    assert result.tool_history[-1]["expectation_matched"] is True


def test_uncertain_commit_records_failure_and_verifies_without_retrying_write():
    case_id = "m2-uncertain-commit"
    runtime = build_runtime(case_id)
    run(
        runtime.fault_plan.add(
            case_id,
            "simulation_execute_action",
            SimulatedFault(kind="network_lost_after_commit", phase="after"),
        )
    )
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="执行一次幂等恢复",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="断网后的真实结果已经独立验证",
            verification_complete=True,
            resolution_evidence=["vpn=connected", "access=restored"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(), tool_runtime=runtime, planner=planner
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert [item["event"] for item in result.decision_events][-2:] == [
        "tool_failure",
        "verification_complete",
    ]
    assert sum(
        item["tool_id"] == "simulation_execute_action" for item in result.tool_history
    ) == 1


def test_missing_tool_capabilities_are_replanned_instead_of_escalated():
    case_id = "m2-missing-capabilities"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="模型漏填了能力集合",
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="根据工具契约补齐能力",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="自动验证完成",
            verification_complete=True,
            resolution_evidence=["vpn=connected", "access=restored"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            permissions=["simulation:act"],
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.episode_decisions == 3
    assert {item["code"] for item in result.guardrail_events} == {
        "PLAN_CAPABILITY_MISSING"
    }


def test_user_evidence_resumes_same_case_without_repeating_bootstrap():
    case_id = "m2-user-resume"
    runtime = build_runtime(case_id)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="需要确认用户是否刚修改过密码",
            response="请确认最近是否修改过密码。",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="用户补充了密码变更事实，刷新凭据",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="恢复目标已验证",
            verification_complete=True,
            resolution_evidence=["access=restored"],
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
    )
    thread_id = "thread:user-resume"

    first = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=["simulation:act"],
        )
    )
    second = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=["simulation:act"],
            additional_evidence=[
                {
                    "key": "password_changed_today",
                    "value": True,
                    "epistemic_status": "reported_observation",
                    "source": "user",
                }
            ],
            user_state_update={"urgency": "high", "reading_budget": "minimal"},
        )
    )

    assert first.status is ResolutionStatus.WAITING_FOR_USER
    assert second.status is ResolutionStatus.RESOLVED
    assert second.thread_id == thread_id
    assert second.cycles == 3
    assert second.episode_decisions == 2
    assert [item["tool_id"] for item in second.tool_history].count(
        "simulation_list_actions"
    ) == 1
    assert "user_evidence_updated" in {
        item["event"] for item in second.decision_events
    }
    assert second.progress_events[-4].stage == "evidence_update"
    assert "补充的信息" in second.progress_events[-4].message
    assert any(
        item.get("key") == "password_changed_today"
        for item in planner.calls[1]["resolution_context"]["evidence_handoff"][
            "resolved"
        ]
    )
    progress = run(service.get_progress(thread_id))
    assert progress is not None
    assert progress["status"] == "resolved"
    assert progress["latest_progress"]["stage"] == "resolved"


def test_m2_rejects_a_context_that_has_not_crossed_the_m1_boundary():
    runtime = build_runtime("not-ready")
    context = resolution_context("not-ready")
    context["action_constraints"]["case_entry_allowed"] = False
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=QueuedPlanner(),
    )

    try:
        run(service.resolve(resolution_context=context))
    except ValueError as exc:
        assert "does not allow M2 entry" in str(exc)
    else:
        raise AssertionError("M2 accepted a context that was not ready")


def test_event_arriving_after_planning_replans_before_action_execution():
    case_id = "m2-mid-episode-update"
    thread_id = "thread:mid-episode-update"
    runtime = build_runtime(case_id)
    store = InMemoryCaseEventStore()

    class InjectingPlanner(QueuedPlanner):
        async def plan(self, **kwargs):
            plan = await super().plan(**kwargs)
            if len(self.calls) == 1:
                await store.enqueue(
                    CaseEvent(
                        thread_id=thread_id,
                        case_id=case_id,
                        actor_id="user-1",
                        type=CaseEventType.GOAL_UPDATE,
                        payload={
                            "explicit_goal": "只收集证据，不再执行恢复动作",
                            "success_criteria": ["evidence_collected=true"],
                        },
                    )
                )
            return plan

    planner = InjectingPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="原计划执行恢复",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="目标已改变，停止旧动作",
            response="已停止旧的恢复动作，当前只保留诊断证据。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=planner,
        event_store=store,
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=["simulation:act"],
            actor_id="user-1",
        )
    )

    assert result.status is ResolutionStatus.WAITING_FOR_USER
    assert not any(
        item["tool_id"] == "simulation_execute_action" for item in result.tool_history
    )
    assert "user_goal_updated" in {
        item["event"] for item in result.decision_events
    }
    assert planner.calls[1]["resolution_context"]["goal"]["explicit_goal"] == (
        "只收集证据，不再执行恢复动作"
    )


def test_cancel_event_stops_before_state_changing_action_and_preserves_progress():
    case_id = "m2-cancel-before-action"
    thread_id = "thread:cancel-before-action"
    runtime = build_runtime(case_id)
    store = InMemoryCaseEventStore()

    class CancellingPlanner(QueuedPlanner):
        async def plan(self, **kwargs):
            plan = await super().plan(**kwargs)
            await store.enqueue(
                CaseEvent(
                    thread_id=thread_id,
                    case_id=case_id,
                    actor_id="user-1",
                    type=CaseEventType.CANCEL,
                )
            )
            return plan

    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=CancellingPlanner(
            ResolutionPlan(
                decision=ResolutionDecision.USE_TOOL,
                rationale="准备执行恢复",
                required_capabilities={"scenario_action", "state_change"},
                tool_arguments={"action_id": "refresh_credential"},
            )
        ),
        event_store=store,
    )

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=["simulation:act"],
            actor_id="user-1",
        )
    )

    assert result.status is ResolutionStatus.CANCELLED
    assert result.progress_events[-1].stage == "cancelled"
    assert not any(
        item["tool_id"] == "simulation_execute_action" for item in result.tool_history
    )


def test_collaboration_returns_structured_handoff_without_promoting_hypothesis():
    case_id = "m2-collaboration-handoff"
    runtime = build_runtime(case_id)
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=runtime,
        planner=QueuedPlanner(
            ResolutionPlan(
                decision=ResolutionDecision.COLLABORATE,
                rationale="权限边界",
                provisional_explanations=[
                    ProvisionalExplanation(statement="可能需要管理员权限")
                ],
                collaboration_reason="required permission is unavailable",
                suggested_collaboration_mode="specialist_handoff",
                coordination_structure={
                    "parallelism_known": True,
                    "serial_dependencies": ["obtain accountable approval before action"],
                    "human_authority_required": True,
                },
            )
        ),
    )

    result = run(service.resolve(resolution_context=resolution_context(case_id)))

    assert result.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert result.collaboration_handoff is not None
    assert result.collaboration_handoff.reason == "required permission is unavailable"
    assert result.collaboration_handoff.competing_explanations[0].statement == (
        "可能需要管理员权限"
    )
    assert result.collaboration_handoff.reported_evidence == []
    assert result.collaboration_handoff.confirmed_facts == []
    assert result.collaboration_handoff.goal["current_outcome"] == "恢复内网访问"
    assert result.collaboration_handoff.action_constraints["case_entry_allowed"] is True
    assert result.collaboration_handoff.suggested_collaboration_mode == (
        "specialist_handoff"
    )
    assert result.collaboration_handoff.coordination_structure.parallelism_known
    assert result.collaboration_handoff.coordination_structure.human_authority_required
    assert result.collaboration_handoff.coordination_structure.serial_dependencies == [
        "obtain accountable approval before action"
    ]


def test_m3_advice_resumes_m2_as_advice_not_confirmed_evidence():
    case_id = "m2-resume-after-m3"
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.COLLABORATE,
            rationale="need specialist",
            collaboration_reason="capability boundary",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="need source evidence",
            response="请提供权威状态页结果。",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )
    thread_id = "thread:m3-return"

    first = run(
        service.resolve(
            resolution_context=resolution_context(case_id), thread_id=thread_id
        )
    )
    second = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            collaboration_advice={
                "source": "m3_adaptive_collaboration",
                "summary": "可能需要检查依赖版本",
                "epistemic_status": "advisory_until_m2_verifies",
            },
        )
    )

    assert first.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert second.status is ResolutionStatus.WAITING_FOR_USER
    returned_context = planner.calls[1]["resolution_context"]
    assert returned_context["collaboration_history"][0]["epistemic_status"] == (
        "advisory_until_m2_verifies"
    )
    assert returned_context["evidence_handoff"]["resolved"] == []
    assert planner.calls[1]["decision_event"] == "collaboration_result"


def test_explicit_empty_permissions_revoke_previous_grant_on_resume():
    case_id = "m2-explicit-revoke"
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="wait before action",
            response="请确认是否继续。",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="attempt action after confirmation",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.COLLABORATE,
            rationale="permission was revoked",
            collaboration_reason="required permission is unavailable",
        ),
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )
    thread_id = "thread:explicit-revoke"

    first = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=["simulation:act"],
        )
    )
    second = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
            permissions=[],
        )
    )

    assert first.status is ResolutionStatus.WAITING_FOR_USER
    assert second.status is ResolutionStatus.COLLABORATION_REQUIRED
    assert second.collaboration_reason == (
        "matching tools are blocked by runtime authorization"
    )
    assert all(
        item.get("tool_id") != "simulation_execute_action"
        for item in second.tool_history
    )


def test_step_by_step_initiative_requires_confirmation_before_action():
    case_id = "m2-step-by-step"
    context = resolution_context(case_id)
    context["user_state"] = {"collaboration_preference": "step_by_step"}
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="perform the repair",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        )
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )

    result = run(
        service.resolve(
            resolution_context=context,
            permissions=["simulation:act"],
            confirmation_granted=False,
        )
    )

    assert result.status is ResolutionStatus.WAITING_FOR_USER
    assert not any(
        item.get("tool_id") == "simulation_execute_action"
        for item in result.tool_history
    )
    assert any(
        item.get("mechanism") == "initiative_mode"
        for item in result.control_events
    )


def test_structured_user_questions_are_bounded_by_m1_policy():
    case_id = "m2-question-budget"
    context = resolution_context(case_id)
    context["provisional_resolution_policy"]["max_questions_per_turn"] = 1
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="need one discriminating observation",
            response="这里不能藏额外问题。",
            user_questions=["VPN 当前显示什么错误？", "其他账号能否登录？"],
        )
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )

    result = run(service.resolve(resolution_context=context))

    assert result.status is ResolutionStatus.WAITING_FOR_USER
    assert result.user_questions == ["VPN 当前显示什么错误？"]
    assert result.response == "这里不能藏额外问题。\nVPN 当前显示什么错误？"


def test_waiting_for_user_keeps_result_first_bridge_before_structured_request():
    case_id = "m2-result-first-question"
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="the user's hypothesis lacks supporting evidence",
            response="目前证据不支持 DNS 是主因，691 更接近认证失败。",
            user_questions=["最近是否修改过域密码？"],
        )
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )

    result = run(service.resolve(resolution_context=resolution_context(case_id)))

    assert result.status is ResolutionStatus.WAITING_FOR_USER
    assert result.user_questions == ["最近是否修改过域密码？"]
    assert result.response == (
        "目前证据不支持 DNS 是主因，691 更接近认证失败。\n"
        "最近是否修改过域密码？"
    )


def test_waiting_for_user_drops_question_leaked_into_response_channel():
    case_id = "m2-question-channel-dedup"
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.ASK_USER,
            rationale="need one discriminating observation",
            response=(
                "明白，你的时间很紧。VPN 里的密码也一起改了吗？"
                "（没改的话大概率就是这个原因）"
            ),
            user_questions=["你改完公司密码后，VPN 里的密码有没有同步更新？"],
        )
    )
    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=planner,
    )

    result = run(service.resolve(resolution_context=resolution_context(case_id)))

    assert result.status is ResolutionStatus.WAITING_FOR_USER
    assert result.response == (
        "明白，你的时间很紧。\n"
        "你改完公司密码后，VPN 里的密码有没有同步更新？"
    )


def test_evidence_lifecycle_has_one_current_status_and_keeps_transition_history():
    from core.adaptive_resolution.events import merge_resolution_context

    previous = resolution_context("evidence-transition")
    previous["evidence_handoff"]["open"] = [
        {"key": "vpn_status", "issue_id": "issue-1", "value": "unknown"}
    ]
    current = resolution_context("evidence-transition")
    current["evidence_handoff"]["resolved"] = [
        {"key": "vpn_status", "issue_id": "issue-1", "value": "connected"}
    ]

    merged, *_ = merge_resolution_context(previous, current)
    evidence = merged["evidence_handoff"]

    assert evidence["open"] == []
    assert evidence["resolved"][0]["value"] == "connected"
    assert evidence["status_history"] == [
        {"identity": "issue-1:vpn_status", "from": "open", "to": "resolved"}
    ]


def test_runtime_failure_is_checkpointed_as_terminal_error():
    case_id = "m2-runtime-error"

    class FailingPlanner(QueuedPlanner):
        async def plan(self, **kwargs):
            del kwargs
            raise RuntimeError("provider secret must not reach the API")

    service = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=build_runtime(case_id),
        planner=FailingPlanner(),
    )
    thread_id = "thread:runtime-error"

    result = run(
        service.resolve(
            resolution_context=resolution_context(case_id),
            thread_id=thread_id,
        )
    )

    assert result.status is ResolutionStatus.ERROR
    assert result.error == "M2_RUNTIME_FAILURE"
    assert "provider secret" not in result.response
    assert result.progress_events[-1].stage == "error"
    progress = run(service.get_progress(thread_id))
    assert progress is not None
    assert progress["status"] == "error"
    assert progress["error"] == "M2_RUNTIME_FAILURE"
