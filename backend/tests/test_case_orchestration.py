import asyncio

import pytest

from core.adaptive_resolution import ResolutionResult, ResolutionStatus
from core.case_orchestration import (
    CaseBudgetLimits,
    CaseJob,
    CaseJobKind,
    CaseJobStatus,
    CasePhase,
    DurableCaseJobWorker,
    DurableCaseRunRegistry,
    InMemoryCaseRunRegistry,
    StaleCaseResultError,
)
from core.human_collaboration import EvidenceArtifact, InteractionPreferences, LayeredResponse
from core.multi_agent_collaboration import (
    CollaborationResult,
    CollaborationStatus,
)
from core.problem_formulation.models import (
    CaseStatus,
    FormulationResult,
    InteractionAction,
    PolicyDecision,
    SharedProblemState,
    UserStateEvidence,
)


def run(coro):
    return asyncio.run(coro)


def formulation(*, ready: bool = True) -> FormulationResult:
    state = SharedProblemState(
        case_id="case-1",
        user_id="user-1",
        conv_id="conv-1",
        status=CaseStatus.CASE_READY if ready else CaseStatus.ALIGNING_GOAL,
        turn_count=2,
    )
    return FormulationResult(
        trace_id="m1-trace",
        response="可以开始排查" if ready else "你希望恢复什么？",
        case_ready=ready,
        state=state,
        policy=PolicyDecision(
            action=(InteractionAction.EMIT_CASE_READY if ready else InteractionAction.REFLECT),
            reason="test",
        ),
        resolution_context=(
            {
                "case_id": "case-1",
                "action_constraints": {"case_entry_allowed": True},
            }
            if ready
            else None
        ),
    )


def resolution(status: ResolutionStatus, *, trace_id: str = "m2-trace") -> ResolutionResult:
    return ResolutionResult(
        case_id="case-1",
        thread_id="m2:case-1",
        trace_id=trace_id,
        status=status,
        response=f"M2 {status.value}",
        cycles=2,
    )


def collaboration(
    status: CollaborationStatus = CollaborationStatus.READY_FOR_M2,
) -> CollaborationResult:
    return CollaborationResult(
        collaboration_id="collab-1",
        case_id="case-1",
        m2_thread_id="m2:case-1",
        thread_id="m3:m2:case-1:collab-1",
        status=status,
        mode="independent_review",
        response="请由 M2 验证建议",
        rounds=1,
        agents_recruited=["reviewer"],
        source_case_revision=1,
    )


def test_registry_exposes_complete_m1_m2_m3_m2_lifecycle():
    registry = InMemoryCaseRunRegistry()
    first = run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    assert first.case_id == "case-1"
    assert first.m2_thread_id == "m2:case-1"
    assert first.case_revision == 1

    run(
        registry.record_resolution(
            resolution(ResolutionStatus.COLLABORATION_REQUIRED),
            user_id="user-1",
            tenant_id="local",
        )
    )
    run(
        registry.record_collaboration_started(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            collaboration_id="collab-1",
            thread_id="m3:m2:case-1:collab-1",
            case_revision=1,
        )
    )
    run(
        registry.record_collaboration_completed(
            collaboration(),
            user_id="user-1",
            tenant_id="local",
        )
    )
    final = run(
        registry.record_resolution(
            resolution(ResolutionStatus.RESOLVED, trace_id="m2-resume-trace"),
            user_id="user-1",
            tenant_id="local",
            resumed_from_collaboration=True,
        )
    )

    assert final.phase is CasePhase.RESOLVED
    assert final.status == "resolved"
    assert final.m3["collaboration_id"] == "collab-1"
    assert final.trace_ids == ["m1-trace", "m2-trace", "m2-resume-trace"]
    assert [item.event_type for item in final.events] == [
        "m1_turn_completed",
        "m2_run_completed",
        "m3_started",
        "m3_completed",
        "m2_resumed_after_collaboration",
    ]


