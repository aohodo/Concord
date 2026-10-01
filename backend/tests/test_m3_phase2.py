import asyncio
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from test_adaptive_collaboration import (
    QueuedCoordinator,
    RecordingWorker,
    complete_synthesis,
    handoff,
    parallel_plan,
)

from core.adaptive_resolution import CoordinationStructure
from core.multi_agent_collaboration import (
    AdaptiveCollaborationService,
    AgentContribution,
    CollaborationAssignment,
    CollaborationStatus,
    CollaborationSynthesis,
    CollaborationTopology,
    ContributionItem,
    ContributionKind,
    RoundDecision,
)
from core.multi_agent_collaboration.usage import (
    ModelUsageMeter,
    begin_usage_meter,
    end_usage_meter,
    record_model_response,
)
from evaluation.m3_collaboration import load_longitudinal_episodes
from evaluation.m3_collaboration.report import build_report, merge_longitudinal_runs
from infrastructure.agent_performance import InMemoryAgentPerformanceStore


def run(awaitable):
    return asyncio.run(awaitable)


def make_service(coordinator, worker=None, store=None):
    return AdaptiveCollaborationService(
        checkpointer=InMemorySaver(),
        performance_store=store or InMemoryAgentPerformanceStore(),
        coordinator=coordinator,
        worker=worker or RecordingWorker(),
    )


def test_three_topologies_are_actually_executable():
    m2_service = make_service(QueuedCoordinator([]))
    m2 = run(
        m2_service.collaborate(
            handoff=handoff(), topology=CollaborationTopology.M2_ONLY
        )
    )
    assert m2.agents_recruited == []
    assert m2.metrics.model_calls == 0

    fixed_service = make_service(
        QueuedCoordinator([], [complete_synthesis()]), RecordingWorker()
    )
    fixed = run(
        fixed_service.collaborate(
            handoff=handoff(), topology=CollaborationTopology.FIXED_THREE
        )
    )
    assert fixed.status is CollaborationStatus.READY_FOR_M2
    assert len(fixed.agents_recruited) == 3
    assert fixed.metrics.worker_calls == 3


def test_known_serial_dependency_blocks_parallel_fanout():
    worker = RecordingWorker()
    constrained = handoff().model_copy(
        update={
            "coordination_structure": CoordinationStructure(
                parallelism_known=True,
                independent_workstreams=["wait for upstream observation"],
                serial_dependencies=["observe upstream before downstream diagnosis"],
                blocking_evidence=["upstream job status"],
            )
        }
    )
    result = run(
        make_service(QueuedCoordinator([parallel_plan()]), worker).collaborate(
            handoff=constrained
        )
    )
    assert result.status is CollaborationStatus.NO_BENEFIT
    assert result.agents_recruited == []
    assert worker.calls == []
    assert "blocks parallel fan-out" in result.plans[0].rationale


def test_explicit_human_authority_boundary_skips_model_planning():
    coordinator = QueuedCoordinator([parallel_plan()])
    constrained = handoff().model_copy(
        update={
            "coordination_structure": CoordinationStructure(
                human_authority_required=True
            )
        }
    )
    result = run(make_service(coordinator).collaborate(handoff=constrained))
    assert result.status is CollaborationStatus.HUMAN_REQUIRED
    assert result.metrics.model_calls == 0
    assert result.metrics.coordinator_calls == 0
    assert coordinator.plan_calls == []


def test_model_usage_meter_records_openai_compatible_tokens_per_context():
    meter = ModelUsageMeter()
    token = begin_usage_meter(meter)
    try:
        record_model_response(
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7)
            )
        )
    finally:
        end_usage_meter(token)
    assert meter.calls == 1
    assert meter.input_tokens == 12
    assert meter.output_tokens == 7
    assert meter.tokens_available is True


def test_worker_timeout_isolated_and_measured():
    service = make_service(
        QueuedCoordinator([parallel_plan()], [complete_synthesis()]),
        RecordingWorker(delay=0.2),
    )
    result = run(service.collaborate(handoff=handoff(), worker_timeout_seconds=0.1))
    assert result.metrics.worker_failures == 2
    assert all(item.error == "COLLABORATOR_FAILURE" for item in result.contributions)


