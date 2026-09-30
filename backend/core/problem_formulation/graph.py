"""LangGraph runtime for adaptive shared problem formulation."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .interpreter import TurnInterpreter
from .models import (
    CaseStatus,
    ClaimType,
    CommonGroundStatus,
    EvidenceNeedStatus,
    ExperimentVariant,
    FormulationHistoryEntry,
    InteractionAction,
    InteractionContract,
    PolicyDecision,
    ProgressStatus,
    SharedProblemState,
    TurnInterpretation,
)
from .needs import (
    InteractionContractBuilder,
    InteractionNeedDetector,
    ResolutionPolicyBuilder,
)
from .policy import FirstAllowedPolicy, InteractionPolicy
from .sufficiency import EvidenceAssessor, ProblemStateReducer


class FormulationGraphState(TypedDict, total=False):
    problem_state: dict[str, Any]
    turn_message: str
    interpretation: dict[str, Any]
    policy_decision: dict[str, Any]
    interaction_contract: dict[str, Any]
    response: str
    case_ready: bool
    resolution_context: dict[str, Any] | None
    trace: Annotated[list[dict[str, Any]], operator.add]
    experiment_variant: str
    interpreter_model_calls: int


class ProblemFormulationGraph:
    """Nodes own one responsibility; LangGraph owns transitions and persistence."""

    def __init__(
        self,
        interpreter: TurnInterpreter,
        policy: InteractionPolicy,
        checkpointer: Any,
    ):
        self._interpreter = interpreter
        self._policy = policy
        self._policy_fallback = FirstAllowedPolicy()
        self._reducer = ProblemStateReducer()
        self._assessor = EvidenceAssessor()
        self._need_detector = InteractionNeedDetector()
        self._contract_builder = InteractionContractBuilder()
        self.compiled = self._build().compile(checkpointer=checkpointer)

    def _build(self) -> StateGraph:
        graph = StateGraph(FormulationGraphState)
        graph.add_node("interpret_turn", self._interpret_turn)
        graph.add_node("update_problem_state", self._update_problem_state)
        graph.add_node("update_user_state", self._update_user_state)
        graph.add_node("assess_evidence", self._assess_evidence)
        graph.add_node("detect_interaction_needs", self._detect_interaction_needs)
        graph.add_node("build_interaction_contract", self._build_interaction_contract)
        graph.add_node("select_human_behavior", self._select_human_behavior)
        graph.add_node("render_interaction", self._render_interaction)
        graph.add_node("emit_case_ready", self._emit_case_ready)
        graph.add_node("record_outcome", self._record_outcome)

        graph.add_edge(START, "interpret_turn")
        graph.add_edge("interpret_turn", "update_problem_state")
        graph.add_edge("update_problem_state", "update_user_state")
        graph.add_edge("update_user_state", "assess_evidence")
        graph.add_edge("assess_evidence", "detect_interaction_needs")
        graph.add_edge("detect_interaction_needs", "build_interaction_contract")
        graph.add_edge("build_interaction_contract", "select_human_behavior")
        graph.add_conditional_edges(
            "select_human_behavior",
            self._route_after_policy,
            {
                "interact": "render_interaction",
                "ready": "emit_case_ready",
            },
        )
        graph.add_edge("render_interaction", "record_outcome")
        graph.add_edge("emit_case_ready", "record_outcome")
        graph.add_edge("record_outcome", END)
        return graph

    async def _interpret_turn(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        try:
            interpretation = await self._interpreter.interpret(state["turn_message"], problem)
            if problem.status is CaseStatus.FORMULATION_ERROR:
                problem.status = CaseStatus.ALIGNING_GOAL
            problem.conversation_act = interpretation.conversation_act
            if interpretation.conversation_act.value != "case_formulation":
                problem.status = CaseStatus.META_HANDLED
                problem.turn_count += 1
            problem.fallback_reason = None
            return {
                "problem_state": problem.model_dump(mode="json"),
                "interpretation": interpretation.model_dump(mode="json"),
                "resolution_context": None,
                "response": "",
                "interpreter_model_calls": interpretation.extraction_attempts,
                "trace": [
                    {
                        "node": "interpret_turn",
                        "status": "ok",
                        "semantic_coverage_complete": interpretation.semantic_coverage_complete,
                        "unmapped_spans": interpretation.unmapped_spans,
                    }
                ],
            }
        except Exception as exc:  # noqa: BLE001 - graph boundary must record provider failures
            problem.fallback_reason = f"{type(exc).__name__}: {exc}"
            problem.status = CaseStatus.FORMULATION_ERROR
            problem.turn_count += 1
            return {
                "problem_state": problem.model_dump(mode="json"),
                "interpretation": TurnInterpretation().model_dump(mode="json"),
                "resolution_context": None,
                "response": "",
                "interpreter_model_calls": int(getattr(exc, "model_calls", 1)),
                "trace": [
                    {
                        "node": "interpret_turn",
                        "status": "degraded",
                        "error": problem.fallback_reason,
                    }
                ],
            }

    async def _update_problem_state(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        if problem.status in {CaseStatus.FORMULATION_ERROR, CaseStatus.META_HANDLED}:
            return {"trace": [{"node": "update_problem_state", "status": "skipped"}]}
        interpretation = TurnInterpretation.model_validate(state["interpretation"])
        problem = self._reducer.merge_problem(problem, interpretation, state["turn_message"])
        if state.get("experiment_variant") == ExperimentVariant.NO_EPISTEMIC_SEPARATION.value:
            for claim in problem.claims:
                if claim.turn_id == problem.turn_count and claim.type is ClaimType.HYPOTHESIS:
                    claim.type = ClaimType.OBSERVATION
        return {
            "problem_state": problem.model_dump(mode="json"),
            "trace": [{"node": "update_problem_state", "status": "ok"}],
        }

    async def _update_user_state(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        if problem.status in {CaseStatus.FORMULATION_ERROR, CaseStatus.META_HANDLED}:
            return {"trace": [{"node": "update_user_state", "status": "skipped"}]}
        if state.get("experiment_variant") == ExperimentVariant.NO_USER_STATE.value:
            return {"trace": [{"node": "update_user_state", "status": "ablated_no_user_state"}]}
        interpretation = TurnInterpretation.model_validate(state["interpretation"])
        problem = self._reducer.merge_user_state(problem, interpretation, state["turn_message"])
        return {
            "problem_state": problem.model_dump(mode="json"),
            "trace": [{"node": "update_user_state", "status": "ok"}],
        }

    async def _assess_evidence(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        if problem.status not in {CaseStatus.FORMULATION_ERROR, CaseStatus.META_HANDLED}:
            problem = self._assessor.assess(problem)
        return {
            "problem_state": problem.model_dump(mode="json"),
            "case_ready": problem.status is CaseStatus.CASE_READY,
            "trace": [
                {
                    "node": "assess_evidence",
                    "status": problem.status.value,
                    "evidence_sufficiency": problem.evidence_sufficiency.value,
                }
            ],
        }

    async def _detect_interaction_needs(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        problem.interaction_needs = self._need_detector.detect(problem)
        return {
            "problem_state": problem.model_dump(mode="json"),
            "trace": [
                {
                    "node": "detect_interaction_needs",
                    "needs": [need.type.value for need in problem.interaction_needs],
                }
            ],
        }

    async def _select_human_behavior(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        contract = InteractionContract.model_validate(state["interaction_contract"])
        policy_error = None
        try:
            decision = await self._policy.select_action(problem, contract)
        except Exception as exc:  # noqa: BLE001 - policy fallback is an explicit graph path
            policy_error = f"{type(exc).__name__}: {exc}"
            decision = await self._policy_fallback.select_action(problem, contract)
        return {
            "policy_decision": decision.model_dump(mode="json"),
            "trace": [
                {
                    "node": "select_human_behavior",
                    "primary": decision.action.value,
                    "supporting": [item.value for item in decision.supporting_actions],
                    "modifiers": [item.value for item in decision.modifiers],
                    "planning_mode": decision.planning_mode,
                    "control_priors": decision.control_priors.model_dump(mode="json"),
                    "candidate_actions": [item.value for item in decision.candidate_actions],
                    "status": "fallback" if policy_error else "ok",
                    "error": policy_error,
                    "addressed_need": (
                        decision.addressed_need.value if decision.addressed_need else None
                    ),
                    "contract_compliance": decision.compliance.model_dump(mode="json"),
                }
            ],
        }

    async def _build_interaction_contract(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        contract = self._contract_builder.build(problem)
        return {
            "interaction_contract": contract.model_dump(mode="json"),
            "trace": [
                {
                    "node": "build_interaction_contract",
                    "contract": contract.model_dump(mode="json"),
                }
            ],
        }

    @staticmethod
    def _route_after_policy(state: FormulationGraphState) -> str:
        decision = PolicyDecision.model_validate(state["policy_decision"])
        if decision.action is InteractionAction.EMIT_CASE_READY:
            return "ready"
        return "interact"

    async def _render_interaction(self, state: FormulationGraphState) -> dict[str, Any]:
        decision = PolicyDecision.model_validate(state["policy_decision"])
        response = decision.response_text or decision.question or "请补充当前关键信息。"
        return {
            "response": response,
            "case_ready": False,
            "trace": [{"node": "render_interaction", "status": "waiting_for_user"}],
        }

    async def _emit_case_ready(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        decision = PolicyDecision.model_validate(state["policy_decision"])
        resolution_policy = ResolutionPolicyBuilder.build(problem, decision.contract)
        return {
            "response": decision.response_text or "我先按当前目标继续处理。",
            "case_ready": True,
            "resolution_context": problem.to_resolution_context(resolution_policy),
            "trace": [
                {
                    "node": "emit_case_ready",
                    "status": "case_ready",
                    "readiness_reason": problem.readiness.reason_codes,
                }
            ],
        }

    async def _record_outcome(self, state: FormulationGraphState) -> dict[str, Any]:
        problem = SharedProblemState.model_validate(state["problem_state"])
        decision = PolicyDecision.model_validate(state["policy_decision"])
        evidence_added = [
            claim.content for claim in problem.claims if claim.turn_id == problem.turn_count
        ]
        evidence_resolved = [
            item.key
            for item in problem.missing_evidence
            if item.status is EvidenceNeedStatus.RESOLVED
            and item.resolved_turn_id == problem.turn_count
        ]
        claims_revised = [
            claim.content for claim in problem.claims if claim.revised_turn_id == problem.turn_count
        ]
        if decision.target_evidence:
            target_key = decision.target_evidence.strip().casefold()
            target = next(
                (
                    item
                    for item in problem.missing_evidence
                    if item.key.strip().casefold() == target_key
                    and item.status is EvidenceNeedStatus.OPEN
                ),
                None,
            )
            if target is not None:
                target.attempt_count += 1
                target.last_asked_turn_id = problem.turn_count
        progress = (
            ProgressStatus.PROGRESSED
            if evidence_added or evidence_resolved or claims_revised
            else (
                ProgressStatus.NOT_APPLICABLE
                if problem.turn_count == 1
                else ProgressStatus.NO_PROGRESS
            )
        )
        if (
            decision.addressed_need
            and decision.addressed_need.value == "goal_alignment"
            and decision.action in {InteractionAction.REFLECT, InteractionAction.STRUCTURE}
            and (problem.goal.explicit_goal or problem.goal.inferred_goal)
        ):
            problem.goal.common_ground = CommonGroundStatus.REFLECTED
            problem.goal.reflected_turn_id = problem.turn_count
        problem.formulation_history.append(
            FormulationHistoryEntry(
                turn_id=problem.turn_count,
                status=problem.status,
                action=decision.action,
                supporting_actions=decision.supporting_actions,
                modifiers=decision.modifiers,
                missing_evidence=[item.key for item in problem.missing_evidence],
                response=state.get("response"),
                question=decision.question,
                target_evidence=decision.target_evidence,
                addressed_need=decision.addressed_need,
                evidence_added=evidence_added,
                evidence_resolved=evidence_resolved,
                claims_revised=claims_revised,
                progress=progress,
                compliance=decision.compliance,
            )
        )
        problem.compact_runtime_history()
        return {
            "problem_state": problem.model_dump(mode="json"),
            "trace": [{"node": "record_outcome", "status": "ok"}],
        }
