import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from core.adaptive_resolution import CollaborationHandoff, CollaborationMode
from core.multi_agent_collaboration import (
    AdaptiveCollaborationService,
    AgentContribution,
    AgentDirectory,
    AgentPerformance,
    AgentProfile,
    CollaborationAssignment,
    CollaborationConflict,
    CollaborationDecision,
    CollaborationPlan,
    CollaborationStatus,
    CollaborationSynthesis,
    ConflictType,
    ContextPolicy,
    ContributionItem,
    ContributionKind,
    RoundDecision,
)
from infrastructure.agent_performance import InMemoryAgentPerformanceStore


def run(awaitable):
    return asyncio.run(awaitable)


def handoff() -> CollaborationHandoff:
    return CollaborationHandoff(
        case_id="case-m3",
        thread_id="m2:case-m3",
        reason="two independent directions remain under deadline",
        goal={
            "current_outcome": "restore service",
            "success_criteria": [{"value": "health=ok"}],
        },
        action_constraints={"restricted_operation_modes": ["production_write"]},
        reported_evidence=[
            {
                "key": "user_report",
                "value": "service unavailable",
                "epistemic_status": "reported_observation",
            }
        ],
        observed_evidence=[{"tool_id": "health", "observation": "degraded"}],
        competing_explanations=[{"statement": "dependency may be stale"}],
        suggested_collaboration_mode="parallel_workers",
    )


class QueuedCoordinator:
    def __init__(self, plans, syntheses=()):
        self.plans = list(plans)
        self.syntheses = list(syntheses)
        self.plan_calls = []
        self.synthesis_calls = []

    async def plan(self, **kwargs):
        self.plan_calls.append(kwargs)
        return self.plans.pop(0)

    async def synthesize(self, **kwargs):
        self.synthesis_calls.append(kwargs)
        return self.syntheses.pop(0)