def test_m3_evidence_request_is_not_exposed_as_waiting_before_m2_consumes_it():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    run(
        registry.record_collaboration_started(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            collaboration_id="collab-1",
            thread_id="m3:m2:case-1:collab-1",
            case_revision=1,
        )
    )

    snapshot = run(
        registry.record_collaboration_completed(
            collaboration(CollaborationStatus.EVIDENCE_REQUIRED),
            user_id="user-1",
            tenant_id="local",
        )
    )

    assert snapshot.phase is CasePhase.VERIFICATION
    assert snapshot.status == "evidence_required"


def test_registry_keeps_one_case_across_formulation_turns():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(ready=False),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    second = run(
        registry.record_formulation(
            formulation=formulation(ready=True),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    assert second.case_id == "case-1"
    assert len(second.events) == 2
    assert second.m1["turn_count"] == 2
    assert second.case_revision == 2


def test_new_user_turn_fences_older_m1_and_m2_results():
    async def scenario():
        registry = InMemoryCaseRunRegistry()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        first = await registry.record_turn_received(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        second = await registry.record_turn_received(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )

        with pytest.raises(StaleCaseResultError):
            await registry.record_formulation(
                formulation=formulation(),
                user_id="user-1",
                tenant_id="local",
                conv_id="conv-1",
                expected_request_generation=first.request_generation,
            )

        current = await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
            expected_request_generation=second.request_generation,
        )
        with pytest.raises(StaleCaseResultError):
            await registry.record_resolution(
                resolution(ResolutionStatus.RESOLVED),
                user_id="user-1",
                tenant_id="local",
                expected_case_revision=current.case_revision,
                expected_request_generation=first.request_generation,
            )
        return await registry.get("case-1", user_id="user-1", tenant_id="local")

    snapshot = run(scenario())
    assert snapshot is not None
    assert snapshot.phase is CasePhase.RESOLUTION
    assert snapshot.status == "case_ready"


def test_pause_and_cancel_fence_inflight_synchronous_results():
    async def scenario(action):
        registry = InMemoryCaseRunRegistry()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        accepted = await registry.record_turn_received(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        await registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action=action,
        )
        with pytest.raises(StaleCaseResultError):
            await registry.record_resolution(
                resolution(ResolutionStatus.RESOLVED),
                user_id="user-1",
                tenant_id="local",
                expected_request_generation=accepted.request_generation,
            )
        return await registry.get("case-1", user_id="user-1", tenant_id="local")

    paused = run(scenario("pause"))
    cancelled = run(scenario("cancel"))
    assert paused is not None and paused.phase is CasePhase.PAUSED
    assert cancelled is not None and cancelled.phase is CasePhase.CANCELLED


def test_read_only_generation_fence_protects_external_side_effects():
    async def scenario():
        registry = InMemoryCaseRunRegistry()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        first = await registry.record_turn_received(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        await registry.record_turn_received(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        with pytest.raises(StaleCaseResultError):
            await registry.assert_current_request(
                "case-1",
                user_id="user-1",
                tenant_id="local",
                expected_request_generation=first.request_generation,
            )

    run(scenario())


def test_transient_m1_error_cannot_erase_a_verified_terminal_case():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    resolved = run(
        registry.record_resolution(
            resolution(ResolutionStatus.RESOLVED),
            user_id="user-1",
            tenant_id="local",
        )
    )
    failed_turn = formulation(ready=False)
    failed_turn.trace_id = "m1-transient-error"
    failed_turn.m1_model_calls = 1
    failed_turn.state.status = CaseStatus.FORMULATION_ERROR

    preserved = run(
        registry.record_formulation(
            formulation=failed_turn,
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )

    assert preserved.phase is CasePhase.RESOLVED
    assert preserved.status == "resolved"
    assert preserved.case_revision == resolved.case_revision
    assert preserved.m2 == resolved.m2
    assert preserved.events[-1].event_type == "m1_turn_rejected_preserved_prior_state"
    assert preserved.events[-1].status == "formulation_error"


def test_case_budget_and_lifecycle_controls_are_consumed_by_registry():
    registry = InMemoryCaseRunRegistry()
    initial = formulation()
    initial.m1_model_calls = 1
    run(
        registry.record_formulation(
            formulation=initial,
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    configured = run(
        registry.set_budget_limits(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            limits=CaseBudgetLimits(
                wall_time_seconds=3600,
                model_calls=1,
                tool_calls=10,
                collaboration_rounds=2,
                user_waits=1,
            ),
        )
    )
    assert configured.events[-1].event_type == "case_budget_updated"
    assert run(registry.budget_exceeded("case-1", user_id="user-1", tenant_id="local")) == [
        "model_calls"
    ]

    waiting = run(
        registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="wait_for_external",
        )
    )
    assert waiting.phase is CasePhase.WAITING_FOR_EXTERNAL
    assert waiting.budget_usage.user_waits == 1
    assert set(run(registry.budget_exceeded("case-1", user_id="user-1", tenant_id="local"))) == {
        "model_calls",
        "user_waits",
    }

    paused = run(
        registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="pause",
        )
    )
    assert paused.phase is CasePhase.PAUSED
    resumed = run(
        registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="resume",
        )
    )
    assert resumed.phase is CasePhase.RESOLUTION
    cancelled = run(
        registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="cancel",
        )
    )
    assert cancelled.phase is CasePhase.CANCELLED
    reopened = run(
        registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="reopen",
        )
    )
    assert reopened.phase is CasePhase.RESOLUTION
    assert reopened.case_revision == cancelled.case_revision + 1


def test_registry_rejects_cross_actor_access():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    with pytest.raises(PermissionError):
        run(registry.get("case-1", user_id="other", tenant_id="local"))


def test_stale_collaboration_is_visible_and_not_promoted_to_m2():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    snapshot = run(
        registry.record_stale_collaboration(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            collaboration_id="old",
            source_revision=1,
            current_revision=2,
        )
    )
    assert snapshot.events[-1].event_type == "m3_result_discarded"
    assert snapshot.events[-1].status == "stale"
    assert snapshot.m2 == {}


def test_human_required_is_exposed_as_waiting_instead_of_verification():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    snapshot = run(
        registry.record_collaboration_completed(
            collaboration(CollaborationStatus.HUMAN_REQUIRED),
            user_id="user-1",
            tenant_id="local",
        )
    )

    assert snapshot.phase is CasePhase.HUMAN_REQUIRED
    assert snapshot.status == "human_required"


def test_new_user_turn_invalidates_collaboration_source_revision():
    registry = InMemoryCaseRunRegistry()
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )
    run(
        registry.record_collaboration_started(
            case_id="case-1",
            user_id="user-1",
            tenant_id="local",
            collaboration_id="collab-1",
            thread_id="m3:m2:case-1:collab-1",
            case_revision=1,
        )
    )
    run(
        registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
    )

    with pytest.raises(ValueError, match="stale collaboration"):
        run(
            registry.record_collaboration_completed(
                collaboration(),
                user_id="user-1",
                tenant_id="local",
            )
        )


def test_durable_registry_restores_case_events_and_summary(tmp_path):
    async def scenario():
        path = tmp_path / "runtime.sqlite3"
        first = DurableCaseRunRegistry(path)
        await first.setup()
        created = await first.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        await first.record_control_activation(
            created.case_id,
            user_id="user-1",
            tenant_id="local",
            mechanism="case_user_state",
            activated=True,
            behavior_changes=["latency_weight=0.55"],
        )
        await first.close()

        restored = DurableCaseRunRegistry(path)
        await restored.setup()
        snapshot = await restored.get("case-1", user_id="user-1", tenant_id="local")
        events = await restored.list_all_events("case-1")
        await restored.close()
        assert snapshot is not None
        assert snapshot.case_revision == 1
        assert snapshot.context_summary.action_constraints["case_entry_allowed"] is True
        assert events[-1]["event_type"] == "control_mechanism_evaluated"

    run(scenario())


def test_durable_jobs_are_idempotent_and_recover_after_restart(tmp_path):
    async def scenario():
        path = tmp_path / "runtime.sqlite3"
        registry = DurableCaseRunRegistry(path)
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        job = CaseJob(
            case_id="case-1",
            user_id="user-1",
            kind=CaseJobKind.COLLABORATE_THEN_RESUME,
            case_revision=1,
            idempotency_key="case-1:m3:1:1",
        )
        first = await registry.enqueue_job(job)
        duplicate = await registry.enqueue_job(job.model_copy(update={"job_id": "other"}))
        claimed = await registry.claim_next_job("worker-a", lease_seconds=60)
        assert claimed is not None
        assert claimed.status is CaseJobStatus.RUNNING
        await registry.close()

        restored = DurableCaseRunRegistry(path)
        await restored.setup()
        recovered = await restored.get_job(first.job_id)
        await restored.close()
        assert duplicate.job_id == first.job_id
        assert recovered is not None
        assert recovered.status is CaseJobStatus.PENDING

    run(scenario())


def test_new_case_revision_marks_older_durable_work_stale(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "runtime.sqlite3")
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        job = await registry.enqueue_job(
            CaseJob(
                case_id="case-1",
                user_id="user-1",
                kind=CaseJobKind.COLLABORATE_THEN_RESUME,
                case_revision=1,
                idempotency_key="case-1:m3:1:1",
            )
        )
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        changed = await registry.invalidate_stale_jobs("case-1", 2)
        stale = await registry.get_job(job.job_id)
        await registry.close()
        assert changed == 1
        assert stale is not None
        assert stale.status is CaseJobStatus.STALE

    run(scenario())


def test_durable_worker_completes_job_and_pause_resume_controls_queue(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "runtime.sqlite3")
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        job = await registry.enqueue_job(
            CaseJob(
                case_id="case-1",
                user_id="user-1",
                kind=CaseJobKind.COLLABORATE_THEN_RESUME,
                case_revision=1,
                idempotency_key="case-1:m3:1:worker",
            )
        )
        assert await registry.pause_case_jobs("case-1") == 1
        waiting = await registry.get_job(job.job_id)
        assert waiting is not None and waiting.status is CaseJobStatus.WAITING
        assert await registry.resume_case_jobs("case-1") == 1

        async def handler(item):
            return {"handled": item.job_id}

        worker = DurableCaseJobWorker(registry, handler, poll_seconds=0.01)
        await worker.start()
        worker.wake()
        for _ in range(100):
            completed = await registry.get_job(job.job_id)
            if completed is not None and completed.status is CaseJobStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        await worker.stop()
        await registry.close()
        assert completed is not None
        assert completed.status is CaseJobStatus.COMPLETED
        assert completed.result == {"handled": job.job_id}

    run(scenario())


def test_runtime_history_compaction_keeps_current_user_evidence():
    state = SharedProblemState(
        case_id="compact",
        user_id="user",
        conv_id="conv",
        user_state_evidence=[
            UserStateEvidence(
                field="patience",
                value="low" if index == 29 else "unknown",
                evidence_quote=f"turn-{index}",
                turn_id=index,
                is_current=index == 29,
            )
            for index in range(30)
        ],
    )

    state.compact_runtime_history(revision_window=5)

    assert len(state.user_state_evidence) == 6
    assert state.user_state_evidence[-1].is_current is True
    assert state.user_state_evidence[-1].evidence_quote == "turn-29"


def test_invalidation_scope_ignores_identical_turn_but_detects_goal_change():
    async def scenario():
        registry = InMemoryCaseRunRegistry()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        identical = await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        unchanged = await registry.invalidation_scopes(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            revision=identical.case_revision,
        )
        changed_formulation = formulation()
        changed_formulation.state.goal.explicit_goal = "恢复新的目标"
        changed = await registry.record_formulation(
            formulation=changed_formulation,
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        changed_scopes = await registry.invalidation_scopes(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            revision=changed.case_revision,
        )
        assert unchanged == set()
        assert "goal" in changed_scopes

    run(scenario())


def test_m6_preferences_and_evidence_are_case_local_and_durable(tmp_path):
    async def scenario():
        path = tmp_path / "m6.sqlite3"
        registry = DurableCaseRunRegistry(path)
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        preferred = await registry.set_interaction_preferences(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            preferences=InteractionPreferences(
                expression_depth="minimal",
                progress_cadence="every_step",
            ),
        )
        stored = await registry.record_evidence_artifacts(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            artifacts=[
                EvidenceArtifact(
                    kind="log",
                    name="vpn.log",
                    observations=["error=691"],
                )
            ],
        )
        await registry.close()

        restored_registry = DurableCaseRunRegistry(path)
        await restored_registry.setup()
        restored = await restored_registry.get("case-1", user_id="user-1", tenant_id="local")
        await restored_registry.close()
        return preferred, stored, restored

    preferred, stored, restored = run(scenario())
    assert preferred.human_collaboration.expression_depth.value == "minimal"
    assert stored.evidence_artifacts[0].observations == ["error=691"]
    assert restored is not None
    assert restored.human_collaboration.progress_cadence.value == "every_step"
    assert restored.evidence_artifacts[0].name == "vpn.log"


def test_preference_update_preserves_usage_and_explicit_null_clears_override():
    async def scenario():
        registry = InMemoryCaseRunRegistry()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        first = await registry.set_interaction_preferences(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            preferences=InteractionPreferences(expression_depth="minimal"),
        )
        await registry.record_human_effort(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            presentation=LayeredResponse(primary_message="done"),
            asked_questions=1,
        )
        cleared = await registry.set_interaction_preferences(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            preferences=InteractionPreferences(expression_depth=None),
        )
        return first, cleared

    first, cleared = run(scenario())
    assert first.human_collaboration.expression_depth.value == "minimal"
    assert cleared.human_collaboration.preference_overrides.expression_depth is None
    assert cleared.human_collaboration.usage.questions_asked == 1
    assert cleared.human_collaboration.usage.primary_response_chars == 4


def test_latest_event_query_returns_tail_in_chronological_order(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "latest-events.sqlite3")
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        for index in range(100):
            await registry.record_runtime_event(
                "case-1",
                user_id="user-1",
                tenant_id="local",
                event_type=f"event_{index}",
                status="working",
                summary="progress",
            )
        latest = await registry.list_all_events("case-1", limit=5, latest=True)
        await registry.close()
        return latest

    latest = run(scenario())
    assert [item["event_type"] for item in latest] == [
        "event_95",
        "event_96",
        "event_97",
        "event_98",
        "event_99",
    ]


def test_atomic_schedule_and_lease_fence_reject_stale_worker_completion(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "fence.sqlite3")
        await registry.setup()
        snapshot = await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        job = CaseJob(
            case_id="case-1",
            user_id="user-1",
            kind=CaseJobKind.COLLABORATE_THEN_RESUME,
            case_revision=snapshot.case_revision,
            idempotency_key="case-1:atomic:1",
        )
        await registry.schedule_collaboration_job(
            job,
            collaboration_id="collab-atomic",
            thread_id="m3:m2:case-1:collab-atomic",
        )
        claimed = await registry.claim_next_job("worker-a")
        assert claimed is not None
        await registry.pause_case_jobs("case-1")
        committed = await registry.complete_job(
            claimed.job_id,
            "worker-a",
            {"status": "late"},
            lease_generation=claimed.lease_generation,
        )
        events = await registry.list_all_events("case-1")
        persisted = await registry.get_job(job.job_id)
        await registry.close()
        return committed, events, persisted

    committed, events, persisted = run(scenario())
    assert not committed
    assert persisted is not None
    assert persisted.status is CaseJobStatus.WAITING
    assert [item["event_type"] for item in events][-2:] == [
        "m3_started",
        "durable_job_enqueued",
    ]


def test_worker_timeout_is_terminal_and_visible_on_case(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "timeout.sqlite3")
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        job = await registry.enqueue_job(
            CaseJob(
                case_id="case-1",
                user_id="user-1",
                kind=CaseJobKind.COLLABORATE_THEN_RESUME,
                case_revision=1,
                idempotency_key="case-1:timeout:1",
                max_attempts=1,
                execution_timeout_seconds=0.02,
            )
        )

        async def handler(_item):
            await asyncio.sleep(10)
            return {"unreachable": True}

        worker = DurableCaseJobWorker(registry, handler, poll_seconds=0.01)
        await worker.start()
        worker.wake()
        for _ in range(100):
            persisted = await registry.get_job(job.job_id)
            if persisted is not None and persisted.status is CaseJobStatus.FAILED:
                break
            await asyncio.sleep(0.01)
        snapshot = await registry.get("case-1", user_id="user-1", tenant_id="local")
        events = await registry.list_all_events("case-1")
        await worker.stop()
        await registry.close()
        return persisted, snapshot, events

    persisted, snapshot, events = run(scenario())
    assert persisted is not None
    assert persisted.status is CaseJobStatus.FAILED
    assert persisted.last_error == "JOB_EXECUTION_TIMEOUT"
    assert snapshot is not None and snapshot.phase is CasePhase.TIMED_OUT
    assert events[-1]["event_type"] == "durable_job_failed"


def test_failed_snapshot_write_restores_in_memory_authority(tmp_path, monkeypatch):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "rollback.sqlite3")
        await registry.setup()
        before = await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )

        async def fail_write(_snapshot):
            raise RuntimeError("simulated disk failure")

        monkeypatch.setattr(registry, "_persist_snapshot_unlocked", fail_write)
        with pytest.raises(RuntimeError, match="simulated disk failure"):
            await registry.transition(
                "case-1",
                user_id="user-1",
                tenant_id="local",
                action="pause",
            )
        after = await registry.get("case-1", user_id="user-1", tenant_id="local")
        await registry.close()
        return before, after

    before, after = run(scenario())
    assert after is not None
    assert after.phase == before.phase
    assert after.status == before.status
    assert after.request_generation == before.request_generation
    assert len(after.events) == len(before.events)


