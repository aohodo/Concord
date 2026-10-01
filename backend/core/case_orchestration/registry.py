"""M4 case identity and observable lifecycle registry.

The first implementation is deliberately in-memory.  It establishes one Case
contract and makes the completed asynchronous M3→M2 result observable.  M5
will replace the storage adapter with a durable job/case store without changing
the API-facing contract.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Any

from core.adaptive_resolution import ResolutionResult, ResolutionStatus
from core.human_collaboration import (
    EvidenceArtifact,
    InteractionPreferences,
    LayeredResponse,
    derive_human_collaboration_state,
)
from core.multi_agent_collaboration import CollaborationResult, CollaborationStatus
from core.problem_formulation import FormulationResult

from .models import (
    CaseBudgetLimits,
    CaseContextSummary,
    CaseLifecycleEvent,
    CasePhase,
    CaseRunSnapshot,
)


def m1_thread_id(user_id: str, conv_id: str) -> str:
    return f"{user_id}:{conv_id}"


def m2_thread_id(case_id: str) -> str:
    return f"m2:{case_id}"


def m3_thread_id(resolution_thread_id: str, collaboration_id: str) -> str:
    return f"m3:{resolution_thread_id}:{collaboration_id}"


class StaleCaseResultError(RuntimeError):
    """Raised when an older synchronous run attempts to overwrite newer Case state."""


def _phase_for_resolution(status: ResolutionStatus) -> CasePhase:
    return {
        ResolutionStatus.INVESTIGATING: CasePhase.RESOLUTION,
        ResolutionStatus.WAITING_FOR_USER: CasePhase.WAITING_FOR_USER,
        ResolutionStatus.RESOLVED: CasePhase.RESOLVED,
        ResolutionStatus.COLLABORATION_REQUIRED: CasePhase.COLLABORATION,
        ResolutionStatus.PAUSED: CasePhase.PAUSED,
        ResolutionStatus.EXHAUSTED: CasePhase.PAUSED,
        ResolutionStatus.ERROR: CasePhase.ERROR,
        ResolutionStatus.CANCELLED: CasePhase.CANCELLED,
    }[status]


class InMemoryCaseRunRegistry:
    """Ownership-aware M4 registry with immutable copies at its boundary."""

    def __init__(self, *, max_events: int = 200) -> None:
        self._cases: dict[str, CaseRunSnapshot] = {}
        self._case_by_conversation: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()
        self._max_events = max(20, max_events)

    @property
    def storage_backend(self) -> str:
        return "in_memory"

    async def setup(self) -> None:
        return

    async def close(self) -> None:
        return None

    async def _after_mutation(self, snapshot: CaseRunSnapshot) -> None:
        """Persistence hook. Called while the registry mutation lock is held."""

        return

    async def record_turn_received(
        self,
        *,
        case_id: str,
        user_id: str,
        tenant_id: str,
        conv_id: str,
    ) -> CaseRunSnapshot:
        """Persist a fast acknowledgement before any model call starts."""

        async with self._lock:
            snapshot = self._cases.get(case_id)
            if snapshot is None:
                snapshot = CaseRunSnapshot(
                    case_id=case_id,
                    conv_id=conv_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    m1_thread_id=m1_thread_id(user_id, conv_id),
                    m2_thread_id=m2_thread_id(case_id),
                    phase=CasePhase.FORMULATION,
                    status="turn_received",
                )
                self._cases[case_id] = snapshot
                self._case_by_conversation[(tenant_id, user_id, conv_id)] = case_id
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            if snapshot.conv_id != conv_id:
                raise ValueError("case_id belongs to a different conversation")
            snapshot.request_generation += 1
            self._append_event(
                snapshot,
                phase=CasePhase.FORMULATION,
                event_type="user_turn_received",
                status="accepted",
                summary="已收到补充信息，正在更新 Case。",
                data={"request_generation": snapshot.request_generation},
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_formulation(
        self,
        *,
        formulation: FormulationResult,
        user_id: str,
        tenant_id: str,
        conv_id: str,
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        case_id = formulation.state.case_id
        async with self._lock:
            snapshot = self._cases.get(case_id)
            if snapshot is None:
                snapshot = CaseRunSnapshot(
                    case_id=case_id,
                    conv_id=conv_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    m1_thread_id=m1_thread_id(user_id, conv_id),
                    m2_thread_id=m2_thread_id(case_id),
                )
                self._cases[case_id] = snapshot
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            if snapshot.conv_id != conv_id:
                raise ValueError("case_id belongs to a different conversation")
            self._assert_current_request(snapshot, expected_request_generation)
            if formulation.state.status.value == "formulation_error" and snapshot.phase in {
                CasePhase.RESOLVED,
                CasePhase.HUMAN_REQUIRED,
            }:
                # A transient structured-output failure is not new Case evidence.
                # Preserve the last verified terminal state and keep the failed
                # turn observable so the next user turn can recover naturally.
                snapshot.budget_usage.model_calls += formulation.m1_model_calls
                self._append_trace(snapshot, formulation.trace_id)
                self._append_event(
                    snapshot,
                    phase=CasePhase.FORMULATION,
                    event_type="m1_turn_rejected_preserved_prior_state",
                    status="formulation_error",
                    trace_id=formulation.trace_id,
                    summary=(
                        "M1 could not reliably interpret the new turn; the last "
                        "verified Case state was preserved"
                    ),
                    data={
                        "preserved_phase": snapshot.phase.value,
                        "preserved_status": snapshot.status,
                        "case_revision": snapshot.case_revision,
                    },
                )
                self._touch(snapshot)
                await self._after_mutation(snapshot)
                return snapshot.model_copy(deep=True)
            previous_problem = dict(snapshot.m1.get("problem_state", {}))
            previous_context = dict(snapshot.m1.get("resolution_context") or {})
            previous_goal = dict(previous_problem.get("goal", {}))
            current_goal = formulation.state.goal.model_dump(mode="json")
            snapshot.case_revision += 1
            invalidation_scopes: list[str] = []
            if previous_goal and current_goal != previous_goal:
                snapshot.goal_revision += 1
                invalidation_scopes.append("goal")
            current_problem = formulation.state.model_dump(mode="json")
            evidence_fields = (
                "situation",
                "reported_issues",
                "claims",
                "action_result_ledger",
                "missing_evidence",
                "contradictions",
            )
            if previous_problem and any(
                previous_problem.get(field) != current_problem.get(field)
                for field in evidence_fields
            ):
                invalidation_scopes.append("evidence")
            current_context = formulation.resolution_context or {}
            current_context = copy.deepcopy(current_context)
            overrides = snapshot.human_collaboration.preference_overrides
            current_user_state = dict(current_context.get("user_state") or {})
            if overrides.expression_depth is not None:
                current_user_state["explanation_preference"] = {
                    "minimal": "minimal",
                    "guided": "on_demand",
                    "expert": "detailed",
                }[overrides.expression_depth.value]
            if overrides.initiative_mode is not None:
                current_user_state["collaboration_preference"] = overrides.initiative_mode.value
            if overrides.progress_cadence is not None:
                current_user_state["progress_preference"] = overrides.progress_cadence.value
            if overrides.result_first is not None:
                current_user_state["delivery_preference"] = (
                    "result_first" if overrides.result_first else "balanced"
                )
            current_context["user_state"] = current_user_state
            current_context["user_state_snapshot"] = copy.deepcopy(current_user_state)
            if previous_context and previous_context.get(
                "action_constraints"
            ) != current_context.get("action_constraints"):
                invalidation_scopes.append("constraints")
            if invalidation_scopes:
                snapshot.invalidated_artifacts.append(
                    {
                        "artifact_types": ["m2_plan", "m3_advice", "pending_job"],
                        "reason": "case_dependencies_changed",
                        "dependency_scopes": invalidation_scopes,
                        "invalidated_at_revision": snapshot.case_revision,
                    }
                )
            snapshot.phase = (
                CasePhase.RESOLUTION if formulation.case_ready else CasePhase.FORMULATION
            )
            snapshot.status = formulation.state.status.value
            snapshot.response = formulation.response
            snapshot.m1 = {
                "trace_id": formulation.trace_id,
                "case_ready": formulation.case_ready,
                "status": formulation.state.status.value,
                "turn_count": formulation.state.turn_count,
                "evidence_sufficiency": formulation.state.evidence_sufficiency.value,
                "interaction_action": formulation.policy.action.value,
                "problem_state": current_problem,
                "resolution_context": formulation.resolution_context,
            }
            snapshot.m1["resolution_context"] = current_context
            self._append_trace(snapshot, formulation.trace_id)
            snapshot.budget_usage.model_calls += formulation.m1_model_calls
            snapshot.context_summary = self._summary_from_formulation(formulation)
            snapshot.context_summary.user_state = copy.deepcopy(current_user_state)
            previous_usage = snapshot.human_collaboration.usage.model_copy(deep=True)
            snapshot.human_collaboration = derive_human_collaboration_state(
                current_context,
                overrides=overrides,
            )
            snapshot.human_collaboration.usage = previous_usage
            snapshot.human_collaboration.usage.goal_corrections = max(
                previous_usage.goal_corrections,
                len(formulation.state.goal.revision_history),
            )
            self._append_event(
                snapshot,
                phase=CasePhase.FORMULATION,
                event_type="m1_turn_completed",
                status=formulation.state.status.value,
                trace_id=formulation.trace_id,
                summary=(
                    "M1 produced a resolution context"
                    if formulation.case_ready
                    else "M1 requires another user turn"
                ),
                data={
                    "turn_count": formulation.state.turn_count,
                    "case_ready": formulation.case_ready,
                    "interaction_action": formulation.policy.action.value,
                },
            )
            self._case_by_conversation[(tenant_id, user_id, conv_id)] = case_id
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def invalidation_scopes(
        self, case_id: str, *, user_id: str, tenant_id: str, revision: int
    ) -> set[str]:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            return {
                str(scope)
                for item in snapshot.invalidated_artifacts
                if item.get("invalidated_at_revision") == revision
                for scope in item.get("dependency_scopes", [])
            }

    async def record_resolution(
        self,
        result: ResolutionResult,
        *,
        user_id: str,
        tenant_id: str,
        resumed_from_collaboration: bool = False,
        expected_case_revision: int | None = None,
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(result.case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            if (
                expected_case_revision is not None
                and snapshot.case_revision != expected_case_revision
            ):
                raise StaleCaseResultError(
                    "M2 result belongs to an older Case revision"
                )
            if snapshot.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
                raise StaleCaseResultError(
                    f"M2 result cannot update a {snapshot.phase.value} Case"
                )
            previous_tool_calls = len((snapshot.m2 or {}).get("tool_history", []))
            snapshot.m2_thread_id = result.thread_id
            snapshot.phase = _phase_for_resolution(result.status)
            snapshot.status = result.status.value
            snapshot.response = result.response
            snapshot.m2 = result.model_dump(mode="json")
            snapshot.budget_usage.model_calls += result.episode_decisions
            # A resumed M2 result carries the cumulative tool history. Count only
            # calls added by this episode or the same work would consume budget
            # again after every collaboration round.
            snapshot.budget_usage.tool_calls += max(
                0, len(result.tool_history) - previous_tool_calls
            )
            snapshot.context_summary.provisional_explanations = [
                item.model_dump(mode="json") for item in result.provisional_explanations
            ]
            snapshot.context_summary.salient_failures = [
                item
                for item in result.tool_history
                if item.get("status") in {"failed", "timed_out", "denied", "conflict"}
            ][-20:]
            snapshot.context_summary.updated_at = datetime.now(UTC).isoformat()
            self._append_trace(snapshot, result.trace_id)
            self._append_event(
                snapshot,
                phase=(
                    CasePhase.VERIFICATION if resumed_from_collaboration else CasePhase.RESOLUTION
                ),
                event_type=(
                    "m2_resumed_after_collaboration"
                    if resumed_from_collaboration
                    else "m2_run_completed"
                ),
                status=result.status.value,
                trace_id=result.trace_id,
                summary=result.response,
                data={
                    "cycles": result.cycles,
                    "episode_decisions": result.episode_decisions,
                    "tool_calls": len(result.tool_history),
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_collaboration_started(
        self,
        *,
        case_id: str,
        user_id: str,
        tenant_id: str,
        collaboration_id: str,
        thread_id: str,
        case_revision: int,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            if case_revision != snapshot.case_revision:
                raise ValueError("collaboration source revision does not match the current Case")
            snapshot.phase = CasePhase.COLLABORATION
            snapshot.status = "running"
            snapshot.collaboration_id = collaboration_id
            snapshot.m3_thread_id = thread_id
            snapshot.m3 = {
                "collaboration_id": collaboration_id,
                "thread_id": thread_id,
                "status": "running",
                "source_case_revision": case_revision,
            }
            self._append_event(
                snapshot,
                phase=CasePhase.COLLABORATION,
                event_type="m3_started",
                status="running",
                summary="M3 collaboration started",
                data={
                    "collaboration_id": collaboration_id,
                    "thread_id": thread_id,
                    "source_case_revision": case_revision,
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_collaboration_completed(
        self,
        result: CollaborationResult,
        *,
        user_id: str,
        tenant_id: str,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(result.case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            if result.source_case_revision != snapshot.case_revision:
                raise ValueError("stale collaboration result cannot update the Case")
            snapshot.phase = {
                CollaborationStatus.READY_FOR_M2: CasePhase.VERIFICATION,
                # M3 evidence requests are advisory until the Case Owner consumes
                # the resume packet.  They may be satisfiable by a read-only tool,
                # so exposing WAITING_FOR_USER here creates a false terminal window.
                CollaborationStatus.EVIDENCE_REQUIRED: CasePhase.VERIFICATION,
                CollaborationStatus.GOAL_ALIGNMENT_REQUIRED: CasePhase.WAITING_FOR_USER,
                CollaborationStatus.HUMAN_REQUIRED: CasePhase.HUMAN_REQUIRED,
                CollaborationStatus.NO_BENEFIT: CasePhase.PAUSED,
                CollaborationStatus.EXHAUSTED: CasePhase.PAUSED,
                CollaborationStatus.CANCELLED: CasePhase.CANCELLED,
                CollaborationStatus.ERROR: CasePhase.ERROR,
                CollaborationStatus.RUNNING: CasePhase.COLLABORATION,
            }[result.status]
            snapshot.status = result.status.value
            snapshot.collaboration_id = result.collaboration_id
            snapshot.m3_thread_id = result.thread_id
            snapshot.m3 = result.model_dump(mode="json")
            snapshot.budget_usage.collaboration_rounds += result.rounds
            snapshot.budget_usage.model_calls += result.metrics.model_calls
            self._append_event(
                snapshot,
                phase=CasePhase.COLLABORATION,
                event_type="m3_completed",
                status=result.status.value,
                summary="M3 returned evidence or advice to M2",
                data={
                    "collaboration_id": result.collaboration_id,
                    "mode": result.mode.value,
                    "agents_recruited": result.agents_recruited,
                    "rounds": result.rounds,
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_stale_collaboration(
        self,
        *,
        case_id: str,
        user_id: str,
        tenant_id: str,
        collaboration_id: str,
        source_revision: int,
        current_revision: int,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._append_event(
                snapshot,
                phase=CasePhase.COLLABORATION,
                event_type="m3_result_discarded",
                status="stale",
                summary="A stale collaboration result was not applied to M2",
                data={
                    "collaboration_id": collaboration_id,
                    "source_revision": source_revision,
                    "current_revision": current_revision,
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_error(
        self,
        *,
        case_id: str,
        user_id: str,
        tenant_id: str,
        phase: CasePhase,
        error_code: str,
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            snapshot.phase = CasePhase.ERROR
            snapshot.status = error_code
            self._append_event(
                snapshot,
                phase=phase,
                event_type="stage_error",
                status=error_code,
                summary="A stage failed; the current Case state was retained",
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def assert_current_request(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        expected_request_generation: int,
    ) -> CaseRunSnapshot:
        """Read-only generation fence for side effects outside the registry."""

        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            return snapshot.model_copy(deep=True)

    async def transition(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        action: str,
        reason: str = "",
    ) -> CaseRunSnapshot:
        """Apply a user-visible lifecycle transition without deleting history."""

        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            action = action.strip().lower()
            if action == "pause" and snapshot.phase is CasePhase.PAUSED:
                return snapshot.model_copy(deep=True)
            if action == "cancel" and snapshot.phase is CasePhase.CANCELLED:
                return snapshot.model_copy(deep=True)
            allowed_sources = {
                "pause": {
                    CasePhase.FORMULATION,
                    CasePhase.RESOLUTION,
                    CasePhase.COLLABORATION,
                    CasePhase.VERIFICATION,
                    CasePhase.WAITING_FOR_USER,
                    CasePhase.WAITING_FOR_EXTERNAL,
                },
                "resume": {CasePhase.PAUSED},
                "cancel": {
                    CasePhase.FORMULATION,
                    CasePhase.RESOLUTION,
                    CasePhase.COLLABORATION,
                    CasePhase.VERIFICATION,
                    CasePhase.WAITING_FOR_USER,
                    CasePhase.WAITING_FOR_EXTERNAL,
                    CasePhase.PAUSED,
                },
                "wait_for_external": {
                    CasePhase.RESOLUTION,
                    CasePhase.COLLABORATION,
                    CasePhase.VERIFICATION,
                },
                "wait_for_user": {
                    CasePhase.FORMULATION,
                    CasePhase.RESOLUTION,
                    CasePhase.COLLABORATION,
                    CasePhase.VERIFICATION,
                },
                "reopen": {
                    CasePhase.RESOLVED,
                    CasePhase.CANCELLED,
                    CasePhase.HUMAN_REQUIRED,
                    CasePhase.TIMED_OUT,
                    CasePhase.ERROR,
                },
            }
            if action not in allowed_sources:
                raise ValueError(f"unsupported case action: {action}")
            if snapshot.phase not in allowed_sources[action]:
                raise ValueError(
                    f"case action {action} is invalid from phase {snapshot.phase.value}"
                )
            if action == "pause":
                snapshot.request_generation += 1
                snapshot.phase, snapshot.status = CasePhase.PAUSED, "paused"
            elif action == "resume":
                snapshot.request_generation += 1
                snapshot.phase, snapshot.status = CasePhase.RESOLUTION, "resuming"
            elif action == "cancel":
                snapshot.request_generation += 1
                snapshot.phase, snapshot.status = CasePhase.CANCELLED, "cancelled"
            elif action == "wait_for_external":
                snapshot.phase = CasePhase.WAITING_FOR_EXTERNAL
                snapshot.status = "waiting_for_external"
                snapshot.budget_usage.user_waits += 1
            elif action == "wait_for_user":
                snapshot.phase = CasePhase.WAITING_FOR_USER
                snapshot.status = "waiting_for_user"
                snapshot.budget_usage.user_waits += 1
            elif action == "reopen":
                snapshot.request_generation += 1
                snapshot.case_revision += 1
                snapshot.phase, snapshot.status = CasePhase.RESOLUTION, "reopened"
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type=f"case_{action}",
                status=snapshot.status,
                summary=reason or f"Case lifecycle action: {action}",
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def set_budget_limits(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        limits: CaseBudgetLimits,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            snapshot.budget_limits = limits
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type="case_budget_updated",
                status=snapshot.status,
                summary="Case resource envelope updated",
                data=limits.model_dump(mode="json"),
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def set_interaction_preferences(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        preferences: InteractionPreferences,
    ) -> CaseRunSnapshot:
        """Apply explicit Case-local preferences without creating a persona."""

        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            previous_usage = snapshot.human_collaboration.usage.model_copy(deep=True)
            provided_fields = preferences.model_fields_set
            merged = snapshot.human_collaboration.preference_overrides.model_copy(
                update={
                    field: getattr(preferences, field)
                    for field in provided_fields
                }
            )
            context = copy.deepcopy(snapshot.m1.get("resolution_context") or {})
            snapshot.human_collaboration = derive_human_collaboration_state(
                context,
                overrides=merged,
            )
            snapshot.human_collaboration.usage = previous_usage
            snapshot.context_summary.user_state.update(
                {
                    "explanation_preference": {
                        "minimal": "minimal",
                        "guided": "on_demand",
                        "expert": "detailed",
                    }[snapshot.human_collaboration.expression_depth.value],
                    "collaboration_preference": (
                        snapshot.human_collaboration.initiative_mode.value
                    ),
                    "progress_preference": snapshot.human_collaboration.progress_cadence.value,
                    "delivery_preference": (
                        "result_first"
                        if snapshot.human_collaboration.result_first
                        else "balanced"
                    ),
                }
            )
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type="interaction_preferences_updated",
                status=snapshot.status,
                summary="Current-Case interaction preferences updated",
                data=merged.model_dump(mode="json", exclude_none=True),
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_evidence_artifacts(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        artifacts: list[EvidenceArtifact],
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            known = {item.artifact_id for item in snapshot.evidence_artifacts}
            additions = [item for item in artifacts if item.artifact_id not in known]
            snapshot.evidence_artifacts.extend(additions)
            if additions:
                self._append_event(
                    snapshot,
                    phase=CasePhase.FORMULATION,
                    event_type="multimodal_evidence_received",
                    status="unverified",
                    summary=f"Received {len(additions)} source-preserving evidence artifacts",
                    data={
                        "artifacts": [
                            {
                                "artifact_id": item.artifact_id,
                                "kind": item.kind,
                                "name": item.name,
                                "epistemic_status": item.epistemic_status,
                            }
                            for item in additions
                        ]
                    },
                )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_human_effort(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        presentation: LayeredResponse,
        asked_questions: int = 0,
        requested_actions: int = 0,
        progress_updates: int = 0,
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            usage = snapshot.human_collaboration.usage
            usage.questions_asked += max(0, asked_questions)
            usage.user_actions_requested += max(0, requested_actions)
            usage.primary_response_chars += len(presentation.primary_message)
            usage.deferred_detail_chars += len(presentation.details)
            usage.visible_progress_updates += max(0, progress_updates)
            snapshot.human_collaboration.updated_at = datetime.now(UTC).isoformat()
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type="human_effort_accounted",
                status=snapshot.status,
                summary="User-visible interaction cost accounted",
                data={
                    "expression_depth": presentation.expression_depth.value,
                    "primary_chars": len(presentation.primary_message),
                    "deferred_chars": len(presentation.details),
                    "questions": max(0, asked_questions),
                    "requested_actions": max(0, requested_actions),
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_control_activation(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        mechanism: str,
        activated: bool,
        inputs: dict[str, Any] | None = None,
        behavior_changes: list[str] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> CaseRunSnapshot:
        """Record whether a design actually reached a behavior consumer."""

        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type="control_mechanism_evaluated",
                status="activated" if activated else "not_activated",
                summary=f"Control mechanism evaluated: {mechanism}",
                data={
                    "mechanism": mechanism,
                    "inputs": inputs or {},
                    "behavior_changes": behavior_changes or [],
                    "outcome": outcome or {},
                },
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def record_runtime_event(
        self,
        case_id: str,
        *,
        user_id: str,
        tenant_id: str,
        event_type: str,
        status: str,
        summary: str,
        data: dict[str, Any] | None = None,
        phase: CasePhase | None = None,
        expected_request_generation: int | None = None,
    ) -> CaseRunSnapshot:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            self._append_event(
                snapshot,
                phase=phase or snapshot.phase,
                event_type=event_type,
                status=status,
                summary=summary,
                data=data,
            )
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return snapshot.model_copy(deep=True)

    async def budget_exceeded(self, case_id: str, *, user_id: str, tenant_id: str) -> list[str]:
        async with self._lock:
            snapshot = self._required(case_id)
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            limits = snapshot.budget_limits
            usage = snapshot.budget_usage
            usage.wall_time_seconds = max(
                0.0,
                (datetime.now(UTC) - datetime.fromisoformat(snapshot.created_at)).total_seconds(),
            )
            exceeded: list[str] = []
            if usage.wall_time_seconds >= limits.wall_time_seconds:
                exceeded.append("wall_time_seconds")
            if usage.model_calls >= limits.model_calls:
                exceeded.append("model_calls")
            if usage.tool_calls >= limits.tool_calls:
                exceeded.append("tool_calls")
            if usage.collaboration_rounds >= limits.collaboration_rounds:
                exceeded.append("collaboration_rounds")
            if usage.user_waits >= limits.user_waits:
                exceeded.append("user_waits")
            self._touch(snapshot)
            await self._after_mutation(snapshot)
            return exceeded

    async def get(self, case_id: str, *, user_id: str, tenant_id: str) -> CaseRunSnapshot | None:
        async with self._lock:
            snapshot = self._cases.get(case_id)
            if snapshot is None:
                return None
            self._assert_owner(snapshot, user_id=user_id, tenant_id=tenant_id)
            return snapshot.model_copy(deep=True)

    async def get_by_conversation(
        self, conv_id: str, *, user_id: str, tenant_id: str
    ) -> CaseRunSnapshot | None:
        async with self._lock:
            case_id = self._case_by_conversation.get((tenant_id, user_id, conv_id))
            if case_id is None:
                return None
            return self._cases[case_id].model_copy(deep=True)

    def _required(self, case_id: str) -> CaseRunSnapshot:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise KeyError(f"unknown case_id: {case_id}") from exc

    @staticmethod
    def _summary_from_formulation(formulation: FormulationResult) -> CaseContextSummary:
        context = formulation.resolution_context or {}
        evidence = context.get("evidence_handoff", {})
        observations = list(context.get("observations", []))
        return CaseContextSummary(
            goal=dict(context.get("goal", {})),
            confirmed_facts=[
                item for item in observations if item.get("verification_status") == "verified"
            ],
            reported_observations=observations,
            salient_failures=list(context.get("salient_failures", []))[-20:],
            open_evidence=list(evidence.get("open", []))[-30:],
            user_state=dict(context.get("user_state", {})),
            action_constraints=dict(context.get("action_constraints", {})),
        )

    @staticmethod
    def _assert_owner(snapshot: CaseRunSnapshot, *, user_id: str, tenant_id: str) -> None:
        if snapshot.user_id != user_id or snapshot.tenant_id != tenant_id:
            raise PermissionError("case belongs to a different actor or tenant")

    @staticmethod
    def _assert_current_request(
        snapshot: CaseRunSnapshot,
        expected_request_generation: int | None,
    ) -> None:
        if (
            expected_request_generation is not None
            and snapshot.request_generation != expected_request_generation
        ):
            raise StaleCaseResultError(
                "result belongs to an older Case request generation"
            )

    @staticmethod
    def _append_trace(snapshot: CaseRunSnapshot, trace_id: str) -> None:
        if trace_id and trace_id not in snapshot.trace_ids:
            snapshot.trace_ids.append(trace_id)

    def _append_event(
        self,
        snapshot: CaseRunSnapshot,
        *,
        phase: CasePhase,
        event_type: str,
        status: str,
        summary: str,
        trace_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        sequence = snapshot.events[-1].sequence + 1 if snapshot.events else 1
        snapshot.events.append(
            CaseLifecycleEvent(
                sequence=sequence,
                case_id=snapshot.case_id,
                phase=phase,
                event_type=event_type,
                status=status,
                trace_id=trace_id,
                summary=summary,
                data=data or {},
            )
        )
        snapshot.events = snapshot.events[-self._max_events :]

    @staticmethod
    def _touch(snapshot: CaseRunSnapshot) -> None:
        snapshot.updated_at = datetime.now(UTC).isoformat()


__all__ = [
    "InMemoryCaseRunRegistry",
    "StaleCaseResultError",
    "m1_thread_id",
    "m2_thread_id",
    "m3_thread_id",
]
