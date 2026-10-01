"""Application service for M3 adaptive collaboration."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from uuid import uuid4

from core.adaptive_resolution.models import CollaborationHandoff, CollaborationMode

from .directory import AgentDirectory
from .graph import AdaptiveCollaborationGraph
from .models import (
    AgentContribution,
    AgentOutcomeFeedback,
    CollaborationPlan,
    CollaborationProgressEvent,
    CollaborationResult,
    CollaborationRuntimeMetrics,
    CollaborationStatus,
    CollaborationSynthesis,
    CollaborationTopology,
    M2ResumePacket,
)
from .planner import (
    CollaborationCoordinator,
    CollaborationWorker,
    LangChainCollaborationCoordinator,
    LangChainCollaborationWorker,
)
from .usage import ModelUsageMeter, begin_usage_meter, end_usage_meter

logger = logging.getLogger(__name__)


class AdaptiveCollaborationService:
    def __init__(
        self,
        *,
        checkpointer: Any,
        performance_store: Any,
        client: Any | None = None,
        model: str = "",
        coordinator: CollaborationCoordinator | None = None,
        worker: CollaborationWorker | None = None,
        directory: AgentDirectory | None = None,
    ) -> None:
        if coordinator is None or worker is None:
            if client is None or not model:
                raise ValueError("client and model are required without test doubles")
            coordinator = coordinator or LangChainCollaborationCoordinator(client, model)
            worker = worker or LangChainCollaborationWorker(client, model)
        self._performance_store = performance_store
        self._directory = directory or AgentDirectory()
        self._graph = AdaptiveCollaborationGraph(
            coordinator=coordinator,
            worker=worker,
            directory=self._directory,
            checkpointer=checkpointer,
        )

    async def collaborate(
        self,
        *,
        handoff: CollaborationHandoff | dict[str, Any],
        thread_id: str | None = None,
        collaboration_id: str | None = None,
        max_rounds: int = 2,
        max_agents: int = 4,
        minimum_gain_margin: float = 0.05,
        topology: CollaborationTopology | str = CollaborationTopology.ADAPTIVE,
        coordinator_timeout_seconds: float = 120.0,
        worker_timeout_seconds: float = 120.0,
    ) -> CollaborationResult:
        validated = (
            handoff
            if isinstance(handoff, CollaborationHandoff)
            else CollaborationHandoff.model_validate(handoff)
        )
        resolved_collaboration_id = collaboration_id or str(uuid4())
        resolved_thread = (
            thread_id or f"m3:{validated.thread_id}:{resolved_collaboration_id}"
        )
        topology = CollaborationTopology(topology)
        if topology is CollaborationTopology.M2_ONLY:
            return CollaborationResult(
                collaboration_id=resolved_collaboration_id,
                case_id=validated.case_id,
                m2_thread_id=validated.thread_id,
                thread_id=resolved_thread,
                status=CollaborationStatus.NO_BENEFIT,
                mode=CollaborationMode(validated.suggested_collaboration_mode),
                response="M2-only control does not recruit collaborators.",
                topology=topology,
                source_case_revision=validated.case_revision,
            )
        config = {"configurable": {"thread_id": resolved_thread}}
        performance = await self._performance_store.snapshot()
        context_snapshot = getattr(self._performance_store, "context_snapshot", None)
        contextual_performance = await context_snapshot() if context_snapshot else {}
        runtime_error = None
        meter = ModelUsageMeter()
        usage_token = begin_usage_meter(meter)
        started = time.monotonic()
        try:
            output = await self._graph.compiled.ainvoke(
                {
                    "handoff": validated.model_dump(mode="json"),
                    "collaboration_id": resolved_collaboration_id,
                    "case_id": validated.case_id,
                    "m2_thread_id": validated.thread_id,
                    "thread_id": resolved_thread,
                    "performance": {
                        key: item.model_dump(mode="json")
                        for key, item in performance.items()
                    },
                    "contextual_performance": {
                        key: item.model_dump(mode="json")
                        for key, item in contextual_performance.items()
                    },
                    "max_rounds": max(1, min(max_rounds, 3)),
                    "max_agents": max(1, min(max_agents, 6)),
                    "minimum_gain_margin": max(0.0, min(minimum_gain_margin, 1.0)),
                    "topology": topology.value,
                    "coordinator_timeout_seconds": max(
                        0.1, min(float(coordinator_timeout_seconds), 600.0)
                    ),
                    "worker_timeout_seconds": max(
                        0.1, min(float(worker_timeout_seconds), 600.0)
                    ),
                },
                config={
                    **config,
                    "run_name": "concord_m3_adaptive_collaboration",
                    "tags": ["concord", "m3", "adaptive-collaboration"],
                    "metadata": {"case_id": validated.case_id, "phase": "M3"},
                    "recursion_limit": 24,
                },
            )
        except Exception:
            logger.exception(
                "M3 runtime failure thread_id=%s case_id=%s",
                resolved_thread,
                validated.case_id,
            )
            runtime_error = "M3_RUNTIME_FAILURE"
            snapshot = await self._graph.compiled.aget_state(config)
            recovered = dict(snapshot.values or {})
            error_event = CollaborationProgressEvent(
                stage="error",
                message="协作运行遇到异常，M2 Case 与已有证据未受影响。",
                status="error",
                round_index=int(recovered.get("round_index", 0)),
            ).model_dump(mode="json")
            failure_state = {
                "status": CollaborationStatus.ERROR.value,
                "error": runtime_error,
                "response": "协作运行遇到异常，M2 Case 与已有证据未受影响。",
                "progress_events": [
                    *recovered.get("progress_events", []),
                    error_event,
                ],
            }
            await self._graph.compiled.aupdate_state(config, failure_state)
            output = {**recovered, **failure_state}
        finally:
            wall_time_ms = (time.monotonic() - started) * 1000
            end_usage_meter(usage_token)
        synthesis = (
            CollaborationSynthesis.model_validate(output["synthesis"])
            if output.get("synthesis")
            else None
        )
        packet = (
            M2ResumePacket.model_validate(output["resume_packet"])
            if output.get("resume_packet")
            else None
        )
        plans = [
            CollaborationPlan.model_validate(item) for item in output.get("plans", [])
        ]
        mode = (
            plans[-1].mode
            if plans
            else CollaborationMode(validated.suggested_collaboration_mode)
        )
        contributions = [
            item if isinstance(item, AgentContribution) else AgentContribution.model_validate(item)
            for item in output.get("contributions", [])
        ]
        statements = [
            re.sub(r"\s+", " ", item.statement.strip().casefold())
            for contribution in contributions
            for item in contribution.items
            if item.statement.strip()
        ]
        unique_claims = len(set(statements))
        duplicate_claims = max(0, len(statements) - unique_claims)
        evidenced_claims = sum(
            bool(item.basis_refs)
            for contribution in contributions
            for item in contribution.items
        )
        source_agents: dict[str, set[str]] = {}
        for contribution in contributions:
            for item in contribution.items:
                for ref in item.basis_refs:
                    source_agents.setdefault(ref, set()).add(contribution.agent_id)
        shared_source_groups = sum(len(agents) > 1 for agents in source_agents.values())
        novelty_proxy = unique_claims / max(1, len(statements))
        worker_failures = sum(bool(item.error) for item in contributions)
        measured_cost = min(
            1.0,
            meter.calls / 10 * 0.4
            + wall_time_ms / 120_000 * 0.4
            + worker_failures / max(1, len(contributions)) * 0.2,
        )
        metrics = CollaborationRuntimeMetrics(
            coordinator_calls=int(output.get("coordinator_calls", 0)),
            worker_calls=len(contributions),
            model_calls=meter.calls,
            input_tokens=meter.input_tokens if meter.tokens_available else None,
            output_tokens=meter.output_tokens if meter.tokens_available else None,
            tokens_available=meter.tokens_available,
            wall_time_ms=round(wall_time_ms, 1),
            worker_latency_ms=round(sum(item.latency_ms for item in contributions), 1),
            worker_failures=worker_failures,
            unique_claims=unique_claims,
            duplicate_claims=duplicate_claims,
            evidenced_claims=evidenced_claims,
            shared_source_groups=shared_source_groups,
            novelty_proxy=round(novelty_proxy, 4),
            measured_coordination_cost=round(measured_cost, 4),
        )
        return CollaborationResult(
            collaboration_id=resolved_collaboration_id,
            case_id=validated.case_id,
            m2_thread_id=validated.thread_id,
            thread_id=resolved_thread,
            status=CollaborationStatus(output.get("status", "error")),
            mode=mode,
            response=output.get("response", "协作运行已结束。"),
            rounds=int(output.get("round_index", 0)),
            agents_recruited=list(output.get("recruited_agents", [])),
            plans=plans,
            contributions=contributions,
            synthesis=synthesis,
            resume_packet=packet,
            progress_events=output.get("progress_events", []),
            coordination_cost=float(output.get("coordination_cost", 0.0)),
            topology=topology,
            source_case_revision=validated.case_revision,
            metrics=metrics,
            error=runtime_error,
        )

    async def get_progress(self, thread_id: str) -> dict[str, Any] | None:
        snapshot = await self._graph.compiled.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        state = dict(snapshot.values or {})
        if not state:
            return None
        return {
            "collaboration_id": state.get("collaboration_id", ""),
            "thread_id": thread_id,
            "case_id": state.get("case_id", ""),
            "m2_thread_id": state.get("m2_thread_id", ""),
            "status": state.get("status", "running"),
            "rounds": int(state.get("round_index", 0)),
            "agents_recruited": list(state.get("recruited_agents", [])),
            "progress_events": list(state.get("progress_events", [])),
            "resume_packet": state.get("resume_packet"),
            "error": state.get("error"),
        }

    async def cancel(self, thread_id: str) -> dict[str, Any] | None:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._graph.compiled.aget_state(config)
        state = dict(snapshot.values or {})
        if not state:
            return None
        event = CollaborationProgressEvent(
            stage="cancelled",
            message="已停止当前协作并保留已有 Case 与贡献。",
            status=CollaborationStatus.CANCELLED.value,
            round_index=int(state.get("round_index", 0)),
        ).model_dump(mode="json")
        await self._graph.compiled.aupdate_state(
            config,
            {
                "status": CollaborationStatus.CANCELLED.value,
                "response": event["message"],
                "progress_events": [*state.get("progress_events", []), event],
            },
        )
        return await self.get_progress(thread_id)

    async def record_outcome(self, feedback: AgentOutcomeFeedback):
        self._directory.require(feedback.agent_id)
        return await self._performance_store.record(feedback)

    async def record_resolution_outcome(
        self,
        *,
        collaboration: CollaborationResult,
        resolution_result: Any,
        previous_tool_count: int = 0,
    ) -> list[AgentOutcomeFeedback]:
        """Credit only assignment IDs explicitly cited by M2 and tested by tools."""

        assignments = {
            assignment.assignment_id: assignment
            for plan in collaboration.plans
            for assignment in plan.assignments
        }
        new_history = list(resolution_result.tool_history)[previous_tool_count:]
        recorded: list[AgentOutcomeFeedback] = []
        for assignment_id, assignment in assignments.items():
            tested = [
                item
                for item in new_history
                if assignment_id in item.get("collaboration_source_ids", [])
            ]
            if not tested:
                continue
            # Advice is disproven only by successful evidence acquisition that
            # contradicts its prediction. Permission, timeout and tool/runtime
            # failures say nothing about whether the advice was correct.
            disproven = sum(
                item.get("status") == "succeeded"
                and item.get("category") in {"observe", "verify"}
                and bool(item.get("evidence"))
                and item.get("expectation_matched") is False
                for item in tested
            )
            verified_support = any(
                item.get("status") == "succeeded"
                and item.get("category") in {"observe", "verify"}
                and bool(item.get("evidence"))
                and item.get("expectation_matched") is not False
                for item in tested
            )
            if not verified_support and not disproven:
                continue
            successful = verified_support and disproven == 0
            feedback = AgentOutcomeFeedback(
                agent_id=assignment.agent_id,
                successful=successful,
                latency_ms=sum(float(item.get("latency_ms", 0.0)) for item in tested),
                collaboration_id=collaboration.collaboration_id,
                assignment_id=assignment_id,
                capabilities=assignment.required_capabilities,
                task_type=collaboration.mode.value,
                # Assignment-level provenance cannot identify which of a worker's
                # many statements was tested. Count one verified outcome rather
                # than promoting every contribution item to fact.
                verified_claims=int(successful),
                disproven_claims=disproven,
                progress_delta=1.0 if successful else -1.0 if disproven else 0.0,
                source=(
                    "m2_verified_support"
                    if successful
                    else "m2_verified_disproof"
                ),
            )
            await self.record_outcome(feedback)
            recorded.append(feedback)
        return recorded

    @staticmethod
    def result_is_current(
        collaboration: CollaborationResult, *, current_case_revision: int
    ) -> bool:
        return collaboration.source_case_revision == current_case_revision

    async def agent_catalog(self):
        return self._directory.catalog(await self._performance_store.snapshot())

    async def contextual_performance(self):
        snapshot = getattr(self._performance_store, "context_snapshot", None)
        return await snapshot() if snapshot else {}


__all__ = ["AdaptiveCollaborationService"]
