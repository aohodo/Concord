"""LangChain behavior planning with deterministic capability constraints."""

from __future__ import annotations

import json
from typing import Any, Protocol

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from core.control_priors import ControlPriorDecision
from core.llm_utils import extract_text_content

from .guardrails import PolicyHarness
from .models import (
    ActionOutcome,
    ActionRelevance,
    BehaviorProposal,
    CaseStatus,
    ConversationAct,
    EvidenceNeedStatus,
    InteractionAction,
    InteractionContract,
    InteractionModifier,
    InteractionNeedType,
    IssueStatus,
    PolicyDecision,
    SharedProblemState,
)

BEHAVIOR_CAPABILITIES = {
    InteractionNeedType.GOAL_ALIGNMENT: (
        InteractionAction.REFLECT,
        InteractionAction.STRUCTURE,
    ),
    InteractionNeedType.SITUATION_RECONSTRUCTION: (
        InteractionAction.STRUCTURE,
        InteractionAction.SCAFFOLD,
        InteractionAction.VERIFY,
    ),
    InteractionNeedType.EPISTEMIC_CALIBRATION: (
        InteractionAction.CALIBRATE,
        InteractionAction.CHALLENGE,
        InteractionAction.VERIFY,
    ),
    InteractionNeedType.CONTRADICTION_REPAIR: (
        InteractionAction.REPAIR,
        InteractionAction.VERIFY,
    ),
    InteractionNeedType.EVIDENCE_ACQUISITION: (
        InteractionAction.VERIFY,
        InteractionAction.SCAFFOLD,
    ),
    InteractionNeedType.USER_SUPPORT: (
        InteractionAction.REFLECT,
        InteractionAction.RESTORE_AGENCY,
        InteractionAction.SCAFFOLD,
    ),
    InteractionNeedType.URGENCY_PRIORITIZATION: (
        InteractionAction.PRIORITIZE,
        InteractionAction.RESTORE_AGENCY,
    ),
    InteractionNeedType.READY_FOR_RESOLUTION: (InteractionAction.EMIT_CASE_READY,),
}


