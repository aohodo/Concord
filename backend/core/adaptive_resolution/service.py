"""Application boundary for the compiled M2 LangGraph."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.tools import RuntimeToolProfile, ToolContext

from .events import merge_resolution_context
from .graph import AdaptiveResolutionGraph
from .models import (
    CaseEvent,
    CaseEventType,
    CollaborationHandoff,
    DecisionEvent,
    ProvisionalExplanation,
    ResolutionPlan,
    ResolutionResult,
    ResolutionStatus,
    UserProgressEvent,
)
from .planner import LangChainResolutionPlanner, ResolutionPlanner

logger = logging.getLogger(__name__)


class AdaptiveResolutionService:
    def __init__(
        self,
        *,
        checkpointer: Any,
        tool_runtime: Any,
        planner: ResolutionPlanner | None = None,
        client: Any = None,
        model: str = "",
        event_store: Any | None = None,
    ) -> None:
        if planner is None:
            if client is None or not model:
                raise ValueError("client and model are required when planner is not provided")
            planner = LangChainResolutionPlanner(client, model)
        self._event_store = event_store
        self._tool_runtime = tool_runtime
        self._locks: dict[str, asyncio.Lock] = {}
        self._graph = AdaptiveResolutionGraph(
            planner=planner,
            tool_runtime=tool_runtime,
            checkpointer=checkpointer,
            event_store=event_store,
        )

    def runtime_tool_profile(
        self,
        *,
        case_id: str,
        permissions: list[str] | None = None,
        confirmation_granted: bool = False,
        actor_id: str = "anonymous",
        tenant_id: str = "local",
    ) -> RuntimeToolProfile:
        """Describe the tools this exact request could execute."""

        context = ToolContext(
            case_id=case_id,
            actor_id=actor_id,
            tenant_id=tenant_id,
            permissions=set(permissions or []),
            confirmation_granted=confirmation_granted,
            allow_external_writes=False,
        )
        return self._tool_runtime.describe_authorized_tools(context)

    async def resolve(
        self,
        *,
        resolution_context: dict[str, Any],
        thread_id: str | None = None,
        permissions: list[str] | None = None,
        confirmation_granted: bool = False,
        episode_decision_budget: int = 6,
        additional_evidence: list[dict[str, Any]] | None = None,
        user_state_update: dict[str, Any] | None = None,
        collaboration_advice: dict[str, Any] | None = None,
        failure_memory_enabled: bool = True,
        max_cycles: int | None = None,
        actor_id: str = "anonymous",
        tenant_id: str = "local",
    ) -> ResolutionResult:
        case_id = str(resolution_context.get("case_id", "")).strip()
        if not case_id:
            raise ValueError("resolution_context.case_id is required")
        resolved_thread_id = thread_id or f"m2:{case_id}:{uuid4()}"
        await self.prepare_thread(
            thread_id=resolved_thread_id,
            case_id=case_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        lock = self._locks.setdefault(resolved_thread_id, asyncio.Lock())
        async with lock:
            return await self._resolve_locked(
                resolution_context=resolution_context,
                thread_id=resolved_thread_id,
                permissions=permissions,
                confirmation_granted=confirmation_granted,
                episode_decision_budget=episode_decision_budget,
                additional_evidence=additional_evidence,
                user_state_update=user_state_update,
                collaboration_advice=collaboration_advice,
                failure_memory_enabled=failure_memory_enabled,
                max_cycles=max_cycles,
                actor_id=actor_id,
                tenant_id=tenant_id,
            )

    async def prepare_thread(
        self,
        *,
        thread_id: str,
        case_id: str,
        tenant_id: str = "local",
        actor_id: str = "anonymous",
    ) -> None:
        if self._event_store is not None:
            await self._event_store.bind_thread(
                thread_id=thread_id,
                case_id=case_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                created_at=datetime.now(UTC).isoformat(),
            )

    async def _resolve_locked(
        self,
        *,
        resolution_context: dict[str, Any],
        thread_id: str,
        permissions: list[str] | None,
        confirmation_granted: bool,
        episode_decision_budget: int,
        additional_evidence: list[dict[str, Any]] | None,
        user_state_update: dict[str, Any] | None,
        collaboration_advice: dict[str, Any] | None,
        failure_memory_enabled: bool,
        max_cycles: int | None,
        actor_id: str,
        tenant_id: str,
    ) -> ResolutionResult:
        case_id = str(resolution_context.get("case_id", "")).strip()
        if not case_id:
            raise ValueError("resolution_context.case_id is required")
        constraints = resolution_context.get("action_constraints", {})
        if not constraints.get("case_entry_allowed", False):
            raise ValueError("resolution_context does not allow M2 entry")
        trace_uuid = uuid4()
        resolved_thread_id = thread_id
        config = {"configurable": {"thread_id": resolved_thread_id}}
        snapshot = await self._graph.compiled.aget_state(config)
        previous = dict(snapshot.values or {})
        is_resume = bool(previous)
        if is_resume and previous.get("case_id") != case_id:
            raise ValueError("thread_id belongs to a different M2 case")

        merged_context, merged_event, cancelled, _ = merge_resolution_context(
            previous.get("resolution_context"),
            resolution_context,
            additional_evidence=additional_evidence or [],
            user_state_update=user_state_update or {},
        )
        if cancelled:
            raise ValueError("initial context merge cannot cancel a case")
        if collaboration_advice:
            collaboration_history = list(merged_context.get("collaboration_history", []))
            collaboration_history.append(collaboration_advice)
            merged_context["collaboration_history"] = collaboration_history[-4:]
        effective_budget = max_cycles if max_cycles is not None else episode_decision_budget
        input_state = {
            "resolution_context": merged_context,
            "case_id": case_id,
            "thread_id": resolved_thread_id,
            "trace_id": str(trace_uuid),
            # None means "reuse the prior grant"; an explicit empty list means
            # "the user revoked every grant" and must never fall back.
            "permissions": (
                list(permissions)
                if permissions is not None
                else list(previous.get("permissions", []))
            ),
            "confirmation_granted": confirmation_granted,
            "episode_decision_budget": max(1, min(effective_budget, 12)),
            "episode_decisions": 0,
            "cycles": int(previous.get("cycles", 0)),
            "tool_history": list(previous.get("tool_history", [])),
            "belief_history": list(previous.get("belief_history", [])),
            "progress_history": list(previous.get("progress_history", [])),
            "progress_events": list(previous.get("progress_events", [])),
            "pending_verification_targets": list(
                previous.get("pending_verification_targets", [])
            ),
            "pending_verification_requests": list(
                previous.get("pending_verification_requests", [])
            ),
            "guardrail_feedback": "",
            "guardrail_events": list(previous.get("guardrail_events", [])),
            "decision_event": (
                DecisionEvent.COLLABORATION_RESULT.value
                if collaboration_advice
                else
                "user_evidence_updated"
                if additional_evidence or user_state_update
                else "environment_updated"
                if is_resume
                else "initial"
            ),
            "decision_events": list(previous.get("decision_events", [])),
            "control_events": list(previous.get("control_events", [])),
            "is_resume": is_resume,
            "resume_event": (
                DecisionEvent.COLLABORATION_RESULT.value
                if collaboration_advice
                else "user_evidence_updated"
                if additional_evidence or user_state_update
                else "environment_updated"
            ),
            "status": ResolutionStatus.INVESTIGATING.value,
            "response": "",
            "collaboration_reason": None,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "consumed_event_ids": list(previous.get("consumed_event_ids", [])),
            "last_event_sequence": int(previous.get("last_event_sequence", 0)),
            # A genuinely new M3 strategy resets local search stagnation, while
            # tool/failure history remains intact for retry control and audit.
            "stalled_steps": (
                0 if collaboration_advice else int(previous.get("stalled_steps", 0))
            ),
            "failure_memory_enabled": failure_memory_enabled,
            "collaboration_mode": str(previous.get("collaboration_mode", "")),
            "considered_experience_ids": list(
                previous.get("considered_experience_ids", [])
            ),
            "adopted_experience_ids": list(previous.get("adopted_experience_ids", [])),
        }
        if merged_event is not None:
            input_state["decision_event"] = merged_event.value
        runtime_error: str | None = None
        try:
            output = await self._graph.compiled.ainvoke(
                input_state,
                config={
                    **config,
                    "run_id": trace_uuid,
                    "run_name": "concord_m2_adaptive_resolution",
                    "tags": ["concord", "m2", "adaptive-resolution"],
                    "metadata": {
                        "case_id": case_id,
                        "phase": "M2",
                        "synthetic": self._graph.is_synthetic_case(case_id),
                    },
                    "recursion_limit": max(20, effective_budget * 7),
                },
            )
        except Exception:
            logger.exception("M2 runtime failure thread_id=%s case_id=%s", thread_id, case_id)
            runtime_error = "M2_RUNTIME_FAILURE"
            failed_snapshot = await self._graph.compiled.aget_state(config)
            recovered = dict(failed_snapshot.values or input_state)
            failure_event = UserProgressEvent(
                cycle=int(recovered.get("cycles", 0)),
                stage=ResolutionStatus.ERROR.value,
                message="本轮处理遇到异常，已保留当前进度，可以稍后从这里继续。",
                status=ResolutionStatus.ERROR.value,
            ).model_dump(mode="json")
            failure_state = {
                "status": ResolutionStatus.ERROR.value,
                "response": failure_event["message"],
                "progress_events": [
                    *list(recovered.get("progress_events", [])),
                    failure_event,
                ],
            }
            await self._graph.compiled.aupdate_state(config, failure_state)
            output = {**recovered, **failure_state}
        latest_beliefs = output.get("belief_history", [])
        explanations = (
            latest_beliefs[-1].get("provisional_explanations", [])
            if latest_beliefs
            else []
        )
        final_plan = (
            ResolutionPlan.model_validate(output["plan"])
            if output.get("plan")
            else None
        )
        resolution_policy = merged_context.get("provisional_resolution_policy", {})
        question_limit = max(0, int(resolution_policy.get("max_questions_per_turn", 1)))
        action_limit = max(0, int(resolution_policy.get("max_user_actions_per_turn", 1)))
        waiting_for_user = (
            output.get("status") == ResolutionStatus.WAITING_FOR_USER.value
        )
        retrieved_experience_ids = [
            str(item.get("experience_id"))
            for item in merged_context.get("candidate_experiences", [])
            if isinstance(item, dict) and item.get("experience_id")
        ]
        considered_experience_ids = list(
            dict.fromkeys(output.get("considered_experience_ids", []))
        )
        adopted_experience_ids = list(
            dict.fromkeys(
                experience_id
                for item in output.get("tool_history", [])
                for experience_id in item.get("experience_source_ids", [])
            )
        )
        verified_experience_ids = (
            adopted_experience_ids
            if output.get("status") == ResolutionStatus.RESOLVED.value
            else []
        )
        result = ResolutionResult(
            case_id=case_id,
            thread_id=resolved_thread_id,
            trace_id=str(trace_uuid),
            status=ResolutionStatus(output["status"]),
            response=output.get("response", ""),
            cycles=int(output.get("cycles", 0)),
            episode_decisions=int(output.get("episode_decisions", 0)),
            final_decision=final_plan.decision if final_plan is not None else None,
            user_questions=(
                final_plan.user_questions[:question_limit]
                if final_plan is not None and waiting_for_user
                else []
            ),
            requested_user_actions=(
                final_plan.requested_user_actions[:action_limit]
                if final_plan is not None and waiting_for_user
                else []
            ),
            provisional_explanations=[
                ProvisionalExplanation.model_validate(item) for item in explanations
            ],
            tool_history=output.get("tool_history", []),
            belief_history=latest_beliefs,
            progress_history=output.get("progress_history", []),
            collaboration_reason=output.get("collaboration_reason"),
            guardrail_events=output.get("guardrail_events", []),
            decision_events=output.get("decision_events", []),
            control_events=output.get("control_events", []),
            progress_events=[
                UserProgressEvent.model_validate(item)
                for item in output.get("progress_events", [])
            ],
            verification_complete=(
                bool(final_plan.verification_complete) if final_plan is not None else False
            ),
            resolution_evidence=(
                list(final_plan.resolution_evidence) if final_plan is not None else []
            ),
            criterion_verifications=(
                list(final_plan.criterion_verifications)
                if final_plan is not None
                else []
            ),
            retrieved_experience_ids=retrieved_experience_ids,
            considered_experience_ids=considered_experience_ids,
            adopted_experience_ids=adopted_experience_ids,
            verified_experience_ids=verified_experience_ids,
            experience_source_ids=(
                adopted_experience_ids
            ),
            error=runtime_error,
        )
        if result.status is ResolutionStatus.COLLABORATION_REQUIRED:
            handoff = self._build_collaboration_handoff(
                result=result,
                resolution_context=output.get("resolution_context", merged_context),
                plan={
                    **output.get("plan", {}),
                    "suggested_collaboration_mode": output.get("collaboration_mode")
                    or output.get("plan", {}).get(
                        "suggested_collaboration_mode", "specialist_handoff"
                    ),
                },
            )
            result = result.model_copy(update={"collaboration_handoff": handoff})
        return result

    async def get_progress(
        self,
        thread_id: str,
        *,
        actor_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest checkpoint so clients can poll grounded progress."""

        if self._event_store is not None and actor_id is not None and tenant_id is not None:
            await self._event_store.assert_owner(
                thread_id, tenant_id=tenant_id, actor_id=actor_id
            )

        snapshot = await self._graph.compiled.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        state = dict(snapshot.values or {})
        if not state:
            return None
        return {
            "thread_id": thread_id,
            "case_id": state.get("case_id", ""),
            "status": state.get("status", ResolutionStatus.INVESTIGATING.value),
            "total_model_calls": int(state.get("cycles", 0)),
            "case_revision": int(state.get("cycles", 0)),
            "episode_model_calls": int(state.get("episode_decisions", 0)),
            "progress_events": list(state.get("progress_events", [])),
            "latest_progress": (
                state.get("progress_events", [])[-1]
                if state.get("progress_events")
                else None
            ),
            "error": (
                "M2_RUNTIME_FAILURE"
                if state.get("status") == ResolutionStatus.ERROR.value
                else None
            ),
        }

    async def enqueue_event(self, event: CaseEvent) -> CaseEvent:
        if self._event_store is None:
            raise RuntimeError("case event inbox is not configured")
        return await self._event_store.enqueue(event)

    async def cancel(
        self,
        *,
        thread_id: str,
        case_id: str,
        actor_id: str = "anonymous",
        tenant_id: str = "local",
        event_id: str | None = None,
    ) -> CaseEvent:
        return await self.enqueue_event(
            CaseEvent(
                event_id=event_id or str(uuid4()),
                thread_id=thread_id,
                case_id=case_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                type=CaseEventType.CANCEL,
                payload={},
            )
        )

    @staticmethod
    def _build_collaboration_handoff(
        *,
        result: ResolutionResult,
        resolution_context: dict[str, Any],
        plan: dict[str, Any],
    ) -> CollaborationHandoff:
        evidence = resolution_context.get("evidence_handoff", {})
        successful_observations = [
            {
                "tool_id": item.get("tool_id"),
                "observation": item.get("observation"),
                "evidence": item.get("evidence", []),
            }
            for item in result.tool_history
            if item.get("status") == "succeeded"
            and item.get("category") in {"observe", "verify"}
        ]
        independently_verified = [
            item
            for item in successful_observations
            if item.get("automatic_verification") is True
            and item.get("expectation_matched") is True
        ]
        failed = [
            item
            for item in result.tool_history
            if item.get("status") in {"failed", "timed_out", "denied", "conflict"}
        ]
        return CollaborationHandoff(
            case_id=result.case_id,
            thread_id=result.thread_id,
            case_revision=result.cycles,
            reason=result.collaboration_reason or "M2 cannot continue within current boundary",
            goal=dict(resolution_context.get("goal", {})),
            action_constraints=dict(resolution_context.get("action_constraints", {})),
            reported_evidence=list(evidence.get("resolved", [])),
            observed_evidence=successful_observations,
            confirmed_facts=independently_verified,
            competing_explanations=result.provisional_explanations,
            failed_directions=failed,
            prohibited_retries=[
                f"{item.get('tool_id')}:{item.get('canonical_arguments', '')}"
                for item in failed
            ],
            open_evidence=(
                list(evidence.get("open", []))
                + list(evidence.get("unavailable", []))
                + list(evidence.get("deferred", []))
            ),
            permission_boundary=list(
                resolution_context.get("action_constraints", {}).get(
                    "restricted_operation_modes", []
                )
            ),
            coordination_structure=plan.get("coordination_structure", {}),
            suggested_collaboration_mode=str(
                plan.get("suggested_collaboration_mode", "specialist_handoff")
            ),
        )

    def build_experimental_collaboration_handoff(
        self,
        *,
        result: ResolutionResult,
        resolution_context: dict[str, Any],
        reason: str,
    ) -> CollaborationHandoff:
        """Build an evidence-preserving handoff for an explicit topology control.

        This exists only so an always-on fixed-team experiment can measure the
        cost of bypassing Concord's collaboration gate.
        """

        handoff = self._build_collaboration_handoff(
            result=result,
            resolution_context=resolution_context,
            plan={
                "suggested_collaboration_mode": "parallel_workers",
                "coordination_structure": {
                    "parallelism_known": True,
                    "independent_workstreams": [
                        "domain diagnosis",
                        "evidence audit",
                        "safe action planning",
                    ],
                },
            },
        )
        return handoff.model_copy(update={"reason": reason})