class RecordingWorker:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []

    async def contribute(self, *, profile, assignment, context):
        self.calls.append((profile, assignment, context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return AgentContribution(
            assignment_id=assignment.assignment_id,
            agent_id=profile.agent_id,
            items=[
                ContributionItem(
                    kind=ContributionKind.HYPOTHESIS,
                    statement=f"{profile.agent_id} candidate",
                    basis_refs=["observed_evidence[0]"],
                )
            ],
            novel_information=[f"lead:{profile.agent_id}"],
        )


def service(coordinator, worker=None, directory=None):
    return AdaptiveCollaborationService(
        checkpointer=InMemorySaver(),
        performance_store=InMemoryAgentPerformanceStore(),
        coordinator=coordinator,
        worker=worker or RecordingWorker(),
        directory=directory,
    )


def parallel_plan(*, gain=0.8, cost=0.2):
    return CollaborationPlan(
        decision=CollaborationDecision.RECRUIT,
        mode=CollaborationMode.PARALLEL_WORKERS,
        rationale="independent directions",
        assignments=[
            CollaborationAssignment(
                agent_id="domain_specialist",
                objective="inspect dependency failure",
                required_capabilities={"specialized_diagnosis"},
            ),
            CollaborationAssignment(
                agent_id="evidence_auditor",
                objective="audit source reliability",
                required_capabilities={"source_reliability"},
            ),
        ],
        expected_information_gain=gain,
        estimated_coordination_cost=cost,
    )


def test_recruit_plan_requires_an_executable_assignment():
    with pytest.raises(ValidationError, match="requires at least one assignment"):
        CollaborationPlan(
            decision=CollaborationDecision.RECRUIT,
            mode=CollaborationMode.DIVERSE_EXPLORATION,
            rationale="search is stalled",
            assignments=[],
            expected_information_gain=0.8,
            estimated_coordination_cost=0.2,
        )


def complete_synthesis(*conflicts):
    return CollaborationSynthesis(
        round_decision=RoundDecision.COMPLETE,
        summary="two bounded leads are ready for M2 verification",
        preserved_hypotheses=["dependency may be stale"],
        evidence_requests=[],
        conflicts=list(conflicts),
        new_information_gain=0.6,
        residual_risk=0.3,
    )


def test_low_marginal_gain_does_not_recruit_agents():
    worker = RecordingWorker()
    coordinator = QueuedCoordinator([parallel_plan(gain=0.2, cost=0.2)])

    result = run(service(coordinator, worker).collaborate(handoff=handoff()))

    assert result.status is CollaborationStatus.NO_BENEFIT
    assert result.agents_recruited == []
    assert worker.calls == []


def test_parallel_mode_requires_multiple_distinct_workers():
    worker = RecordingWorker()
    plan = CollaborationPlan(
        decision="recruit",
        mode="parallel_workers",
        rationale="incorrect one-worker parallel plan",
        assignments=[
            CollaborationAssignment(
                agent_id="domain_specialist",
                objective="only direction",
                required_capabilities={"specialized_diagnosis"},
            )
        ],
        expected_information_gain=0.9,
        estimated_coordination_cost=0.01,
    )

    result = run(
        service(QueuedCoordinator([plan]), worker).collaborate(handoff=handoff())
    )

    assert result.status is CollaborationStatus.NO_BENEFIT
    assert worker.calls == []


def test_independent_tasks_are_dispatched_and_returned_to_m2():
    worker = RecordingWorker(delay=0.01)
    coordinator = QueuedCoordinator(
        [parallel_plan()], [complete_synthesis()]
    )

    result = run(service(coordinator, worker).collaborate(handoff=handoff()))

    assert result.status is CollaborationStatus.READY_FOR_M2
    assert set(result.agents_recruited) == {"domain_specialist", "evidence_auditor"}
    assert result.resume_packet is not None
    assert result.resume_packet.epistemic_status == "advisory_until_m2_verifies"
    assert len(worker.calls) == 2


def test_fact_conflict_requires_new_evidence_instead_of_voting():
    conflict = CollaborationConflict(
        conflict_type=ConflictType.FACT,
        statements=["version=7", "version=8"],
        distinguishing_evidence=["read version from authoritative provider"],
    )
    coordinator = QueuedCoordinator(
        [parallel_plan()], [complete_synthesis(conflict)]
    )

    result = run(service(coordinator).collaborate(handoff=handoff()))

    assert result.status is CollaborationStatus.EVIDENCE_REQUIRED
    normalized = result.synthesis.conflicts[0]
    assert normalized.resolution_strategy == "reacquire_authoritative_evidence"
    assert normalized.blocks_action is True
    assert "read version from authoritative provider" in (
        result.resume_packet.evidence_requests
    )


def test_goal_conflict_returns_to_user_goal_alignment():
    conflict = CollaborationConflict(
        conflict_type=ConflictType.GOAL,
        statements=["restore now", "investigate root cause first"],
    )
    coordinator = QueuedCoordinator(
        [parallel_plan()], [complete_synthesis(conflict)]
    )

    result = run(service(coordinator).collaborate(handoff=handoff()))

    assert result.status is CollaborationStatus.GOAL_ALIGNMENT_REQUIRED
    assert result.synthesis.conflicts[0].resolution_strategy == (
        "return_to_user_goal_and_hard_constraints"
    )


def test_independent_review_is_blinded_to_current_hypotheses():
    worker = RecordingWorker()
    plan = CollaborationPlan(
        decision="recruit",
        mode="independent_review",
        rationale="high risk",
        assignments=[
            CollaborationAssignment(
                agent_id="risk_reviewer",
                objective="review rollback safety",
                required_capabilities={"risk_review"},
                context_policy=ContextPolicy.FULL_HANDOFF,
            )
        ],
        expected_information_gain=0.7,
        estimated_coordination_cost=0.1,
    )
    coordinator = QueuedCoordinator([plan], [complete_synthesis()])

    result = run(service(coordinator, worker).collaborate(handoff=handoff()))

    assert result.status is CollaborationStatus.READY_FOR_M2
    _, assignment, context = worker.calls[0]
    assert assignment.independent is True
    assert assignment.context_policy is ContextPolicy.FACTS_ONLY
    assert "competing_explanations" not in context
    assert context["reported_evidence"][0]["epistemic_status"] == (
        "reported_observation"
    )


def test_directory_routes_by_capability_and_verified_reliability():
    directory = AgentDirectory(
        [
            AgentProfile(
                agent_id="a",
                role="A",
                mission="A",
                capabilities={"diagnose"},
                perspective="A",
                base_coordination_cost=0.1,
            ),
            AgentProfile(
                agent_id="b",
                role="B",
                mission="B",
                capabilities={"diagnose"},
                perspective="B",
                base_coordination_cost=0.1,
            ),
        ]
    )
    performance = {
        "a": AgentPerformance(agent_id="a", successes=1, failures=4, samples=5),
        "b": AgentPerformance(agent_id="b", successes=8, failures=1, samples=9),
    }

    selected = directory.select({"diagnose"}, performance)

    assert selected is not None
    assert selected.agent_id == "b"


def test_directory_prefers_capability_specific_verified_outcomes():
    directory = AgentDirectory(
        [
            AgentProfile(
                agent_id="a",
                role="A",
                mission="A",
                capabilities={"diagnose"},
                perspective="A",
                base_coordination_cost=0.1,
            ),
            AgentProfile(
                agent_id="b",
                role="B",
                mission="B",
                capabilities={"diagnose"},
                perspective="B",
                base_coordination_cost=0.1,
            ),
        ]
    )
    global_performance = {
        "a": AgentPerformance(agent_id="a", successes=8, failures=1, samples=9),
        "b": AgentPerformance(agent_id="b", successes=2, failures=2, samples=4),
    }
    contextual = {
        "a|diagnose|specialist_handoff": AgentPerformance(
            agent_id="a", successes=0, failures=4, samples=4
        ),
        "b|diagnose|specialist_handoff": AgentPerformance(
            agent_id="b", successes=5, failures=0, samples=5
        ),
    }

    selected = directory.select(
        {"diagnose"},
        global_performance,
        contextual_performance=contextual,
        task_type="specialist_handoff",
    )

    assert selected is not None
    assert selected.agent_id == "b"


def test_progress_is_checkpointed_while_collaborators_run():
    async def scenario():
        coordinator = QueuedCoordinator(
            [parallel_plan()], [complete_synthesis()]
        )
        collaboration = service(coordinator, RecordingWorker(delay=0.1))
        task = asyncio.create_task(
            collaboration.collaborate(
                handoff=handoff(),
                thread_id="m3:progress-test",
                collaboration_id="progress-test",
            )
        )
        await asyncio.sleep(0.04)
        progress = await collaboration.get_progress("m3:progress-test")
        result = await task
        return progress, result

    progress, result = run(scenario())

    assert progress is not None
    assert progress["status"] == "running"
    assert any(
        event["stage"] == "agents_recruited"
        for event in progress["progress_events"]
    )
    assert result.status is CollaborationStatus.READY_FOR_M2
    assert result.progress_events[-1].status == "ready_for_m2"


def test_worker_cannot_promote_its_inference_to_observation():
    class MislabelingWorker(RecordingWorker):
        async def contribute(self, *, profile, assignment, context):
            self.calls.append((profile, assignment, context))
            return AgentContribution(
                assignment_id=assignment.assignment_id,
                agent_id=profile.agent_id,
                items=[
                    ContributionItem(
                        kind=ContributionKind.OBSERVATION,
                        statement="I infer this is definitely the root cause",
                    )
                ],
            )

    worker = MislabelingWorker()
    coordinator = QueuedCoordinator([parallel_plan()], [complete_synthesis()])

    result = run(service(coordinator, worker).collaborate(handoff=handoff()))

    assert all(
        item.kind is ContributionKind.HYPOTHESIS
        for contribution in result.contributions
        for item in contribution.items
    )
    assert all(
        "downgraded to hypothesis" in contribution.limitations[-1]
        for contribution in result.contributions
    )