class InteractionPolicy(Protocol):
    async def select_action(
        self,
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> PolicyDecision: ...


class LangChainInteractionPolicy:
    """Use compiled behavior normally and model deliberation only for real conflicts."""

    SYSTEM_PROMPT = """你是 Concord 的交互行为规划器，不负责诊断问题。

根据共享问题状态、当前交互需求和 InteractionContract，从 allowed_actions 中选择一个主行为和最多两个辅助行为。

行为语义：
- reflect：准确复述用户表达，建立共同理解，不做模板共情。
- structure：帮助用户整理碎片化目标、时间线或现象。
- scaffold：降低术语和操作门槛，一次提供一个可完成的小步骤。
- calibrate：明确区分观察、推断与不确定性。
- challenge：温和挑战缺少证据的高置信判断。
- verify：获取当前信息价值最高且负担最低的证据。
- restore_agency：避免重复失败动作，让用户重新获得可控感。
- prioritize：在低耐心或 deadline 下优先恢复和最短路径。
- repair：修复双方误解或状态中的矛盾。

约束：
1. 不得选择 allowed_actions 之外的行为。
2. 最多提出 question_budget 个问题，默认只问一个。
3. 不要求用户重复 forbidden_actions。
4. 不把 hypothesis 写成事实。
5. response_text 必须直接体现行为选择，而不是泛泛表达同理心。
6. response_text 遵循长度、术语和操作负担约束。
7. 只推进当前 Case，不擅自扩展用户目标。
8. target_evidence 只能逐字选择共享状态中 status=open 的 missing_evidence.key；不得创造同义 key。
9. 只有当证据会改变方案、安全边界或成功判定时才追问。deadline、低耐心或低 effort 下禁止追问 blocking=false 的证据。
10. 面向用户按“当前进展/只需做什么/预期可见结果”组织，不倾倒内部推理；安抚必须具体且诚实，不承诺无法保证的结果。
11. 已询问但没有取得进展的 target 不得原样重复；选择另一条高价值低负担证据，或降低回答门槛。
12. response_text 如果要求用户执行具体动作，必须在 proposed_actions 中列出；若动作来自状态中的历史动作，逐字复制其 action 值。仅复述用户已做过的动作不算 proposed action。

{format_instructions}
"""

    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model
        self._parser = PydanticOutputParser(pydantic_object=BehaviorProposal)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.SYSTEM_PROMPT),
                (
                    "human",
                    (
                        "共享状态：\n{state}\n\n当前交互需求：\n{needs}\n\n"
                        "allowed_actions：{allowed_actions}\n\n"
                        "InteractionContract：\n{contract}"
                    ),
                ),
            ]
        ).partial(format_instructions=self._parser.get_format_instructions())
        self._chain = self._prompt | RunnableLambda(self._call_model) | self._parser
        self._harness = PolicyHarness(BEHAVIOR_CAPABILITIES)
        self._compiled = CompiledInteractionPolicy(self._harness)

    async def _call_model(self, prompt_value: Any) -> str:
        system_parts, messages = [], []
        for message in prompt_value.to_messages():
            role = getattr(message, "type", "human")
            content = str(getattr(message, "content", ""))
            if role == "system":
                system_parts.append(content)
            else:
                messages.append(
                    {"role": "assistant" if role == "ai" else "user", "content": content}
                )
        request = {
            "model": self._model,
            "max_tokens": 900,
            "temperature": 0.0,
            "system": "\n\n".join(system_parts),
            "messages": messages,
        }
        if getattr(self._client, "supports_json_object", False):
            request["response_format"] = {"type": "json_object"}
        response = await self._client.messages.create(**request)
        return extract_text_content(response.content)

    async def select_action(
        self,
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> PolicyDecision:
        terminal = self._terminal_decision(state, contract)
        if terminal is not None:
            return terminal

        if not any(
            need.type is InteractionNeedType.CONTRADICTION_REPAIR
            for need in state.interaction_needs
        ):
            return await self._compiled.select_action(state, contract)

        allowed = list(
            dict.fromkeys(
                action
                for need in state.interaction_needs
                for action in BEHAVIOR_CAPABILITIES.get(need.type, ())
            )
        ) or [InteractionAction.VERIFY]
        proposal = await self._chain.ainvoke(
            {
                "state": json.dumps(
                    state.model_dump(
                        mode="json", exclude={"formulation_history", "interaction_needs"}
                    ),
                    ensure_ascii=False,
                ),
                "needs": json.dumps(
                    [need.model_dump(mode="json") for need in state.interaction_needs],
                    ensure_ascii=False,
                ),
                "allowed_actions": [action.value for action in allowed],
                "contract": json.dumps(contract.model_dump(mode="json"), ensure_ascii=False),
            }
        )
        primary = proposal.primary_action if proposal.primary_action in allowed else allowed[0]
        supporting = [
            action
            for action in dict.fromkeys(proposal.supporting_actions)
            if action in allowed and action is not primary
        ][:2]
        decision = PolicyDecision(
            action=primary,
            supporting_actions=supporting,
            reason=proposal.reason,
            target_evidence=proposal.target_evidence,
            question=proposal.question,
            response_text=proposal.response_text,
            proposed_actions=proposal.proposed_actions,
            candidate_actions=allowed,
            contract=contract,
            planning_mode="model_deliberation",
            control_priors=ControlPriorDecision(
                attention_focus=[
                    "material_contradiction",
                    *[f"contradiction:{item}" for item in state.contradictions],
                ],
                deliberation="complex_repair",
                stopping="continue_until_contradiction_resolved",
                search_control="seek_discriminating_evidence",
                experience_refs=[
                    item.key
                    for item in state.missing_evidence
                    if item.status is EvidenceNeedStatus.OPEN
                ],
            ),
        )
        return self._harness.enforce(state, decision)

    @staticmethod
    def _terminal_decision(
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> PolicyDecision | None:
        salient_failures = [
            item.action
            for item in state.action_result_ledger
            if item.relevance is ActionRelevance.ACTIVE
            and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
        ]
        decisions = {
            CaseStatus.FORMULATION_ERROR: PolicyDecision(
                action=InteractionAction.REPAIR,
                reason=state.fallback_reason or "problem formulation failed safely",
                response_text="我没有可靠理解你刚才这句话，请换一种简短说法再告诉我一次。",
                candidate_actions=[InteractionAction.REPAIR],
                contract=contract,
                planning_mode="safe_degraded",
                control_priors=ControlPriorDecision(
                    attention_focus=["interpretation_failure"],
                    deliberation="safe_degraded",
                    stopping="request_reformulation",
                    search_control="reduce_input_complexity",
                ),
            ),
            CaseStatus.CASE_READY: PolicyDecision(
                action=InteractionAction.EMIT_CASE_READY,
                reason="minimum actionable context is available for M2",
                # This is only a compatibility placeholder while M2 is not yet
                # connected. The normal product path replaces it with M2's
                # first useful action; internal formulation is not presented as
                # a user-visible achievement.
                response_text="我先按当前目标继续处理。",
                candidate_actions=[InteractionAction.EMIT_CASE_READY],
                contract=contract,
                planning_mode="compiled",
                control_priors=ControlPriorDecision(
                    attention_focus=[
                        "minimum_actionable_context",
                        *[f"failed_action:{item}" for item in salient_failures],
                    ],
                    deliberation="routine",
                    stopping="emit_case_ready",
                    search_control="continue_formulation_during_resolution",
                    experience_refs=salient_failures,
                ),
            ),
            CaseStatus.META_HANDLED: PolicyDecision(
                action=InteractionAction.META_RESPONSE,
                reason=f"semantic conversation act: {state.conversation_act.value}",
                response_text={
                    ConversationAct.HUMAN_HANDOFF: "已记录你的人工协助请求。",
                    ConversationAct.STOP: "好的，本次问题整理已停止。",
                    ConversationAct.SOCIAL: "你好，请直接告诉我你现在希望解决的问题。",
                }.get(state.conversation_act, "请告诉我你现在希望解决的问题。"),
                candidate_actions=[InteractionAction.META_RESPONSE],
                contract=contract,
                planning_mode="semantic_route",
                control_priors=ControlPriorDecision(
                    attention_focus=[f"conversation_act:{state.conversation_act.value}"],
                    deliberation="routine",
                    stopping="handle_conversation_act",
                    search_control="do_not_open_case",
                ),
            ),
        }
        return decisions.get(state.status)


class CompiledInteractionPolicy:
    """Declarative default policy: one primary move plus orthogonal modifiers."""

    def __init__(self, harness: PolicyHarness | None = None):
        self._harness = harness or PolicyHarness(BEHAVIOR_CAPABILITIES)

    @staticmethod
    def _modifiers(state: SharedProblemState, evidence: Any) -> list[InteractionModifier]:
        modifiers: list[InteractionModifier] = []
        if state.user_state.domain_knowledge.value == "low":
            modifiers.append(InteractionModifier.PLAIN_LANGUAGE)
        if state.user_state.deadline or state.user_state.patience.value == "low":
            modifiers.append(InteractionModifier.CONCISE)
        failures = [
            item
            for item in state.action_result_ledger
            if item.relevance is ActionRelevance.ACTIVE
            and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
        ]
        if failures:
            acknowledged = any(
                InteractionModifier.ACKNOWLEDGE_FAILURE in entry.modifiers
                for entry in state.formulation_history
            )
            if not acknowledged:
                modifiers.append(InteractionModifier.ACKNOWLEDGE_FAILURE)
            modifiers.append(InteractionModifier.NO_BLIND_RETRY)
        if evidence is not None and evidence.acquisition_hint:
            modifiers.append(InteractionModifier.PROVIDE_ACQUISITION_HINT)
        if state.user_state.frustration.value == "high":
            modifiers.extend(
                [
                    InteractionModifier.RESTORE_AGENCY,
                    InteractionModifier.DISCLOSE_PROGRESS,
                ]
            )
        return list(dict.fromkeys(modifiers))

    @staticmethod
    def _failure_prefix(state: SharedProblemState) -> str:
        failure = next(
            (
                item
                for item in reversed(state.action_result_ledger)
                if item.relevance is ActionRelevance.ACTIVE
                and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
            ),
            None,
        )
        if failure is None:
            return ""
        if any(
            InteractionModifier.ACKNOWLEDGE_FAILURE in entry.modifiers
            for entry in state.formulation_history
        ):
            return ""
        count = f"{failure.repetitions} 次" if failure.repetitions else ""
        return f"你已经{count}{failure.action}，这次不会让你在相同条件下重复。"

    @staticmethod
    def _case_compass_prefix(state: SharedProblemState) -> str:
        current = state.goal.current_outcome or state.goal.explicit_goal
        active = next(
            (
                item
                for item in state.reported_issues
                if item.issue_id == state.active_issue_id
            ),
            None,
        )
        goal_changed = bool(
            state.goal.revision_history
            and state.goal.revision_history[-1].turn_id == state.turn_count
        )
        focus_changed = state.last_focus_change_turn_id == state.turn_count
        deferred_added = any(
            item.first_seen_turn_id == state.turn_count
            and item.status is IssueStatus.DEFERRED
            for item in state.reported_issues
        )
        if not (goal_changed or focus_changed or deferred_added):
            return ""
        if current and active:
            return f"当前仍以“{current}”为主线，先处理“{active.summary}”。"
        if current:
            return f"当前主线是“{current}”。"
        return ""

    @staticmethod
    def _supporting_actions(
        state: SharedProblemState,
        primary: InteractionAction,
    ) -> list[InteractionAction]:
        preferred = {
            InteractionNeedType.URGENCY_PRIORITIZATION: InteractionAction.PRIORITIZE,
            InteractionNeedType.USER_SUPPORT: InteractionAction.RESTORE_AGENCY,
        }
        actions = [
            preferred[need.type]
            for need in state.interaction_needs
            if need.type in preferred and preferred[need.type] is not primary
        ]
        return list(dict.fromkeys(actions))[:2]

    @staticmethod
    def _control_priors(
        state: SharedProblemState,
        need_type: InteractionNeedType,
        evidence: Any,
    ) -> ControlPriorDecision:
        active_failures = [
            item
            for item in state.action_result_ledger
            if item.relevance is ActionRelevance.ACTIVE
            and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
        ]
        unavailable = [
            item
            for item in state.missing_evidence
            if item.status in {EvidenceNeedStatus.UNAVAILABLE, EvidenceNeedStatus.DEFERRED}
        ]
        tried_open = [
            item
            for item in state.missing_evidence
            if item.status is EvidenceNeedStatus.OPEN and item.attempt_count > 0
        ]
        attention = [need_type.value]
        if evidence is not None:
            attention.append(f"evidence:{evidence.key}")
        attention.extend(f"failed_action:{item.action}" for item in active_failures)
        search_control = "maintain_current_information_path"
        if unavailable:
            search_control = "defer_unavailable_evidence"
        elif tried_open and evidence is not None and evidence.attempt_count == 0:
            search_control = "switch_to_untried_evidence"
        return ControlPriorDecision(
            attention_focus=list(dict.fromkeys(attention)),
            deliberation="routine_compiled_control",
            stopping="continue_formulation",
            search_control=search_control,
            experience_refs=list(
                dict.fromkeys(
                    [
                        *([f"evidence_contract:{evidence.key}"] if evidence is not None else []),
                        *[f"action_result:{item.action}" for item in active_failures],
                    ]
                )
            ),
        )

    async def select_action(
        self,
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> PolicyDecision:
        terminal = LangChainInteractionPolicy._terminal_decision(state, contract)
        if terminal is not None:
            return terminal

        priority_need = state.interaction_needs[0] if state.interaction_needs else None
        need_type = (
            priority_need.type if priority_need else InteractionNeedType.EVIDENCE_ACQUISITION
        )
        allowed = list(BEHAVIOR_CAPABILITIES.get(need_type, (InteractionAction.VERIFY,)))
        evidence_need_types = {
            InteractionNeedType.GOAL_ALIGNMENT,
            InteractionNeedType.SITUATION_RECONSTRUCTION,
            InteractionNeedType.EPISTEMIC_CALIBRATION,
            InteractionNeedType.EVIDENCE_ACQUISITION,
        }
        evidence = (
            self._harness.select_evidence(state) if need_type in evidence_need_types else None
        )
        modifiers = self._modifiers(state, evidence)
        action = allowed[0]
        supporting_actions = self._supporting_actions(state, action)
        question = evidence.question if evidence is not None else None
        parts: list[str] = []
        compass_prefix = self._case_compass_prefix(state)
        if compass_prefix:
            parts.append(compass_prefix)
        failure_prefix = self._failure_prefix(state)
        if failure_prefix:
            parts.append(failure_prefix)

        if need_type is InteractionNeedType.GOAL_ALIGNMENT:
            goal = state.goal.inferred_goal
            if goal and not state.goal.unresolved_ambiguity:
                parts.append(f"我先按“{goal}”推进；如果不对请直接纠正。")
                modifiers.append(InteractionModifier.INVITE_CORRECTION)
                if question is None:
                    question = "请只描述你现在实际看到的现象。"
            else:
                question = "为了不擅自扩大范围，你现在最希望先得到什么结果？"
        elif need_type is InteractionNeedType.EPISTEMIC_CALIBRATION:
            hypothesis = next(
                (
                    item.content
                    for item in reversed(state.claims)
                    if item.type.value == "hypothesis"
                ),
                "当前原因判断",
            )
            parts.append(f"我先把“{hypothesis}”保留为待验证判断。")
        elif need_type is InteractionNeedType.USER_SUPPORT and question is None:
            question = "请告诉我你现在最方便确认的一项信息。"
        elif need_type is InteractionNeedType.URGENCY_PRIORITIZATION and question is None:
            question = "时间有限时，你希望先恢复哪项最关键的使用？"

        if (
            InteractionAction.PRIORITIZE in supporting_actions
            and need_type is not InteractionNeedType.GOAL_ALIGNMENT
        ):
            parts.append("我会优先走能最快验证并恢复使用的路径。")
        if InteractionAction.RESTORE_AGENCY in supporting_actions:
            parts.append("你不需要一次把所有细节说完整。")

        if question:
            parts.append(question)
            if evidence is not None and evidence.acquisition_hint:
                parts.append(evidence.acquisition_hint)
        if not parts:
            parts.append("请补充当前最关键的一条直接观察。")
        response = "".join(parts)
        decision = PolicyDecision(
            action=action,
            supporting_actions=supporting_actions,
            modifiers=list(dict.fromkeys(modifiers)),
            reason="compiled primary move with case-conditioned modifiers",
            target_evidence=evidence.key if evidence else None,
            question=question,
            response_text=response,
            candidate_actions=allowed,
            contract=contract,
            addressed_need=need_type,
            planning_mode="compiled",
            control_priors=self._control_priors(state, need_type, evidence),
        )
        return self._harness.enforce(state, decision)


class FirstAllowedPolicy(CompiledInteractionPolicy):
    """Backwards-compatible name for deterministic tests and offline runs."""

    async def select_action(
        self,
        state: SharedProblemState,
        contract: InteractionContract,
    ) -> PolicyDecision:
        return await super().select_action(state, contract)