def test_coordinator_timeout_returns_a_bounded_runtime_error():
    class SlowCoordinator(QueuedCoordinator):
        async def plan(self, **kwargs):
            await asyncio.sleep(0.2)
            return await super().plan(**kwargs)

    result = run(
        make_service(SlowCoordinator([parallel_plan()])).collaborate(
            handoff=handoff(),
            coordinator_timeout_seconds=0.1,
        )
    )

    assert result.status is CollaborationStatus.ERROR
    assert result.error == "M3_RUNTIME_FAILURE"
    assert result.metrics.wall_time_ms < 1000


def test_shared_source_agreement_is_not_independent_corroboration():
    class SharedSourceWorker(RecordingWorker):
        async def contribute(self, *, profile, assignment, context):
            return AgentContribution(
                assignment_id=assignment.assignment_id,
                agent_id=profile.agent_id,
                items=[
                    ContributionItem(
                        kind=ContributionKind.HYPOTHESIS,
                        statement=f"bounded view from {profile.agent_id}",
                        basis_refs=["same_operator_report"],
                    )
                ],
            )

    result = run(
        make_service(
            QueuedCoordinator([parallel_plan()], [complete_synthesis()]),
            SharedSourceWorker(),
        ).collaborate(handoff=handoff())
    )
    assert result.metrics.shared_source_groups == 1
    assert result.synthesis.shared_source_warnings


def test_expansion_requires_concrete_complementary_assignment_and_measured_novelty():
    expand_without_assignment = CollaborationSynthesis(
        round_decision=RoundDecision.EXPAND,
        summary="expand without a bounded missing capability",
        new_information_gain=0.9,
    )
    result = run(
        make_service(
            QueuedCoordinator([parallel_plan()], [expand_without_assignment])
        ).collaborate(handoff=handoff())
    )
    assert result.status is CollaborationStatus.EXHAUSTED
    assert result.rounds == 1


def test_second_round_shrinks_to_one_concrete_complementary_gap():
    expansion = CollaborationSynthesis(
        round_decision=RoundDecision.EXPAND,
        summary="one bounded risk gap remains",
        new_information_gain=0.9,
        additional_assignments=[
            CollaborationAssignment(
                agent_id="risk_reviewer",
                objective="review only the remaining irreversible risk",
                required_capabilities={"risk_review"},
            )
        ],
    )
    result = run(
        make_service(
            QueuedCoordinator(
                [parallel_plan()], [expansion, complete_synthesis()]
            )
        ).collaborate(handoff=handoff())
    )
    assert result.status is CollaborationStatus.READY_FOR_M2
    assert result.rounds == 2
    assert result.agents_recruited[-1] == "risk_reviewer"
    assert len(result.plans[-1].assignments) == 1


def test_case_revision_invalidates_stale_collaboration_result():
    original = handoff().model_copy(update={"case_revision": 3})
    result = run(
        make_service(
            QueuedCoordinator([parallel_plan()], [complete_synthesis()])
        ).collaborate(handoff=original)
    )
    assert result.source_case_revision == 3
    assert not AdaptiveCollaborationService.result_is_current(
        result, current_case_revision=4
    )


def test_completed_collaboration_can_be_marked_cancelled_without_losing_case():
    collaboration = make_service(
        QueuedCoordinator([parallel_plan()], [complete_synthesis()])
    )
    result = run(
        collaboration.collaborate(handoff=handoff(), thread_id="m3:cancel-test")
    )
    cancelled = run(collaboration.cancel(result.thread_id))
    assert cancelled["status"] == "cancelled"
    assert cancelled["case_id"] == handoff().case_id


