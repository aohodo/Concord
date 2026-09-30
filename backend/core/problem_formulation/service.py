"""Application service around the compiled LangGraph."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .graph import ProblemFormulationGraph
from .interpreter import LangChainTurnInterpreter, TurnInterpreter
from .models import (
    EvidenceNeed,
    ExperimentVariant,
    FormulationResult,
    PolicyDecision,
    SharedProblemState,
)
from .policy import InteractionPolicy, LangChainInteractionPolicy
from .trace import JsonlTrajectorySink


class ProblemFormulationService:
    def __init__(
        self,
        *,
        checkpointer: Any,
        client: Any = None,
        model: str = "",
        interpreter: TurnInterpreter | None = None,
        policy: InteractionPolicy | None = None,
        trace_path: str | None = None,
    ):
        if interpreter is None:
            if client is None or not model:
                raise ValueError("client and model are required when interpreter is not provided")
            interpreter = LangChainTurnInterpreter(client, model)
        resolved_policy = policy
        if resolved_policy is None:
            if client is None or not model:
                raise ValueError("client and model are required when policy is not provided")
            resolved_policy = LangChainInteractionPolicy(client, model)
        self._runtime = ProblemFormulationGraph(
            interpreter=interpreter,
            policy=resolved_policy,
            checkpointer=checkpointer,
        )
        self._sink = JsonlTrajectorySink(trace_path)
        self._locks: dict[str, asyncio.Lock] = {}

    async def process(
        self,
        *,
        message: str,
        user_id: str,
        conv_id: str,
        domain_prior: str,
        solver_payload: dict[str, Any] | None = None,
        experiment_variant: ExperimentVariant = ExperimentVariant.FULL,
    ) -> FormulationResult:
        payload = dict(solver_payload or {})
        trace_uuid = uuid4()
        trace_id = str(trace_uuid)
        payload.setdefault("trace_id", trace_id)
        thread_id = (
            f"{user_id}:{conv_id}"
            if experiment_variant is ExperimentVariant.FULL
            else f"{experiment_variant.value}:{user_id}:{conv_id}"
        )
        config = {
            "configurable": {"thread_id": thread_id},
            "run_id": trace_uuid,
            "run_name": "concord_m1_problem_formulation",
            "tags": ["concord", "m1", experiment_variant.value],
            "metadata": {
                "thread_id": thread_id,
                "phase": "M1",
                "experiment_variant": experiment_variant.value,
                "synthetic": bool(payload.get("synthetic", False)),
            },
        }
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            snapshot = await self._runtime.compiled.aget_state(config)
            before = snapshot.values.get("problem_state") if snapshot.values else None
            if before:
                problem = SharedProblemState.model_validate(before)
                problem.domain_prior = domain_prior or problem.domain_prior
                requested_case_id = str(payload.get("requested_case_id") or "").strip()
                if requested_case_id and requested_case_id != problem.case_id:
                    raise ValueError("requested_case_id does not match the existing conversation")
            else:
                problem = SharedProblemState(
                    case_id=str(payload.get("requested_case_id") or "").strip()
                    or str(uuid4()),
                    user_id=user_id,
                    conv_id=conv_id,
                    domain_prior=domain_prior or "other",
                )
            config["metadata"]["case_id"] = problem.case_id
            self._merge_evidence_catalog(problem, payload.get("evidence_catalog"))

            output = await self._runtime.compiled.ainvoke(
                {
                    "problem_state": problem.model_dump(mode="json"),
                    "turn_message": message,
                    "trace": [],
                    "experiment_variant": experiment_variant.value,
                },
                config=config,
            )
            current = SharedProblemState.model_validate(output["problem_state"])
            policy = PolicyDecision.model_validate(output["policy_decision"])
            model_calls = int(output.get("interpreter_model_calls", 1)) + int(
                policy.planning_mode == "model_deliberation"
            )
            result = FormulationResult(
                trace_id=trace_id,
                response=output.get("response", ""),
                case_ready=bool(output.get("case_ready")),
                state=current,
                policy=policy,
                trace=output.get("trace", []),
                resolution_context=(
                    output.get("resolution_context") if output.get("case_ready") else None
                ),
                m1_model_calls=model_calls,
            )
            await self._sink.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "trace_id": trace_id,
                    "thread_id": thread_id,
                    "case_id": current.case_id,
                    "turn_id": current.turn_count,
                    "raw_user_input": message,
                    "state_before": before,
                    "interaction_needs": [
                        need.model_dump(mode="json") for need in current.interaction_needs
                    ],
                    "candidate_actions": [item.value for item in policy.candidate_actions],
                    "chosen_action": policy.action.value,
                    "supporting_actions": [item.value for item in policy.supporting_actions],
                    "interaction_contract": policy.contract.model_dump(mode="json"),
                    "state_after": current.model_dump(mode="json"),
                    "response": result.response,
                    "case_ready": result.case_ready,
                    "resolution_context": result.resolution_context,
                    "m1_model_calls": result.m1_model_calls,
                    "control_priors": policy.control_priors.model_dump(mode="json"),
                }
            )
            return result

    @staticmethod
    def _merge_evidence_catalog(
        problem: SharedProblemState,
        raw_catalog: Any,
    ) -> None:
        """Attach tool/environment evidence affordances without leaking values."""
        if not isinstance(raw_catalog, list):
            return
        existing = {item.key.casefold() for item in problem.missing_evidence}
        for raw in raw_catalog:
            if isinstance(raw, str):
                raw = {
                    "key": raw,
                    "description": raw,
                    "question": f"请提供 {raw}。",
                }
            if not isinstance(raw, dict) or not str(raw.get("key", "")).strip():
                continue
            try:
                need = EvidenceNeed.model_validate(raw)
            except ValueError:
                continue
            if need.key not in problem.evidence_catalog:
                problem.evidence_catalog.append(need.key)
            if need.key.casefold() not in existing:
                problem.missing_evidence.append(need)
                existing.add(need.key.casefold())
