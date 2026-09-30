"""LangGraph M2 closed loop: explain, sample, act, observe, update."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.tools import (
    DataSource,
    OperationMode,
    ResourceRationalToolSelector,
    RiskLevel,
    ToolCategory,
    ToolContext,
    ToolInvocation,
    ToolNeed,
    ToolResult,
    ToolStatus,
    VerificationRequest,
)
from core.tools.sanitization import allowlisted_projection

from .events import merge_resolution_context
from .models import DecisionEvent, ResolutionDecision, ResolutionPlan, ResolutionStatus
from .planner import ResolutionPlanner
from .verification import validate_criterion_verifications


def _declarative_bridge(text: str) -> str:
    """Keep only statements before a model leaks a question into ``response``.

    ``user_questions`` is the authoritative, budgeted request channel. Treating
    a second natural-language question in ``response`` as another request makes
    the user answer the same thing twice and bypasses M1's interaction budget.
    This is a structural contract check, not domain keyword matching.
    """

    value = (text or "").strip()
    question_positions = [
        position
        for mark in ("?", "？")
        if (position := value.find(mark)) >= 0
    ]
    if not question_positions:
        return value
    prefix = value[: min(question_positions)]
    boundaries = [match.end() for match in re.finditer(r"[。！!]|\n+", prefix)]
    return prefix[: boundaries[-1]].strip() if boundaries else ""


class ResolutionGraphState(TypedDict, total=False):
    resolution_context: dict[str, Any]
    case_id: str
    thread_id: str
    trace_id: str
    permissions: list[str]
    confirmation_granted: bool
    episode_decision_budget: int
    episode_decisions: int
    cycles: int
    is_resume: bool
    resume_event: str
    plan: dict[str, Any]
    selected_tool_id: str
    selection_reason: str
    tool_history: list[dict[str, Any]]
    belief_history: list[dict[str, Any]]
    progress_history: list[dict[str, Any]]
    progress_events: list[dict[str, Any]]
    pending_verification_targets: list[str]
    pending_verification_requests: list[dict[str, Any]]
    decision_event: str
    decision_events: list[dict[str, Any]]
    control_events: list[dict[str, Any]]
    guardrail_feedback: str
    guardrail_events: list[dict[str, Any]]
    status: str
    response: str
    collaboration_reason: str | None
    tenant_id: str
    actor_id: str
    consumed_event_ids: list[str]
    last_event_sequence: int
    stalled_steps: int
    failure_memory_enabled: bool
    collaboration_mode: str
    considered_experience_ids: list[str]
    adopted_experience_ids: list[str]
    route: str


class AdaptiveResolutionGraph:
    def __init__(
        self,
        *,
        planner: ResolutionPlanner,
        tool_runtime: Any,
        checkpointer: Any,
        event_store: Any | None = None,
    ):
        self._planner = planner
        self._runtime = tool_runtime
        self._event_store = event_store
        self._selector = ResourceRationalToolSelector(
            tool_runtime.registry,
            tool_runtime.policy,
        )
        graph = StateGraph(ResolutionGraphState)
        graph.add_node("bootstrap", self._bootstrap)
        graph.add_node("ingest_events", self._ingest_events)
        graph.add_node("ingest_before_action", self._ingest_before_action)
        graph.add_node("plan", self._plan)
        graph.add_node("select_tool", self._select_tool)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("auto_verify", self._auto_verify)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "bootstrap")
        graph.add_edge("bootstrap", "ingest_events")
        graph.add_conditional_edges(
            "ingest_events",
            lambda state: state["route"],
            {"plan": "plan", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "use_tool": "ingest_before_action",
                "replan": "plan",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "ingest_before_action",
            lambda state: state["route"],
            {"select": "select_tool", "plan": "plan", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "select_tool",
            lambda state: state["route"],
            {"execute": "execute_tool", "plan": "plan", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "execute_tool",
            lambda state: state["route"],
            {
                "verify": "auto_verify",
                "ingest": "ingest_events",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "auto_verify",
            lambda state: state["route"],
            {"ingest": "ingest_events", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        self.compiled = graph.compile(checkpointer=checkpointer)

    def is_synthetic_case(self, case_id: str) -> bool:
        """Expose scenario provenance without leaking the runtime implementation."""

        return self._runtime.scenarios.has_case(case_id)

    def _tool_definitions(self, state: ResolutionGraphState) -> list[dict[str, Any]]:
        synthetic = self._runtime.scenarios.has_case(state["case_id"])
        context = self._context(state)
        return [
            spec.public_definition()
            for spec in self._runtime.registry.list()
            if synthetic or spec.data_source is not DataSource.SYNTHETIC
            if spec.is_available(context)
        ]

    @staticmethod
    def _append_decision_event(
        state: ResolutionGraphState,
        event: DecisionEvent,
        **details: Any,
    ) -> list[dict[str, Any]]:
        events = list(state.get("decision_events", []))
        events.append(
            {
                "event": event.value,
                "after_cycle": int(state.get("cycles", 0)),
                **details,
            }
        )
        return events

    @staticmethod
    def _history_item(
        *,
        cycle: int,
        spec: Any,
        arguments: dict[str, Any],
        result: ToolResult,
        selection_reason: str,
        expected_observation: str | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        result_payload = result.model_dump(mode="json")
        return {
            "cycle": cycle,
            "tool_id": spec.tool_id,
            "arguments": arguments,
            "canonical_arguments": json.dumps(
                arguments, ensure_ascii=False, sort_keys=True, default=str
            ),
            "selection_reason": selection_reason,
            "expected_observation": expected_observation,
            "category": spec.category.value,
            "tool_contract_version": spec.contract_version,
            "tool_capabilities": sorted(spec.capabilities),
            "required_permissions": sorted(spec.required_permissions),
            "operation_mode": spec.operation_mode.value,
            "experience_safe_arguments": allowlisted_projection(
                arguments, spec.experience_argument_fields
            ),
            "experience_safe_data": allowlisted_projection(
                result_payload.get("data"), spec.experience_result_fields
            ),
            **metadata,
            **result_payload,
        }
    @staticmethod
    def _append_progress_event(
        state: ResolutionGraphState,
        *,
        stage: str,
        message: str,
        status: str,
        tool_id: str | None = None,
    ) -> list[dict[str, Any]]:
        events = list(state.get("progress_events", []))
        events.append(
            {
                "cycle": int(state.get("cycles", 0)),
                "stage": stage,
                "message": message,
                "status": status,
                "tool_id": tool_id,
            }
        )
        return events

    async def _bootstrap(self, state: ResolutionGraphState) -> dict[str, Any]:
        """Run explicitly declared safe observations before the first model call."""

        if state.get("is_resume", False):
            event = DecisionEvent(
                state.get("resume_event", DecisionEvent.ENVIRONMENT_UPDATED.value)
            )
            message = {
                DecisionEvent.USER_EVIDENCE_UPDATED: "已收到你补充的信息，正在据此更新判断。",
                DecisionEvent.COLLABORATION_RESULT: "协作建议已返回，正在通过主流程验证，暂不把建议视为事实。",
                DecisionEvent.ENVIRONMENT_UPDATED: "已收到新的环境状态，正在继续处理。",
            }.get(event, "Case 已有新信息，正在继续处理。")
            return {
                "decision_event": event.value,
                "decision_events": self._append_decision_event(state, event),
                "progress_events": self._append_progress_event(
                    state,
                    stage="evidence_update",
                    message=message,
                    status="working",
                ),
            }

        constraints = state["resolution_context"].get("action_constraints", {})
        whitelist = constraints.get("pre_resolution_whitelist", {})
        allowed_categories = set(whitelist.get("tool_categories", []))
        allowed_modes = set(whitelist.get("operation_modes", []))
        specs = [
            spec
            for spec in self._runtime.registry.list()
            if spec.bootstrap_safe
            and (
                self._runtime.scenarios.has_case(state["case_id"])
                or spec.data_source is not DataSource.SYNTHETIC
            )
            and spec.is_available(self._context(state))
            and spec.category.value in allowed_categories
            and spec.operation_mode.value in allowed_modes
        ]
        invocations = [
            ToolInvocation(
                tool_id=spec.tool_id,
                arguments=dict(spec.bootstrap_arguments),
                context=self._context(state),
            )
            for spec in specs
        ]
        results = (
            await asyncio.gather(
                *(self._runtime.execute(invocation) for invocation in invocations)
            )
            if invocations
            else []
        )
        history = list(state.get("tool_history", []))
        progress = list(state.get("progress_history", []))
        for spec, invocation, result in zip(specs, invocations, results, strict=True):
            history.append(
                self._history_item(
                    cycle=0,
                    spec=spec,
                    arguments=invocation.arguments,
                    result=result,
                    selection_reason="declared safe bootstrap observation",
                    bootstrap=True,
                )
            )
            progress.append(
                {
                    "cycle": 0,
                    "state": "observed" if result.success else "stalled",
                    "tool_id": spec.tool_id,
                    "status": result.status.value,
                    "bootstrap": True,
                }
            )
        event = (
            DecisionEvent.TOOL_FAILURE
            if any(not result.success for result in results)
            else DecisionEvent.BOOTSTRAP_COMPLETE
            if results
            else DecisionEvent.INITIAL
        )
        return {
            "tool_history": history,
            "progress_history": progress,
            "decision_event": event.value,
            "decision_events": self._append_decision_event(
                state,
                event,
                tool_ids=[spec.tool_id for spec in specs],
                failed_tool_ids=[
                    spec.tool_id
                    for spec, result in zip(specs, results, strict=True)
                    if not result.success
                ],
            ),
            "progress_events": self._append_progress_event(
                state,
                stage="initial_observation",
                message="已完成初始状态检查，正在确定最值得执行的下一步。",
                status="working",
            ),
        }

    async def _consume_case_events(
        self, state: ResolutionGraphState, *, before_action: bool
    ) -> dict[str, Any]:
        if self._event_store is None:
            return {"route": "select" if before_action else "plan"}
        events = await self._event_store.drain(
            state["thread_id"],
            after_sequence=int(state.get("last_event_sequence", 0)),
        )
        if not events:
            return {"route": "select" if before_action else "plan"}
        merged, event, cancelled, event_ids = merge_resolution_context(
            state.get("resolution_context"),
            state["resolution_context"],
            events=events,
        )
        consumed = list(state.get("consumed_event_ids", [])) + event_ids
        last_sequence = max(item.sequence for item in events)
        if cancelled:
            return {
                "resolution_context": merged,
                "consumed_event_ids": consumed,
                "last_event_sequence": last_sequence,
                "status": ResolutionStatus.CANCELLED.value,
                "response": "已停止当前处理并保留 Case 进度；之后可以从这里继续。",
                "decision_event": DecisionEvent.USER_CANCELLED.value,
                "decision_events": self._append_decision_event(
                    state,
                    DecisionEvent.USER_CANCELLED,
                    event_ids=event_ids,
                ),
                "progress_events": self._append_progress_event(
                    state,
                    stage="cancelled",
                    message="已按你的要求停止，现有进度已经保留。",
                    status="cancelled",
                ),
                "route": "finalize",
            }
        decision_event = event or DecisionEvent.USER_EVIDENCE_UPDATED
        message = (
            "已收到目标变化，正在停止旧方向并按新目标重新判断。"
            if decision_event is DecisionEvent.USER_GOAL_UPDATED
            else "已收到新的环境结果，正在更新判断。"
            if decision_event is DecisionEvent.ENVIRONMENT_UPDATED
            else "已合并你刚补充的信息，正在更新判断。"
        )
        return {
            "resolution_context": merged,
            "consumed_event_ids": consumed,
            "last_event_sequence": last_sequence,
            "decision_event": decision_event.value,
            "decision_events": self._append_decision_event(
                state,
                decision_event,
                event_ids=event_ids,
            ),
            "progress_events": self._append_progress_event(
                state,
                stage="evidence_update",
                message=message,
                status="working",
            ),
            "route": "plan",
        }

    async def _ingest_events(self, state: ResolutionGraphState) -> dict[str, Any]:
        return await self._consume_case_events(state, before_action=False)

    async def _ingest_before_action(
        self, state: ResolutionGraphState
    ) -> dict[str, Any]:
        return await self._consume_case_events(state, before_action=True)

    async def _plan(self, state: ResolutionGraphState) -> dict[str, Any]:
        if int(state.get("stalled_steps", 0)) >= 3:
            return {
                "status": ResolutionStatus.COLLABORATION_REQUIRED.value,
                "response": "当前方向连续没有取得进展，已保留证据并请求不同方向的协作。",
                "collaboration_reason": "three consecutive tool steps produced no progress",
                "collaboration_mode": "diverse_exploration",
                "decision_event": DecisionEvent.COLLABORATION_TRIGGERED.value,
                "decision_events": self._append_decision_event(
                    state,
                    DecisionEvent.COLLABORATION_TRIGGERED,
                    trigger="no_progress",
                ),
                "route": "finalize",
            }
        episode_decisions = int(state.get("episode_decisions", 0)) + 1
        if episode_decisions > int(state.get("episode_decision_budget", 6)):
            decision_events = self._append_decision_event(
                state,
                DecisionEvent.EPISODE_BUDGET_REACHED,
            )
            return {
                "status": ResolutionStatus.PAUSED.value,
                "response": "当前还没有足够的新信息继续推进，现有进度已保留；收到新信息后会从这里继续。",
                "collaboration_reason": None,
                "decision_event": DecisionEvent.EPISODE_BUDGET_REACHED.value,
                "decision_events": decision_events,
                "route": "finalize",
            }
        cycles = int(state.get("cycles", 0)) + 1
        plan = await self._planner.plan(
            resolution_context=state["resolution_context"],
            tools=self._tool_definitions(state),
            tool_history=list(state.get("tool_history", [])),
            belief_history=list(state.get("belief_history", [])),
            guardrail_feedback=str(state.get("guardrail_feedback", "")),
            decision_event=str(
                state.get("decision_event", DecisionEvent.INITIAL.value)
            ),
        )
        candidates = {
            str(item.get("experience_id"))
            for item in state["resolution_context"].get("candidate_experiences", [])
            if isinstance(item, dict) and item.get("experience_id")
        }
        requested_considered = set(plan.considered_experience_ids)
        requested_adopted = set(plan.adopted_experience_ids)
        # Older M7 planners used one ambiguous field. Treat it as both considered
        # and adopted, but only when the ID was actually retrieved for this Case.
        requested_considered.update(plan.experience_source_ids)
        requested_adopted.update(plan.experience_source_ids)
        considered = requested_considered & candidates
        adopted = requested_adopted & considered
        plan = plan.model_copy(
            update={
                "considered_experience_ids": sorted(considered),
                "adopted_experience_ids": sorted(adopted),
                "experience_source_ids": sorted(adopted),
            }
        )
        belief_history = list(state.get("belief_history", []))
        belief_history.append(
            {
                "cycle": cycles,
                "provisional_explanations": [
                    item.model_dump(mode="json") for item in plan.provisional_explanations
                ],
                "trigger": (
                    state.get("decision_event", DecisionEvent.INITIAL.value)
                ),
            }
        )
        guardrail_events = list(state.get("guardrail_events", []))
        has_independent_observation = any(
            item.get("category") in {"observe", "verify"}
            and item.get("status") == ToolStatus.SUCCEEDED.value
            and item.get("data") is not None
            for item in state.get("tool_history", [])
        )
        criterion_verifications, missing_criteria = validate_criterion_verifications(
            context=state["resolution_context"],
            tool_history=list(state.get("tool_history", [])),
            declared=plan.criterion_verifications,
        )
        if criterion_verifications != plan.criterion_verifications:
            plan = plan.model_copy(
                update={"criterion_verifications": criterion_verifications}
            )
        if plan.decision is ResolutionDecision.RESOLVE and (
            state.get("pending_verification_targets")
            or not plan.verification_complete
            or not has_independent_observation
            or bool(missing_criteria)
        ):
            feedback = (
                "resolution rejected: independent verification is incomplete or some "
                "declared verification targets or user success criteria remain "
                f"unverified ({missing_criteria}); choose an observe/verify capability "
                "or collaborate"
            )
            guardrail_events.append(
                {"cycle": cycles, "code": "UNVERIFIED_RESOLUTION_REJECTED"}
            )
            decision_events = self._append_decision_event(
                {**state, "cycles": cycles},
                DecisionEvent.GUARDRAIL_REJECTION,
                code="UNVERIFIED_RESOLUTION_REJECTED",
            )
            return {
                "cycles": cycles,
                "episode_decisions": episode_decisions,
                "plan": plan.model_dump(mode="json"),
                "belief_history": belief_history,
                "guardrail_feedback": feedback,
                "guardrail_events": guardrail_events,
                "decision_event": DecisionEvent.GUARDRAIL_REJECTION.value,
                "decision_events": decision_events,
                "route": "replan",
            }
        return {
            "cycles": cycles,
            "episode_decisions": episode_decisions,
            "plan": plan.model_dump(mode="json"),
            "belief_history": belief_history,
            "guardrail_feedback": "",
            "guardrail_events": guardrail_events,
            "considered_experience_ids": sorted(
                set(state.get("considered_experience_ids", [])) | considered
            ),
            "adopted_experience_ids": sorted(
                set(state.get("adopted_experience_ids", [])) | adopted
            ),
            "route": (
                "use_tool"
                if plan.decision is ResolutionDecision.USE_TOOL
                else "finalize"
            ),
        }

    @staticmethod
    def _route_after_plan(state: ResolutionGraphState) -> str:
        return state["route"]

    def _context(self, state: ResolutionGraphState) -> ToolContext:
        return ToolContext(
            case_id=state["case_id"],
            thread_id=state["thread_id"],
            trace_id=state["trace_id"],
            actor_id=state.get("actor_id", "m2-primary-case-owner"),
            tenant_id=state.get("tenant_id", "local"),
            permissions=set(state.get("permissions", [])),
            synthetic=self._runtime.scenarios.has_case(state["case_id"]),
            confirmation_granted=bool(state.get("confirmation_granted", False)),
            allow_external_writes=False,
        )

    @staticmethod
    def _allowed_modes(state: ResolutionGraphState, plan: ResolutionPlan) -> set[OperationMode]:
        constraints = state["resolution_context"].get("action_constraints", {})
        allowed = {
            OperationMode(item)
            for item in constraints.get("pre_resolution_whitelist", {}).get(
                "operation_modes", [OperationMode.READ_ONLY.value]
            )
        }
        conditional = constraints.get("conditional_operation_modes", {})
        if OperationMode.SIMULATED_WRITE.value in conditional:
            allowed.add(OperationMode.SIMULATED_WRITE)
        return allowed & set(plan.allowed_modes)

    async def _select_tool(self, state: ResolutionGraphState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["plan"])
        if not plan.required_capabilities:
            feedback = (
                "use_tool rejected: required_capabilities is empty; describe the "
                "needed capability from the published tool contracts without "
                "inventing a tool or inferring it from keywords"
            )
            guardrail_events = list(state.get("guardrail_events", []))
            guardrail_events.append(
                {"cycle": state["cycles"], "code": "PLAN_CAPABILITY_MISSING"}
            )
            return {
                "guardrail_feedback": feedback,
                "guardrail_events": guardrail_events,
                "decision_event": DecisionEvent.GUARDRAIL_REJECTION.value,
                "decision_events": self._append_decision_event(
                    state,
                    DecisionEvent.GUARDRAIL_REJECTION,
                    code="PLAN_CAPABILITY_MISSING",
                ),
                "route": "plan",
            }
        user_state = state["resolution_context"].get("user_state", {})
        resolution_policy = state["resolution_context"].get(
            "provisional_resolution_policy", {}
        )
        deadline = user_state.get("deadline") or resolution_policy.get("deadline")
        low_patience = user_state.get("patience") == "low"
        low_effort = user_state.get("effort_willingness") == "low"
        frustrated = user_state.get("frustration") == "high"
        latency_weight = 0.55 if deadline or low_patience else 0.15
        burden_weight = 1.1 if low_effort or frustrated else 0.5
        control_events = list(state.get("control_events", []))
        if deadline or low_patience or low_effort or frustrated:
            control_events.append(
                {
                    "mechanism": "case_user_state",
                    "cycle": state.get("cycles", 0),
                    "inputs": {
                        "deadline": deadline,
                        "patience": user_state.get("patience"),
                        "effort_willingness": user_state.get("effort_willingness"),
                        "frustration": user_state.get("frustration"),
                    },
                    "behavior_changes": {
                        "latency_weight": latency_weight,
                        "burden_weight": burden_weight,
                    },
                }
            )
        assessment = self._selector.assess(
            ToolNeed(
                capabilities=plan.required_capabilities,
                allowed_modes=self._allowed_modes(state, plan),
                max_risk=plan.max_risk,
                latency_weight=latency_weight,
                burden_weight=burden_weight,
            ),
            self._context(state),
        )
        selection = assessment.ranked[0] if assessment.ranked else None
        if selection is None:
            authority_codes = {
                "PERMISSION_DENIED",
                "EXTERNAL_WRITE_DISABLED",
                "CONFIRMATION_REQUIRED",
                "EXTERNAL_SIDE_EFFECT_DISABLED",
            }
            authority_exclusions = [
                item for item in assessment.exclusions if item.code in authority_codes
            ]
            authority_required = bool(authority_exclusions)
            coordination = plan.coordination_structure.model_copy(
                update={
                    "blocking_evidence": list(
                        dict.fromkeys(
                            [
                                *plan.coordination_structure.blocking_evidence,
                                *(
                                    f"{item.tool_id}:{item.code}"
                                    for item in authority_exclusions
                                ),
                            ]
                        )
                    ),
                    "human_authority_required": (
                        plan.coordination_structure.human_authority_required
                        or authority_required
                    ),
                }
            )
            plan = plan.model_copy(update={"coordination_structure": coordination})
            boundary = "authorization_boundary" if authority_required else "capability_boundary"
            return {
                "plan": plan.model_dump(mode="json"),
                "status": ResolutionStatus.COLLABORATION_REQUIRED.value,
                "response": (
                    "当前缺少继续操作所需的工具或权限。我已经保留现有证据，"
                    "你不需要重复前面的步骤；条件具备后会从这里继续。"
                ),
                "collaboration_reason": (
                    "matching tools are blocked by runtime authorization"
                    if authority_required
                    else "no available tool satisfies required capabilities"
                ),
                "collaboration_mode": "specialist_handoff",
                "decision_event": DecisionEvent.COLLABORATION_TRIGGERED.value,
                "decision_events": self._append_decision_event(
                    state,
                    DecisionEvent.COLLABORATION_TRIGGERED,
                    trigger=boundary,
                ),
                "control_events": control_events,
                "route": "finalize",
            }
        selected_spec = self._runtime.registry.require(selection.tool_id)
        collaboration_preference = str(
            user_state.get("collaboration_preference")
            or resolution_policy.get("collaboration_mode")
            or "shared"
        )
        if (
            collaboration_preference == "step_by_step"
            and selected_spec.category is ToolCategory.ACT
            and not state.get("confirmation_granted", False)
        ):
            control_events.append(
                {
                    "mechanism": "initiative_mode",
                    "cycle": state.get("cycles", 0),
                    "inputs": {"initiative_mode": "step_by_step"},
                    "behavior_changes": {"action_requires_user_confirmation": True},
                }
            )
            return {
                "status": ResolutionStatus.WAITING_FOR_USER.value,
                "response": "检查已经完成。下一步需要执行一个动作；你确认后我再继续。",
                "decision_event": DecisionEvent.STAGE_COMPLETE.value,
                "decision_events": self._append_decision_event(
                    state,
                    DecisionEvent.STAGE_COMPLETE,
                    code="STEP_BY_STEP_CONFIRMATION_REQUIRED",
                ),
                "control_events": control_events,
                "route": "finalize",
            }
        return {
            "selected_tool_id": selection.tool_id,
            "selection_reason": selection.reason,
            "control_events": control_events,
            "route": "execute",
        }

    @staticmethod
    def _same_failed_call(
        history: list[dict[str, Any]],
        tool_id: str,
        arguments: dict[str, Any],
        collaboration_source_ids: list[str] | None = None,
    ) -> bool:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        matching = [
            (index, item)
            for index, item in enumerate(history)
            if item.get("tool_id") == tool_id
            and item.get("canonical_arguments") == canonical
            and item.get("status")
            in {
                ToolStatus.FAILED.value,
                ToolStatus.TIMED_OUT.value,
                ToolStatus.CONFLICT.value,
                ToolStatus.DENIED.value,
            }
            and item.get("error_code") != "REPEATED_FAILED_ACTION_BLOCKED"
        ]
        if not matching:
            return False
        failure_index, failure = matching[-1]
        if not bool(failure.get("retryable")):
            return True
        later = history[failure_index + 1 :]
        retry_condition_changed = any(
            bool(item.get("changed"))
            or bool(item.get("novel_evidence"))
            or bool(item.get("metadata", {}).get("retry_condition_changed"))
            for item in later
        )
        independently_reviewed = bool(collaboration_source_ids)
        return not (retry_condition_changed or independently_reviewed)

    @staticmethod
    def _evidence_signature(evidence: dict[str, Any]) -> str:
        """Compare semantic observations without volatile retrieval metadata."""

        stable = {
            "key": evidence.get("key"),
            "value": evidence.get("value"),
            "source": evidence.get("source"),
            "source_type": evidence.get("source_type"),
            "path": evidence.get("path"),
            "reliability": evidence.get("reliability"),
        }
        return json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _has_novel_evidence(
        cls, result: ToolResult, history: list[dict[str, Any]]
    ) -> bool:
        seen = {
            cls._evidence_signature(item)
            for previous in history
            for item in previous.get("evidence", [])
            if isinstance(item, dict)
        }
        return any(
            cls._evidence_signature(item.model_dump(mode="json")) not in seen
            for item in result.evidence
        )

    async def _execute_tool(self, state: ResolutionGraphState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["plan"])
        history = list(state.get("tool_history", []))
        tool_id = state["selected_tool_id"]
        arguments = plan.tool_arguments
        if state.get("failure_memory_enabled", True) and self._same_failed_call(
            history,
            tool_id,
            arguments,
            plan.collaboration_source_ids,
        ):
            result = ToolResult(
                tool_id=tool_id,
                invocation_id=f"blocked-repeat-{state['cycles']}",
                status=ToolStatus.DENIED,
                error_code="REPEATED_FAILED_ACTION_BLOCKED",
                error="same failed tool call is blocked until evidence changes the retry condition",
            )
        else:
            spec = self._runtime.registry.require(tool_id)
            idempotency_key = (
                f"{state['case_id']}:{state['cycles']}:{tool_id}"
                if spec.idempotency_key_required
                else None
            )
            result = await self._runtime.execute(
                ToolInvocation(
                    tool_id=tool_id,
                    arguments=arguments,
                    context=self._context(state),
                    idempotency_key=idempotency_key,
                    expected_observation=plan.expected_observation,
                )
            )
        spec = self._runtime.registry.require(tool_id)
        novel_evidence = self._has_novel_evidence(result, history)
        made_progress = bool(result.success and (result.changed or novel_evidence))
        history.append(
            self._history_item(
                cycle=int(state["cycles"]),
                spec=spec,
                arguments=arguments,
                result=result,
                selection_reason=state.get("selection_reason", ""),
                expected_observation=plan.expected_observation,
                collaboration_source_ids=plan.collaboration_source_ids,
                experience_source_ids=plan.adopted_experience_ids,
                novel_evidence=novel_evidence,
            )
        )
        progress = list(state.get("progress_history", []))
        progress.append(
            {
                "cycle": state["cycles"],
                "state": (
                    "advanced"
                    if made_progress
                    else "observed" if result.success else "stalled"
                ),
                "tool_id": tool_id,
                "status": result.status.value,
            }
        )
        pending_targets = list(state.get("pending_verification_targets", []))
        pending_requests = list(state.get("pending_verification_requests", []))
        if result.verification_required:
            pending_requests = [
                item.model_dump(mode="json") for item in result.verification_requests
            ]
            declared_targets = [item.target for item in result.verification_requests]
            if not declared_targets and isinstance(result.data, dict):
                declared_targets = result.data.get("verification_targets", [])
            pending_targets = list(dict.fromkeys(declared_targets)) or [
                "__independent_observation__"
            ]
        elif result.success and spec.category.value in {"observe", "verify"}:
            observed_path = str(arguments.get("path", ""))
            if not observed_path:
                pending_targets = []
            else:
                pending_targets = [
                    item
                    for item in pending_targets
                    if item not in {observed_path, "__independent_observation__"}
                ]
            pending_requests = [
                item
                for item in pending_requests
                if str(item.get("target", "")) != observed_path
            ]
        maxed = int(state.get("episode_decisions", 0)) >= int(
            state.get("episode_decision_budget", 6)
        )
        if not result.success:
            event = DecisionEvent.TOOL_FAILURE
            progress_message = "这一步没有成功，已保留失败记录并准备换方向。"
            progress_status = "adjusting"
        else:
            event = DecisionEvent.STAGE_COMPLETE
            progress_message = (
                "操作已被系统接受，正在等待生效并验证结果。"
                if result.status is ToolStatus.PENDING
                else "操作已执行，正在独立验证是否真正生效。"
                if spec.category.value == "act"
                else "已获得新的环境观察，正在更新判断。"
            )
            progress_status = "working"
        stalled_steps = 0 if made_progress else int(state.get("stalled_steps", 0)) + 1
        decision_events = self._append_decision_event(
            state,
            event,
            tool_id=tool_id,
            status=result.status.value,
        )
        route = "ingest"
        if pending_requests and (
            result.success or result.metadata.get("commit_outcome_uncertain") is True
        ):
            route = "verify"
        if maxed and route != "verify":
            route = "finalize"
        return {
            "tool_history": history,
            "progress_history": progress,
            "pending_verification_targets": pending_targets,
            "pending_verification_requests": pending_requests,
            "decision_event": event.value,
            "decision_events": decision_events,
            "progress_events": self._append_progress_event(
                state,
                stage="tool_result",
                message=progress_message,
                status=progress_status,
                tool_id=tool_id,
            ),
            "status": (
                ResolutionStatus.PAUSED.value
                if maxed and route == "finalize"
                else ResolutionStatus.INVESTIGATING.value
            ),
            "response": (
                "当前还没有足够的新信息继续推进，现有进度已保留；收到新信息后会从这里继续。"
                if maxed and route == "finalize"
                else ""
            ),
            "collaboration_reason": (
                None
            ),
            "stalled_steps": stalled_steps,
            "considered_experience_ids": sorted(
                set(state.get("considered_experience_ids", []))
                | set(plan.considered_experience_ids)
            ),
            "adopted_experience_ids": sorted(
                set(state.get("adopted_experience_ids", []))
                | set(plan.adopted_experience_ids)
            ),
            "route": route,
        }

    @staticmethod
    def _read_result_path(result: ToolResult, path: str) -> tuple[bool, Any]:
        current: Any = result.model_dump(mode="python")
        for part in (item for item in path.split(".") if item):
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current

    @classmethod
    def _expectation_matches(
        cls, request: VerificationRequest, result: ToolResult
    ) -> bool:
        expectation = request.expectation
        if expectation is None:
            return result.success
        exists, actual = cls._read_result_path(result, expectation.result_path)
        if expectation.operator == "exists":
            wanted = True if expectation.value is None else bool(expectation.value)
            return exists is wanted
        if not exists:
            return False
        if expectation.operator == "eq":
            return actual == expectation.value
        if expectation.operator == "ne":
            return actual != expectation.value
        if expectation.operator == "in":
            try:
                return actual in expectation.value
            except TypeError:
                return False
        return False

    async def _auto_verify(self, state: ResolutionGraphState) -> dict[str, Any]:
        """Execute tool-declared read-only verification without another LLM turn."""

        plan = ResolutionPlan.model_validate(state["plan"])
        requests = [
            VerificationRequest.model_validate(item)
            for item in state.get("pending_verification_requests", [])
        ]
        selections: list[tuple[VerificationRequest, Any, Any]] = []
        for request in requests:
            selection = self._selector.select(
                ToolNeed(
                    capabilities=request.required_capabilities,
                    allowed_modes={OperationMode.READ_ONLY},
                    max_risk=RiskLevel.LOW,
                ),
                self._context(state),
            )
            if selection is None:
                return {
                    "status": ResolutionStatus.COLLABORATION_REQUIRED.value,
                    "response": "动作已经执行，但当前没有安全的独立验证能力。",
                    "collaboration_reason": (
                        f"no read-only verification tool for target {request.target}"
                    ),
                    "route": "finalize",
                }
            spec = self._runtime.registry.require(selection.tool_id)
            if spec.category.value not in {"observe", "verify"}:
                return {
                    "status": ResolutionStatus.COLLABORATION_REQUIRED.value,
                    "response": "候选能力不是独立观察工具，无法安全完成验证。",
                    "collaboration_reason": (
                        f"verification capability selected non-observation tool {spec.tool_id}"
                    ),
                    "route": "finalize",
                }
            invocation = ToolInvocation(
                tool_id=spec.tool_id,
                arguments=request.arguments,
                context=self._context(state),
                expected_observation=request.expected_observation,
            )
            selections.append((request, spec, (selection, invocation)))

        results = await asyncio.gather(
            *(
                self._runtime.execute(selection_and_invocation[1])
                for _, _, selection_and_invocation in selections
            )
        )
        history = list(state.get("tool_history", []))
        progress = list(state.get("progress_history", []))
        remaining_targets: list[str] = []
        failed_targets: list[str] = []
        mismatched_targets: list[str] = []
        for (request, spec, selection_and_invocation), result in zip(
            selections, results, strict=True
        ):
            selection, invocation = selection_and_invocation
            matches = self._expectation_matches(request, result)
            history.append(
                self._history_item(
                    cycle=int(state["cycles"]),
                    spec=spec,
                    arguments=invocation.arguments,
                    result=result,
                    selection_reason=selection.reason,
                    expected_observation=request.expected_observation,
                    automatic_verification=True,
                    verification_target=request.target,
                    expectation_matched=matches,
                    collaboration_source_ids=plan.collaboration_source_ids,
                    experience_source_ids=plan.adopted_experience_ids,
                )
            )
            progress.append(
                {
                    "cycle": state["cycles"],
                    "state": "verified" if matches else "stalled",
                    "tool_id": spec.tool_id,
                    "status": result.status.value,
                    "verification_target": request.target,
                }
            )
            if not result.success:
                failed_targets.append(request.target)
                remaining_targets.append(request.target)
            elif not matches:
                mismatched_targets.append(request.target)
                remaining_targets.append(request.target)

        if failed_targets:
            event = DecisionEvent.TOOL_FAILURE
            progress_message = "验证工具未能完成检查，正在改用其他方向。"
            progress_status = "adjusting"
        elif mismatched_targets:
            event = DecisionEvent.PREDICTION_MISMATCH
            progress_message = "当前结果还没有达到预期，正在重新判断，不会把已提交当成已解决。"
            progress_status = "adjusting"
        else:
            event = DecisionEvent.VERIFICATION_COMPLETE
            progress_message = "独立验证已经完成，正在确认是否满足你的目标。"
            progress_status = "verifying"
        event_details = {
            "targets": [request.target for request in requests],
            "failed_targets": failed_targets,
            "mismatched_targets": mismatched_targets,
        }
        maxed = int(state.get("episode_decisions", 0)) >= int(
            state.get("episode_decision_budget", 6)
        )
        return {
            "tool_history": history,
            "progress_history": progress,
            "pending_verification_targets": remaining_targets,
            "pending_verification_requests": [
                request.model_dump(mode="json")
                for request in requests
                if request.target in remaining_targets
            ],
            "decision_event": event.value,
            "decision_events": self._append_decision_event(state, event, **event_details),
            "progress_events": self._append_progress_event(
                state,
                stage="verification",
                message=progress_message,
                status=progress_status,
            ),
            "status": (
                ResolutionStatus.PAUSED.value
                if maxed
                else ResolutionStatus.INVESTIGATING.value
            ),
            "response": (
                "本次验证已经完成，现有进度已保留；收到新的结果后会从这里继续。"
                if maxed
                else ""
            ),
            "collaboration_reason": (
                None
            ),
            "route": "finalize" if maxed else "ingest",
        }

    async def _finalize(self, state: ResolutionGraphState) -> dict[str, Any]:
        if state.get("status") in {
            ResolutionStatus.WAITING_FOR_USER.value,
            ResolutionStatus.RESOLVED.value,
            ResolutionStatus.COLLABORATION_REQUIRED.value,
            ResolutionStatus.PAUSED.value,
            ResolutionStatus.EXHAUSTED.value,
            ResolutionStatus.ERROR.value,
            ResolutionStatus.CANCELLED.value,
        }:
            current_status = str(state.get("status"))
            return {
                "progress_events": self._append_progress_event(
                    state,
                    stage=current_status,
                    message=state.get("response") or "当前阶段已结束，Case 状态已保留。",
                    status=current_status,
                )
            }
        plan = ResolutionPlan.model_validate(state.get("plan", {}))
        mapping = {
            ResolutionDecision.ASK_USER: ResolutionStatus.WAITING_FOR_USER,
            ResolutionDecision.RESOLVE: ResolutionStatus.RESOLVED,
            ResolutionDecision.COLLABORATE: ResolutionStatus.COLLABORATION_REQUIRED,
        }
        status = mapping.get(plan.decision, ResolutionStatus.ERROR)
        response = plan.response or "当前解决阶段已经结束。"
        if status is ResolutionStatus.WAITING_FOR_USER:
            policy = state["resolution_context"].get("provisional_resolution_policy", {})
            question_limit = max(0, int(policy.get("max_questions_per_turn", 1)))
            action_limit = max(0, int(policy.get("max_user_actions_per_turn", 1)))
            questions = plan.user_questions[:question_limit]
            actions = plan.requested_user_actions[:action_limit]
            structured_requests = [*questions, *actions]
            if structured_requests:
                # ``plan.response`` is the short result-first bridge: it may
                # separate an unsupported hypothesis from current evidence or
                # acknowledge what has already been tried.  User requests are
                # structured separately so their count remains enforceable,
                # but they must not erase that useful conclusion.
                response_parts = []
                concise_result = _declarative_bridge(plan.response or "")
                if concise_result and concise_result not in structured_requests:
                    response_parts.append(concise_result)
                response_parts.extend(structured_requests)
                response = "\n".join(response_parts)
        if status is ResolutionStatus.RESOLVED:
            user_state = state["resolution_context"].get("user_state", {})
            resolution_policy = state["resolution_context"].get(
                "provisional_resolution_policy", {}
            )
            urgent = bool(
                user_state.get("deadline")
                or resolution_policy.get("deadline")
                or user_state.get("patience") == "low"
            )
            response = (
                "已优先完成处理，并通过独立检查确认结果。"
                if urgent
                else "已经处理完成，并通过独立检查确认结果。"
            )
            salient_failures = state["resolution_context"].get("salient_failures", [])
            if user_state.get("frustration") == "high" or salient_failures:
                response += "你不用再重复前面的操作。"
            explanation_mode = resolution_policy.get("explanation_mode", "on_demand")
            if explanation_mode != "minimal" and plan.provisional_explanations:
                response += (
                    " 当前证据更支持："
                    f"{plan.provisional_explanations[0].statement}（暂定解释）。"
                )
        return {
            "status": status.value,
            "response": response,
            "collaboration_reason": plan.collaboration_reason,
            "progress_events": self._append_progress_event(
                state,
                stage=status.value,
                message=response,
                status=status.value,
            ),
        }