def test_only_explicitly_cited_m2_tool_results_update_reliability():
    store = InMemoryAgentPerformanceStore()
    service = make_service(
        QueuedCoordinator([parallel_plan()], [complete_synthesis()]),
        store=store,
    )
    result = run(service.collaborate(handoff=handoff()))
    cited = result.plans[0].assignments[0]
    uncited = result.plans[0].assignments[1]
    resolution = SimpleNamespace(
        tool_history=[
            {
                "status": "succeeded",
                "category": "observe",
                "evidence": [{"value": "verified"}],
                "latency_ms": 12,
                "collaboration_source_ids": [cited.assignment_id],
            }
        ]
    )
    recorded = run(
        service.record_resolution_outcome(
            collaboration=result,
            resolution_result=resolution,
        )
    )
    snapshot = run(store.snapshot())
    assert len(recorded) == 1
    assert recorded[0].verified_claims == 1
    assert snapshot[cited.agent_id].successes == 1
    assert uncited.agent_id not in snapshot


def test_action_acceptance_does_not_override_failed_independent_verification():
    store = InMemoryAgentPerformanceStore()
    service = make_service(
        QueuedCoordinator([parallel_plan()], [complete_synthesis()]),
        store=store,
    )
    result = run(service.collaborate(handoff=handoff()))
    cited = result.plans[0].assignments[0]
    resolution = SimpleNamespace(
        tool_history=[
            {
                "status": "succeeded",
                "category": "act",
                "changed": True,
                "collaboration_source_ids": [cited.assignment_id],
            },
            {
                "status": "succeeded",
                "category": "verify",
                "evidence": [{"value": "still broken"}],
                "expectation_matched": False,
                "collaboration_source_ids": [cited.assignment_id],
            },
        ]
    )
    recorded = run(
        service.record_resolution_outcome(
            collaboration=result,
            resolution_result=resolution,
        )
    )
    snapshot = run(store.snapshot())
    assert len(recorded) == 1
    assert recorded[0].successful is False
    assert recorded[0].verified_claims == 0
    assert snapshot[cited.agent_id].failures == 1


def test_permission_or_environment_failure_does_not_disprove_agent_advice():
    store = InMemoryAgentPerformanceStore()
    service = make_service(
        QueuedCoordinator([parallel_plan()], [complete_synthesis()]),
        store=store,
    )
    result = run(service.collaborate(handoff=handoff()))
    cited = result.plans[0].assignments[0]
    resolution = SimpleNamespace(
        tool_history=[
            {
                "status": "denied",
                "category": "act",
                "error_code": "PERMISSION_DENIED",
                "collaboration_source_ids": [cited.assignment_id],
            },
            {
                "status": "timed_out",
                "category": "observe",
                "error_code": "TOOL_TIMEOUT",
                "collaboration_source_ids": [cited.assignment_id],
            },
        ]
    )

    recorded = run(
        service.record_resolution_outcome(
            collaboration=result,
            resolution_result=resolution,
        )
    )

    assert recorded == []
    assert run(store.snapshot()) == {}


def test_longitudinal_catalog_has_36_imperfect_multi_turn_episodes():
    episodes = load_longitudinal_episodes()
    assert len(episodes) == 36
    assert sum(item.updated_handoff is not None for item in episodes) == 12
    assert all(item.initial_case.handoff.failed_directions for item in episodes)
    assert any("说反了" in str(item.updated_handoff) for item in episodes if item.updated_handoff)


def test_report_keeps_topology_and_longitudinal_limits_visible():
    summary, report = build_report(
        [
            {
                "topology": "adaptive",
                "quality_boundary_pass": True,
                "model_calls": 2,
                "agents_recruited": ["a"],
                "latency_ms": 10,
                "measured_coordination_cost": 0.2,
            }
        ],
        [{"passed": True, "stale_initial_discarded": True, "feedback_events": 1}],
    )
    assert summary[0]["quality_boundary_passes"] == 1
    assert "not human expert ground truth" in report
    assert "Mid-flight revision invalidation: 1/1" in report


def test_longitudinal_rerun_replaces_only_matching_episode():
    merged = merge_longitudinal_runs(
        [
            [
                {"episode_id": "a", "passed": False},
                {"episode_id": "b", "passed": True},
            ],
            [{"episode_id": "a", "passed": True}],
        ]
    )
    assert merged == [
        {"episode_id": "a", "passed": True},
        {"episode_id": "b", "passed": True},
    ]
