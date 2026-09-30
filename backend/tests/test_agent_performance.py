import asyncio

from core.multi_agent_collaboration import AgentOutcomeFeedback
from infrastructure.agent_performance import AgentPerformanceStore


def run(awaitable):
    return asyncio.run(awaitable)


def test_performance_store_persists_only_downstream_outcomes(tmp_path):
    path = tmp_path / "performance.sqlite3"
    store = AgentPerformanceStore(path)
    first = run(
        store.record(
            AgentOutcomeFeedback(
                agent_id="risk_reviewer", successful=True, latency_ms=120
            )
        )
    )
    run(
        store.record(
            AgentOutcomeFeedback(
                agent_id="risk_reviewer", successful=False, latency_ms=80
            )
        )
    )

    restarted = AgentPerformanceStore(path)
    observed = run(restarted.snapshot())["risk_reviewer"]

    assert first.successes == 1
    assert observed.successes == 1
    assert observed.failures == 1
    assert observed.samples == 2
    assert observed.average_latency_ms == 100
    assert observed.reliability == 0.5


def test_contextual_outcome_is_idempotent_and_keeps_verified_progress(tmp_path):
    path = tmp_path / "performance.sqlite3"
    store = AgentPerformanceStore(path)
    feedback = AgentOutcomeFeedback(
        agent_id="risk_reviewer",
        successful=True,
        latency_ms=40,
        collaboration_id="collab-1",
        assignment_id="assignment-1",
        capabilities={"risk_review"},
        task_type="independent_review",
        verified_claims=1,
        progress_delta=1.0,
    )

    run(store.record(feedback))
    run(store.record(feedback))

    overall = run(store.snapshot())["risk_reviewer"]
    contextual = run(store.context_snapshot())[
        "risk_reviewer|risk_review|independent_review"
    ]
    assert overall.samples == 1
    assert overall.verified_progress_total == 1.0
    assert contextual.samples == 1
    assert contextual.successes == 1