def test_running_worker_periodically_recovers_a_later_expired_lease(tmp_path):
    async def scenario():
        registry = DurableCaseRunRegistry(tmp_path / "periodic-recovery.sqlite3")
        await registry.setup()
        await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        handled = asyncio.Event()

        async def handler(_item):
            handled.set()
            return {"ok": True}

        job = await registry.enqueue_job(
            CaseJob(
                case_id="case-1",
                user_id="user-1",
                kind=CaseJobKind.COLLABORATE_THEN_RESUME,
                case_revision=1,
                idempotency_key="case-1:later-expiry:1",
            )
        )
        claimed = await registry.claim_next_job("dead-worker", lease_seconds=0.1)
        assert claimed is not None
        worker = DurableCaseJobWorker(registry, handler, poll_seconds=0.01)
        await worker.start()
        worker.wake()
        await asyncio.wait_for(handled.wait(), timeout=2)
        for _ in range(100):
            persisted = await registry.get_job(job.job_id)
            if persisted is not None and persisted.status is CaseJobStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        await worker.stop()
        await registry.close()
        return persisted

    persisted = run(scenario())
    assert persisted is not None
    assert persisted.status is CaseJobStatus.COMPLETED


def test_lifecycle_rejects_resume_from_active_case_and_allows_reopen_terminal():
    async def scenario():
        registry = InMemoryCaseRunRegistry()
        snapshot = await registry.record_formulation(
            formulation=formulation(),
            user_id="user-1",
            tenant_id="local",
            conv_id="conv-1",
        )
        with pytest.raises(ValueError, match="invalid from phase"):
            await registry.transition(
                "case-1",
                user_id="user-1",
                tenant_id="local",
                action="resume",
            )
        cancelled = await registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="cancel",
        )
        reopened = await registry.transition(
            "case-1",
            user_id="user-1",
            tenant_id="local",
            action="reopen",
        )
        return snapshot, cancelled, reopened

    initial, cancelled, reopened = run(scenario())
    assert initial.phase is CasePhase.RESOLUTION
    assert cancelled.phase is CasePhase.CANCELLED
    assert reopened.phase is CasePhase.RESOLUTION
