"""LangGraph M3: recruit, isolate, collaborate, arbitrate and stop."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.adaptive_resolution.models import CollaborationHandoff, CollaborationMode

from .directory import AgentDirectory
from .models import (
    AgentContribution,
    AgentPerformance,
    CollaborationAssignment,
    CollaborationConflict,
    CollaborationDecision,
    CollaborationPlan,
    CollaborationStatus,
    CollaborationSynthesis,
    CollaborationTopology,
    ConflictType,
    ContextPolicy,
    ContributionKind,
    M2ResumePacket,
    RoundDecision,
)
from .planner import CollaborationCoordinator, CollaborationWorker, build_worker_context


class CollaborationGraphState(TypedDict, total=False):
    handoff: dict[str, Any]
    collaboration_id: str
    case_id: str
    m2_thread_id: str
    thread_id: str
    performance: dict[str, dict[str, Any]]
    contextual_performance: dict[str, dict[str, Any]]
    max_rounds: int
    max_agents: int
    minimum_gain_margin: float
    topology: str
    coordinator_timeout_seconds: float
    worker_timeout_seconds: float
    round_index: int
    plans: list[dict[str, Any]]
    current_plan: dict[str, Any]
    pending_assignments: list[dict[str, Any]]
    contributions: list[dict[str, Any]]
    recruited_agents: list[str]
    coordination_cost: float
    last_round_cost: float
    round_novelty_proxy: float
    coordinator_calls: int
    progress_events: list[dict[str, Any]]
    synthesis: dict[str, Any] | None
    resume_packet: dict[str, Any] | None
    status: str
    response: str
    route: str
    error: str | None


def _objective_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


MODE_TEAM_SIZES = {
    CollaborationMode.SPECIALIST_HANDOFF: (1, 1),
    CollaborationMode.PARALLEL_WORKERS: (2, 4),
    CollaborationMode.INDEPENDENT_REVIEW: (1, 2),
    CollaborationMode.DIVERSE_EXPLORATION: (2, 4),
}


class AdaptiveCollaborationGraph:
    def __init__(
        self,
        *,
        coordinator: CollaborationCoordinator,
        worker: CollaborationWorker,
        directory: AgentDirectory,
        checkpointer: Any,
    ) -> None:
        self._coordinator = coordinator
        self._worker = worker
        self._directory = directory
        graph = StateGraph(CollaborationGraphState)
        graph.add_node("bootstrap", self._bootstrap)
        graph.add_node("plan", self._plan)
        graph.add_node("dispatch", self._dispatch)
        graph.add_node("synthesize", self._synthesize)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "bootstrap")
        graph.add_edge("bootstrap", "plan")
        graph.add_conditional_edges(
            "plan",
            lambda state: state["route"],
            {"dispatch": "dispatch", "finalize": "finalize"},
        )
        graph.add_edge("dispatch", "synthesize")
        graph.add_conditional_edges(
            "synthesize",
            lambda state: state["route"],
            {"plan": "plan", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        self.compiled = graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _progress(
        state: CollaborationGraphState,
        *,
        stage: str,
        message: str,
        status: str,
        agents: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            *state.get("progress_events", []),
            {
                "stage": stage,
                "message": message,
                "status": status,
                "round_index": int(state.get("round_index", 0)),
                "agents": agents or [],
            },
        ]

    def _bootstrap(self, state: CollaborationGraphState) -> dict[str, Any]:
        handoff = CollaborationHandoff.model_validate(state["handoff"])
        return {
            "handoff": handoff.model_dump(mode="json"),
            "case_id": handoff.case_id,
            "m2_thread_id": handoff.thread_id,
            "round_index": 0,
            "plans": list(state.get("plans", [])),
            "contributions": list(state.get("contributions", [])),
            "recruited_agents": list(state.get("recruited_agents", [])),
            "coordination_cost": float(state.get("coordination_cost", 0.0)),
            "coordinator_calls": int(state.get("coordinator_calls", 0)),
            "progress_events": self._progress(
                state,
                stage="coordination_assessment",
                message="正在判断额外协作是否值得，并匹配所需能力。",
                status="working",
            ),
            "status": CollaborationStatus.RUNNING.value,
            "response": "",
        }

    def _performance(self, state: CollaborationGraphState) -> dict[str, AgentPerformance]:
        return {
            agent_id: AgentPerformance.model_validate(item)
            for agent_id, item in state.get("performance", {}).items()
        }

    def _contextual_performance(
        self, state: CollaborationGraphState
    ) -> dict[str, AgentPerformance]:
        return {
            key: AgentPerformance.model_validate(item)
            for key, item in state.get("contextual_performance", {}).items()
        }

    @staticmethod
    def _fixed_plan() -> CollaborationPlan:
        """An actually executed fixed topology used only as an experiment control."""

        return CollaborationPlan(
            decision=CollaborationDecision.RECRUIT,
            mode=CollaborationMode.PARALLEL_WORKERS,
            rationale="fixed three-agent experimental control",
            assignments=[
                CollaborationAssignment(
                    agent_id="domain_specialist",
                    objective="independently identify the strongest bounded diagnostic direction",
                    required_capabilities={"specialized_diagnosis"},
                    context_policy=ContextPolicy.FACTS_ONLY,
                    independent=True,
                ),
                CollaborationAssignment(
                    agent_id="evidence_auditor",
                    objective="independently audit evidence reliability and missing checks",
                    required_capabilities={"fact_verification"},
                    context_policy=ContextPolicy.FACTS_ONLY,
                    independent=True,
                ),
                CollaborationAssignment(
                    agent_id="operations_planner",
                    objective="independently compare safe and verifiable next actions",
                    required_capabilities={"operational_planning"},
                    context_policy=ContextPolicy.FACTS_ONLY,
                    independent=True,
                ),
            ],
            expected_information_gain=1.0,
            estimated_coordination_cost=0.39,
            stop_conditions=["one fixed control round completed"],
        )

    def _validated_assignments(
        self,
        state: CollaborationGraphState,
        plan: CollaborationPlan,
    ) -> list[CollaborationAssignment]:
        recruited = set(state.get("recruited_agents", []))
        remaining = max(0, int(state.get("max_agents", 4)) - len(recruited))
        selected: list[CollaborationAssignment] = []
        objective_keys: set[str] = set()
        performance = self._performance(state)
        minimum_size, mode_maximum = MODE_TEAM_SIZES[plan.mode]
        if int(state.get("round_index", 0)) > 0:
            minimum_size = 1
        for proposed in plan.assignments:
            if len(selected) >= min(remaining, mode_maximum):
                break
            key = _objective_key(proposed.objective)
            if not key or key in objective_keys:
                continue
            try:
                profile = self._directory.require(proposed.agent_id)
                valid = (
                    profile.agent_id not in recruited
                    and profile.agent_id not in {item.agent_id for item in selected}
                    and proposed.required_capabilities <= profile.capabilities
                )
            except ValueError:
                valid = False
                profile = None
            best_match = self._directory.select(
                proposed.required_capabilities,
                performance,
                exclude=recruited | {item.agent_id for item in selected},
                contextual_performance=self._contextual_performance(state),
                task_type=plan.mode.value,
            )
            if best_match is not None:
                profile = best_match
                valid = True
            if profile is None or not valid:
                continue
            independent = proposed.independent or plan.mode in {
                CollaborationMode.INDEPENDENT_REVIEW,
                CollaborationMode.DIVERSE_EXPLORATION,
            }
            context_policy = proposed.context_policy
            if independent and context_policy is ContextPolicy.FULL_HANDOFF:
                context_policy = ContextPolicy.FACTS_ONLY
            selected.append(
                proposed.model_copy(
                    update={
                        "agent_id": profile.agent_id,
                        "independent": independent,
                        "context_policy": context_policy,
                    }
                )
            )
            recruited.add(profile.agent_id)
            objective_keys.add(key)
        return selected if len(selected) >= minimum_size else []

    async def _plan(self, state: CollaborationGraphState) -> dict[str, Any]:
        performance = self._performance(state)
        coordinator_calls = int(state.get("coordinator_calls", 0))
        structure = state.get("handoff", {}).get("coordination_structure", {})
        pending_assignments = [
            CollaborationAssignment.model_validate(item)
            for item in state.get("pending_assignments", [])
        ]
        if structure.get("human_authority_required"):
            plan = CollaborationPlan(
                decision=CollaborationDecision.REQUIRE_HUMAN,
                mode=CollaborationMode.SPECIALIST_HANDOFF,
                rationale=(
                    "the environment contract explicitly requires accountable "
                    "human authority; another model cannot remove that boundary"
                ),
            )
        elif pending_assignments:
            prior_synthesis = CollaborationSynthesis.model_validate(state["synthesis"])
            previous_plan = CollaborationPlan.model_validate(state["current_plan"])
            plan = CollaborationPlan(
                decision=CollaborationDecision.RECRUIT,
                mode=previous_plan.mode,
                rationale="previous synthesis identified a bounded complementary gap",
                assignments=pending_assignments,
                expected_information_gain=prior_synthesis.new_information_gain,
                estimated_coordination_cost=0.0,
                stop_conditions=["bounded complementary assignment completed"],
            )
        elif (
            state.get("topology") == CollaborationTopology.FIXED_THREE.value
            and int(state.get("round_index", 0)) == 0
        ):
            plan = self._fixed_plan()
        else:
            plan = await asyncio.wait_for(
                self._coordinator.plan(
                    handoff=state["handoff"],
                    available_agents=self._directory.catalog(performance),
                    prior_contributions=[
                        AgentContribution.model_validate(item)
                        for item in state.get("contributions", [])
                    ],
                    round_index=int(state.get("round_index", 0)),
                    recruited_agents=list(state.get("recruited_agents", [])),
                ),
                timeout=float(state.get("coordinator_timeout_seconds", 120.0)),
            )
            coordinator_calls += 1
        parallel_blocked = (
            state.get("topology") == CollaborationTopology.ADAPTIVE.value
            and
            plan.mode is CollaborationMode.PARALLEL_WORKERS
            and bool(structure.get("parallelism_known"))
            and len(structure.get("independent_workstreams", [])) < 2
        )
        if parallel_blocked:
            plan = plan.model_copy(
                update={
                    "assignments": [],
                    "rationale": (
                        f"{plan.rationale}; runtime dependency contract blocks parallel "
                        "fan-out until the serial prerequisite is observed"
                    ),
                }
            )
        assignments = self._validated_assignments(state, plan)
        base_cost = sum(
            self._directory.require(item.agent_id).base_coordination_cost
            for item in assignments
        )
        effective_cost = max(plan.estimated_coordination_cost, base_cost)
        plan = plan.model_copy(
            update={
                "assignments": assignments,
                "estimated_coordination_cost": effective_cost,
            }
        )
        if (
            plan.decision is CollaborationDecision.RECRUIT
            and not assignments
        ):
            plan = plan.model_copy(
                update={
                    "decision": CollaborationDecision.RETURN_TO_M2,
                    "rationale": (
                        f"{plan.rationale}; no executable assignment remained after "
                        "capability, topology, and team-size validation"
                    ),
                }
            )
        plans = [*state.get("plans", []), plan.model_dump(mode="json")]
        if plan.decision is CollaborationDecision.REQUIRE_HUMAN:
            return {
                "current_plan": plan.model_dump(mode="json"),
                "plans": plans,
                "coordinator_calls": coordinator_calls,
                "status": CollaborationStatus.HUMAN_REQUIRED.value,
                "response": "当前边界需要具备真实权限或责任的人类处理，现有证据已整理。",
                "progress_events": self._progress(
                    state,
                    stage="human_boundary",
                    message="已确认当前边界需要具备真实权限或责任的人类处理。",
                    status="human_required",
                ),
                "route": "finalize",
            }
        margin = plan.expected_information_gain - effective_cost
        if (
            plan.decision is not CollaborationDecision.RECRUIT
            or not assignments
            or margin <= float(state.get("minimum_gain_margin", 0.05))
        ):
            return {
                "current_plan": plan.model_dump(mode="json"),
                "plans": plans,
                "coordinator_calls": coordinator_calls,
                "status": CollaborationStatus.NO_BENEFIT.value,
                "response": "继续增加 Agent 的预期收益不足以覆盖协调成本，已停止扩员。",
                "progress_events": self._progress(
                    state,
                    stage="coordination_stopped",
                    message="额外协作收益不足，已停止扩员并保留当前 Case。",
                    status="no_benefit",
                ),
                "route": "finalize",
            }
        return {
            "current_plan": plan.model_dump(mode="json"),
            "coordinator_calls": coordinator_calls,
            "pending_assignments": [],
            "plans": plans,
            "coordination_cost": round(
                float(state.get("coordination_cost", 0.0))
                + effective_cost,
                4,
            ),
            "last_round_cost": effective_cost,
            "progress_events": self._progress(
                state,
                stage="agents_recruited",
                message=f"已按当前瓶颈招募 {len(assignments)} 个协作者，正在并行处理。",
                status="working",
                agents=[item.agent_id for item in assignments],
            ),
            "route": "dispatch",
        }

    async def _dispatch(self, state: CollaborationGraphState) -> dict[str, Any]:
        plan = CollaborationPlan.model_validate(state["current_plan"])

        async def run(assignment: CollaborationAssignment) -> AgentContribution:
            profile = self._directory.require(assignment.agent_id)
            try:
                contribution = await asyncio.wait_for(
                    self._worker.contribute(
                        profile=profile,
                        assignment=assignment,
                        context=build_worker_context(state["handoff"], assignment),
                    ),
                    timeout=float(state.get("worker_timeout_seconds", 120.0)),
                )
                sanitized_items = [
                    item.model_copy(update={"kind": ContributionKind.HYPOTHESIS})
                    if item.kind is ContributionKind.OBSERVATION
                    else item
                    for item in contribution.items
                ]
                limitations = list(contribution.limitations)
                if any(
                    item.kind is ContributionKind.OBSERVATION
                    for item in contribution.items
                ):
                    limitations.append(
                        "worker-generated observation was downgraded to hypothesis; "
                        "M2 tool verification is required"
                    )
                return contribution.model_copy(
                    update={"items": sanitized_items, "limitations": limitations}
                )
            except Exception:  # noqa: BLE001 - isolate a failed collaborator
                return AgentContribution(
                    assignment_id=assignment.assignment_id,
                    agent_id=assignment.agent_id,
                    limitations=["collaborator execution failed"],
                    error="COLLABORATOR_FAILURE",
                )

        round_items = await asyncio.gather(*(run(item) for item in plan.assignments))
        prior_statements = {
            _objective_key(item.get("statement", ""))
            for contribution in state.get("contributions", [])
            for item in contribution.get("items", [])
            if item.get("statement")
        }
        round_statements = [
            _objective_key(item.statement)
            for contribution in round_items
            for item in contribution.items
            if item.statement
        ]
        novel = {item for item in round_statements if item not in prior_statements}
        round_novelty = len(novel) / max(1, len(round_statements))
        return {
            "contributions": [
                *state.get("contributions", []),
                *(item.model_dump(mode="json") for item in round_items),
            ],
            "recruited_agents": [
                *state.get("recruited_agents", []),
                *(item.agent_id for item in plan.assignments),
            ],
            "round_index": int(state.get("round_index", 0)) + 1,
            "round_novelty_proxy": round(round_novelty, 4),
            "progress_events": self._progress(
                state,
                stage="contributions_received",
                message="协作者已返回，正在检查冲突、证据缺口和可执行性。",
                status="working",
                agents=[item.agent_id for item in round_items],
            ),
        }

    @staticmethod
    def _enforce_conflict_policy(
        synthesis: CollaborationSynthesis,
    ) -> CollaborationSynthesis:
        requests = list(synthesis.evidence_requests)
        normalized: list[CollaborationConflict] = []
        policy = {
            ConflictType.FACT: ("reacquire_authoritative_evidence", True),
            ConflictType.HYPOTHESIS: ("preserve_competing_explanations", False),
            ConflictType.ACTION: ("compare_risk_time_cost_reversibility_information", False),
            ConflictType.GOAL: ("return_to_user_goal_and_hard_constraints", True),
        }
        for conflict in synthesis.conflicts:
            strategy, blocks = policy[conflict.conflict_type]
            item = conflict.model_copy(
                update={
                    "resolution_strategy": strategy,
                    "blocks_action": conflict.blocks_action or blocks,
                }
            )
            normalized.append(item)
            if conflict.conflict_type in {ConflictType.FACT, ConflictType.HYPOTHESIS}:
                requests.extend(conflict.distinguishing_evidence)
        return synthesis.model_copy(
            update={
                "conflicts": normalized,
                "evidence_requests": list(dict.fromkeys(requests)),
            }
        )

    @staticmethod
    def _shared_source_warnings(
        contributions: list[AgentContribution],
    ) -> list[str]:
        sources: dict[str, set[str]] = defaultdict(set)
        for contribution in contributions:
            for item in contribution.items:
                for ref in item.basis_refs:
                    sources[ref].add(contribution.agent_id)
        return [
            f"shared evidence source {ref!r} is reused by {len(agents)} agents; "
            "their agreement is not independent corroboration"
            for ref, agents in sorted(sources.items())
            if len(agents) > 1
        ]

    async def _synthesize(self, state: CollaborationGraphState) -> dict[str, Any]:
        current_plan = CollaborationPlan.model_validate(state["current_plan"])
        current_assignment_ids = {
            item.assignment_id for item in current_plan.assignments
        }
        current_round = [
            AgentContribution.model_validate(item)
            for item in state["contributions"]
            if item.get("assignment_id") in current_assignment_ids
        ]
        if current_round and all(item.error for item in current_round):
            synthesis = CollaborationSynthesis(
                round_decision=RoundDecision.COMPLETE,
                summary=(
                    "All collaborators in the current bounded round failed or timed out; "
                    "no advice is promoted to M2."
                ),
                residual_risk=1.0,
            )
            return {
                "synthesis": synthesis.model_dump(mode="json"),
                "pending_assignments": [],
                "status": CollaborationStatus.EXHAUSTED.value,
                "route": "finalize",
                "progress_events": self._progress(
                    state,
                    stage="collaboration_exhausted",
                    message="本轮协作者均未成功返回，已停止扩员并保留 Case。",
                    status="exhausted",
                ),
            }
        synthesis = await asyncio.wait_for(
            self._coordinator.synthesize(
                handoff=state["handoff"],
                plans=[
                    CollaborationPlan.model_validate(item) for item in state["plans"]
                ],
                contributions=[
                    AgentContribution.model_validate(item)
                    for item in state["contributions"]
                ],
                round_index=int(state["round_index"]),
            ),
            timeout=float(state.get("coordinator_timeout_seconds", 120.0)),
        )
        coordinator_calls = int(state.get("coordinator_calls", 0)) + 1
        synthesis = self._enforce_conflict_policy(synthesis)
        contributions = [
            AgentContribution.model_validate(item) for item in state["contributions"]
        ]
        synthesis = synthesis.model_copy(
            update={
                "shared_source_warnings": list(
                    dict.fromkeys(
                        [
                            *synthesis.shared_source_warnings,
                            *self._shared_source_warnings(contributions),
                        ]
                    )
                )
            }
        )
        conflict_types = {item.conflict_type for item in synthesis.conflicts}
        if ConflictType.GOAL in conflict_types:
            status = CollaborationStatus.GOAL_ALIGNMENT_REQUIRED
            route = "finalize"
        elif ConflictType.FACT in conflict_types:
            status = CollaborationStatus.EVIDENCE_REQUIRED
            route = "finalize"
        elif synthesis.round_decision is RoundDecision.REQUIRE_HUMAN:
            status = CollaborationStatus.HUMAN_REQUIRED
            route = "finalize"
        elif synthesis.round_decision is RoundDecision.NEED_EVIDENCE:
            status = CollaborationStatus.EVIDENCE_REQUIRED
            route = "finalize"
        elif synthesis.round_decision is RoundDecision.EXPAND:
            latest_plan = CollaborationPlan.model_validate(state["current_plan"])
            can_expand = (
                state.get("topology") == CollaborationTopology.ADAPTIVE.value
                and int(state["round_index"]) < int(state.get("max_rounds", 2))
                and len(state.get("recruited_agents", [])) < int(state.get("max_agents", 4))
                and synthesis.new_information_gain
                > latest_plan.estimated_coordination_cost
                and float(state.get("round_novelty_proxy", 0.0))
                > float(state.get("last_round_cost", 0.0))
                and bool(synthesis.additional_assignments)
            )
            status = (
                CollaborationStatus.EXHAUSTED
                if not can_expand
                else CollaborationStatus.RUNNING
            )
            route = "plan" if can_expand else "finalize"
        else:
            status = CollaborationStatus.READY_FOR_M2
            route = "finalize"
        return {
            "synthesis": synthesis.model_dump(mode="json"),
            "coordinator_calls": coordinator_calls,
            "pending_assignments": (
                [
                    item.model_dump(mode="json")
                    for item in synthesis.additional_assignments
                ]
                if route == "plan"
                else []
            ),
            "status": status.value,
            "route": route,
            "progress_events": self._progress(
                state,
                stage="collaboration_synthesized",
                message=(
                    "协作意见已完成证据化合成，正在决定回交、取证或停止。"
                ),
                status="working" if route == "plan" else status.value,
            ),
        }

    def _finalize(self, state: CollaborationGraphState) -> dict[str, Any]:
        status = CollaborationStatus(state.get("status", CollaborationStatus.NO_BENEFIT))
        synthesis_raw = state.get("synthesis")
        if synthesis_raw:
            synthesis = CollaborationSynthesis.model_validate(synthesis_raw)
            packet = M2ResumePacket(
                collaboration_id=state["collaboration_id"],
                summary=synthesis.summary,
                contributions=[
                    AgentContribution.model_validate(item)
                    for item in state.get("contributions", [])
                ],
                preserved_hypotheses=synthesis.preserved_hypotheses,
                recommended_actions=synthesis.recommended_actions,
                evidence_requests=synthesis.evidence_requests,
                conflicts=synthesis.conflicts,
                source_case_revision=int(
                    state.get("handoff", {}).get("case_revision", 0)
                ),
            )
            messages = {
                CollaborationStatus.READY_FOR_M2: "协作已形成可验证的下一步，已交回主处理流程。",
                CollaborationStatus.EVIDENCE_REQUIRED: "协作发现关键证据冲突，需要先重新取证。",
                CollaborationStatus.GOAL_ALIGNMENT_REQUIRED: "协作发现目标冲突，需要先和你重新确认目标。",
                CollaborationStatus.HUMAN_REQUIRED: "当前边界需要具备真实权限或责任的人类处理。",
            }
            return {
                "resume_packet": packet.model_dump(mode="json"),
                "response": messages.get(
                    status, "本轮协作已结束，现有证据和建议已保留。"
                ),
                "progress_events": self._progress(
                    state,
                    stage=status.value,
                    message=messages.get(
                        status, "本轮协作已结束，现有证据和建议已保留。"
                    ),
                    status=status.value,
                ),
            }
        return {
            "resume_packet": None,
            "response": state.get("response") or "本轮没有启动额外协作。",
            "progress_events": self._progress(
                state,
                stage=status.value,
                message=state.get("response") or "本轮没有启动额外协作。",
                status=status.value,
            ),
        }


__all__ = ["AdaptiveCollaborationGraph", "CollaborationGraphState"]
