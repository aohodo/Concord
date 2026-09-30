import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from core.adaptive_resolution import (
    AdaptiveResolutionService,
    PredictionAssessment,
    ProvisionalExplanation,
    ResolutionDecision,
    ResolutionPlan,
)
from evaluation.m2_resolution import (
    evaluate_longitudinal_scenario,
    evaluate_scenarios,
    load_longitudinal_catalog,
    load_scenario_catalog,
)
from infrastructure.simulation import ScenarioRuntime, build_simulation_tools
from infrastructure.tool_runtime import AdaptiveToolRuntime


def run(awaitable):
    return asyncio.run(awaitable)


class QueuedPlanner:
    def __init__(self, *plans: ResolutionPlan):
        self.plans = list(plans)

    async def plan(self, **kwargs):
        return self.plans.pop(0)


def test_m2_catalog_contains_heterogeneous_and_adversarial_conditions():
    cases = load_scenario_catalog()

    assert len(cases) == 10
    assert len({item.domain for item in cases}) >= 6
    tags = set().union(*(item.tags for item in cases))
    assert {
        "dirty_report",
        "wrong_hypothesis",
        "delayed_effect",
        "uncertain_commit",
        "permission_boundary",
        "conflicting_sources",
    }.issubset(tags)
    assert all(item.action_rules for item in cases)
    assert all(item.success_criteria for item in cases)


def test_m2_evaluator_runs_the_same_event_driven_loop_and_scores_contracts():
    scenario = load_scenario_catalog()[0]
    scenarios = ScenarioRuntime()
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="执行场景声明的低风险恢复动作",
            provisional_explanations=[
                ProvisionalExplanation(statement="缓存凭据可能没有同步")
            ],
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
            expected_observation="动作被接受并进入独立验证",
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="两个成功目标都已独立验证",
            previous_observation_assessment=PredictionAssessment.MATCH,
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
        evaluate_scenarios(
            [scenario],
            service=service,
            tool_runtime=runtime,
            concurrency=1,
            run_id="unit",
        )
    )[0]

    assert result.passed is True
    assert result.status == "resolved"
    assert result.model_calls == 2
    assert result.verified_targets == ["access", "vpn"]
    assert result.failures == []


def test_longitudinal_catalog_models_rapid_user_updates_and_resumes_case():
    trajectory = load_longitudinal_catalog()[0]
    scenarios = ScenarioRuntime()
    runtime = AdaptiveToolRuntime(scenarios=scenarios)
    for spec in build_simulation_tools(scenarios):
        runtime.registry.register(spec)
    planner = QueuedPlanner(
        ResolutionPlan(
            decision=ResolutionDecision.USE_TOOL,
            rationale="先执行可逆的凭据刷新",
            required_capabilities={"scenario_action", "state_change"},
            tool_arguments={"action_id": "refresh_credential"},
        ),
        ResolutionPlan(
            decision=ResolutionDecision.RESOLVE,
            rationale="吸收用户补充后，恢复结果仍已得到独立验证",
            previous_observation_assessment=PredictionAssessment.MATCH,
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
        evaluate_longitudinal_scenario(
            trajectory,
            service=service,
            tool_runtime=runtime,
            run_id="unit",
        )
    )

    assert result.passed is True
    assert result.status == "resolved"
    assert [item.status for item in result.episodes] == ["paused", "resolved"]
    assert result.bootstrap_tool_calls == 2
    assert "user_evidence_updated" in result.decision_events
    assert "evidence_update" in result.progress_stages
