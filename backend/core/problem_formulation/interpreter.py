"""LangChain structured interpreter for one user turn."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from core.llm_utils import extract_text_content

from .models import (
    SemanticRecoveryInterpretation,
    SharedProblemState,
    TurnInterpretation,
)


class TurnInterpreter(Protocol):
    async def interpret(
        self,
        message: str,
        current_state: SharedProblemState,
    ) -> TurnInterpretation: ...


class LangChainTurnInterpreter:
    """Use the existing provider client through a LangChain runnable pipeline."""

    SYSTEM_PROMPT = """你负责把用户本轮话语解析成共享问题表征的候选更新，而不是解决问题。

先在语义层面覆盖用户整段表达，再输出结构。逐项检查：
- 用户希望现实达到的结果；
- 用户亲自观察到的现象；
- 已采取的动作、当时条件与真实结果；
- 原因判断、确定性变化和对旧说法的纠正；
- 会改变交互成本或策略的当前约束，包括知识、表达困难、挫败、耐心、控制感、努力意愿与时间压力。
同一句可以同时承载多个角色。不要因为已经识别到故障内容，就忽略句尾的时间、阅读负担或失败经历；也不要依赖固定措辞理解这些语义。

硬约束：
1. Describe before explain。直接观察、已执行动作、原因猜测必须分开。
2. 用户说“肯定是 X”仍然只是 hypothesis，不是事实。
3. situation_updates、claims、user_state_signals 中每一项都必须带用户原文中的连续 evidence_quote。
4. 用户没有说出的时间、环境、影响和 deadline 不得补全；宁可留空。
5. goal 只表示用户希望外部世界达到的结果（例如“恢复 VPN”“先让服务可用”），不表示对话过程或用户预设的原因/方案。必须分类 goal_kind：外部结果=outcome；“按 X 故障处理/直接重置”=procedure_preference；“继续问/愿意配合”=cooperation；“解释原因”=explanation_request。只有 goal_kind=outcome 才设置 operational=true。“告诉我下一步”本身不是 outcome。已有 operational goal 时，除非用户明确改变外部结果，否则不得设置 replaces_current_goal=true。
5.1 用户明确说明“怎样才算解决”时写入 success_criteria，每项附连续原文 quote。不要把系统自己设想的验收条件补进去。
6. missing_evidence 只列会改变后续方案、安全边界或成功判定的信息。用 blocking 标记“不知道就不能安全进入解决阶段”的证据；只会完善解释的信息必须 blocking=false。综合 decision_impact、answerability 和 user_burden，避免为了完整而追问。这三个粗粒度字段只能使用 unknown/low/medium/high，不能使用 critical。
7. user_state_signals 不是给用户贴人格标签，只记录当前 Case、当前轮中有原文依据的知识程度、清晰度、挫败感、操作意愿、控制感、耐心和 deadline。它们可在后续轮次变化；若用户明确撤销 deadline，输出 deadline=none。不得用旧轮次画像压过本轮直接表达。
7.1 用户明确表达希望只看结果、希望了解过程、希望系统先自主推进、希望逐步确认或希望怎样获知进度时，也写入 user_state_signals。字段和值限定为：delivery_preference=result_first|balanced|process_visible；explanation_preference=minimal|on_demand|detailed；collaboration_preference=agent_led|shared|step_by_step；progress_preference=blockers_only|milestones|every_step。没有明确语义证据就不要推断长期偏好。
8. 用户说做过某个动作时，尽量同时提取 target、conditions、expected_result、reported_result、outcome、repetitions 和 retry_condition；reported result 仍不是已验证事实。repetitions 只有原文给出精确次数时才填整数，“多次/好几次”等定性描述保留在 evidence_quote 并令 repetitions=null。明确“没用/仍失败/没有变化”时 outcome=failure，部分改善时 outcome=partial，成功时 outcome=success，信息不足时 unknown。
9. 当前状态中已有 missing_evidence 只有在本轮原话直接提供了对应信息时，才能写入 resolved_evidence；必须附本轮连续原文 quote。不能因为本轮没有再次提及就删除。
9.1 多问题 Case 中，missing_evidence、resolved_evidence 和 unavailable_evidence 必须保留对应 issue_id；同名证据不能跨问题互相关闭。若当前只有一个 active issue，可复制该 issue_id。
10. 当前状态中已有 contradiction 只有在本轮原话明确消解时，才能写入 resolved_contradictions，并附本轮连续原文 quote。
11. 用户明确纠正、撤回或降低先前声明的确定性时写入 claim_revisions。按语义与当前状态中的旧 claim 对齐，即使用户本轮换了说法；original_content 必须逐字复制状态中最接近的原声明，replacement_content 表示用户本轮保留的新认识，evidence_quote 必须是本轮原文。不要把新补充误写成纠正。例如状态中是“缓存坏了”、用户说“缓存损坏只是猜测”时，必须修订旧 claim，而不是因为字面不同忽略纠错。
12. conversation_act 按整句话的交际目的填写：正常求助或继续补充 Case 为 case_formulation；明确要求人工接手为 human_handoff；明确结束当前协作为 stop；只有社交性表达且没有求助内容为 social。不要按关键词匹配；混合表达包含实际问题时优先 case_formulation。
13. 用户可能一次抛出多个问题，也可能跨轮继续补充。必须分别写入 reported_issues，并保留每个直接观察为 situation_updates/observation claim，不得只抽取最显眼的一项，也不得因为本轮出现新问题就覆盖当前状态中的旧问题。若语义上是已有问题的新说法，逐字复制当前状态中的 issue_id 到 existing_issue_id；无法确定就创建新候选，不按关键词或字符串猜。只有用户明确要求优先处理该问题时 make_active=true。relation 和 related_issue_id 不确定时保持 unknown/空值，禁止猜因果。
13.1 当前状态中的 original/current outcome 是 Case 主线。新症状、新问题、原因猜测和方法偏好默认不能改写主线；只有用户明确改变现实结果时才设置 replaces_current_goal=true。
12. 如果系统上一轮复述了目标，本轮用户明确确认或纠正，写入 goal_acknowledgement；沉默、继续描述或回答别的问题不算确认。
13. 不输出建议、诊断或解决方案。
14. 如果 evidence_catalog 非空，missing_evidence.key 必须从该列表原样选择；不得创造同义 key。用户本轮提供了目录中的证据时，用相同 key 写入 resolved_evidence。
15. missing_evidence 中 last_asked_turn_id 等于当前 turn_count 的项，就是系统上一轮刚问的问题。若用户本轮在回答它，必须使用该项的原始 key 写入 resolved_evidence；不要因为 key 是英文而忽略语义上的回答。
16. 如果用户明确表示上一轮所问证据不知道、无法查看、没有权限或当前拿不到，写入 unavailable_evidence，保留用户原文 quote 和原因；不得伪造 resolved_evidence。
17. 失败操作是高显著性负反馈，但失败只说明该动作在当时条件下没有达到预期，不代表用户猜测的根因已经成立。

当前状态：
{current_state}

领域先验仅用于理解词义，不代表问题已经理解：{domain_prior}

当前环境可提供的证据键（可能为空）：{evidence_catalog}

系统上一轮直接追问的证据键：{previous_target}
系统上一轮的问题：{previous_question}

{format_instructions}
"""

    RECOVERY_SYSTEM_PROMPT = """上一次结构化解释遗漏了用户本轮表达。请重新做语义解释，不按关键词分类，也不解决问题。

从整句话和对话上下文识别语义关系：用户想让现实达到什么状态、实际观察到什么、已经做过什么及结果、哪些内容只是原因解释、以及时间/耐心/控制感/认知负担等当前约束。不同表达可以指向相同语义状态。

用户明确给出的成功判据和结果/过程、解释深度、协作方式、进度通知偏好也要保留。多个问题分别写入 reported_issues；新问题不得覆盖已有目标或 active issue，除非用户明确要求改变主线或优先级。

同时按整句话的交际目的填写 conversation_act：case_formulation、human_handoff、stop 或 social。不得按词面触发；混合表达包含实际问题时优先保留 Case。

每个候选必须附用户原话中的连续 evidence_quote。观察、行动、结果、假设不得混合；没有依据的字段留空。若用户确实只提供了无法结构化的社交性话语，才允许返回空更新。

当前状态：
{current_state}

当前环境可提供的证据键：{evidence_catalog}

{format_instructions}
"""

    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model
        self._parser = PydanticOutputParser(pydantic_object=TurnInterpretation)
        self._recovery_parser = PydanticOutputParser(pydantic_object=SemanticRecoveryInterpretation)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.SYSTEM_PROMPT),
                ("human", "用户本轮原话：\n{message}"),
            ]
        ).partial(format_instructions=self._parser.get_format_instructions())
        self._chain = self._prompt | RunnableLambda(self._call_model) | self._parser
        self._recovery_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.RECOVERY_SYSTEM_PROMPT),
                ("human", "用户本轮原话：\n{message}"),
            ]
        ).partial(format_instructions=self._recovery_parser.get_format_instructions())
        self._recovery_chain = (
            self._recovery_prompt | RunnableLambda(self._call_model) | self._recovery_parser
        )

    async def _call_model(self, prompt_value: Any) -> str:
        system_parts = []
        messages = []
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
            "max_tokens": 1600,
            "temperature": 0.0,
            "system": "\n\n".join(system_parts),
            "messages": messages,
        }
        if getattr(self._client, "supports_json_object", False):
            request["response_format"] = {"type": "json_object"}
        response = await self._client.messages.create(
            **request,
        )
        return extract_text_content(response.content)

    async def interpret(
        self,
        message: str,
        current_state: SharedProblemState,
    ) -> TurnInterpretation:
        compact_state = current_state.model_dump(
            mode="json",
            exclude={"formulation_history"},
        )
        previous = (
            current_state.formulation_history[-1] if current_state.formulation_history else None
        )
        inputs = {
            "message": message,
            "domain_prior": current_state.domain_prior,
            "evidence_catalog": json.dumps(current_state.evidence_catalog, ensure_ascii=False),
            "previous_target": previous.target_evidence if previous else "",
            "previous_question": previous.question if previous else "",
            "current_state": json.dumps(compact_state, ensure_ascii=False),
        }
        interpretation = await self._chain.ainvoke(inputs)
        if not self._is_low_coverage(message, interpretation):
            interpretation.semantic_coverage_complete = True
            interpretation.unmapped_spans = []
            return interpretation
        compact_recovery = await self._recovery_chain.ainvoke(inputs)
        recovered = compact_recovery.to_turn_interpretation()
        recovered._extraction_attempts = 2
        recovered.unmapped_spans = self._uncovered_segments(message, recovered)
        recovered.semantic_coverage_complete = not recovered.unmapped_spans
        return recovered

    @classmethod
    def _is_low_coverage(
        cls,
        message: str,
        interpretation: TurnInterpretation,
    ) -> bool:
        if interpretation.conversation_act.value != "case_formulation":
            return False
        goal = interpretation.goal
        has_semantic_update = any(
            [
                goal.explicit_goal,
                goal.inferred_goal,
                goal.scope_constraints,
                goal.ambiguities,
                interpretation.situation_updates,
                interpretation.claims,
                interpretation.claim_revisions,
                interpretation.user_state_signals,
                interpretation.resolved_evidence,
                interpretation.unavailable_evidence,
                interpretation.resolved_contradictions,
                interpretation.goal_acknowledgement,
            ]
        )
        if not has_semantic_update:
            return True
        return bool(cls._uncovered_segments(message, interpretation))

    @classmethod
    def _uncovered_segments(
        cls,
        message: str,
        interpretation: TurnInterpretation,
    ) -> list[str]:
        if interpretation.conversation_act.value != "case_formulation":
            return []
        normalized_quotes = [
            cls._normalize(item) for item in cls._evidence_quotes(interpretation) if item
        ]
        segments = [
            item.strip()
            for item in re.split(r"[，,。；;！？!?\n]+", message)
            if len(cls._normalize(item)) >= 3
        ]
        return [
            segment
            for segment in segments
            if not any(
                cls._normalize(segment) in quote or quote in cls._normalize(segment)
                for quote in normalized_quotes
                if quote
            )
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", "", value or "").casefold()

    @staticmethod
    def _evidence_quotes(interpretation: TurnInterpretation) -> list[str]:
        goal = interpretation.goal
        acknowledgement = interpretation.goal_acknowledgement
        return [
            *([goal.explicit_goal_quote] if goal.explicit_goal_quote else []),
            *(item.evidence_quote for item in goal.success_criteria),
            *(item.evidence_quote for item in interpretation.situation_updates),
            *(item.evidence_quote for item in interpretation.reported_issues),
            *(item.evidence_quote for item in interpretation.claims),
            *(item.evidence_quote for item in interpretation.claim_revisions),
            *(item.evidence_quote for item in interpretation.user_state_signals),
            *(item.evidence_quote for item in interpretation.resolved_evidence),
            *(item.evidence_quote for item in interpretation.unavailable_evidence),
            *(item.evidence_quote for item in interpretation.resolved_contradictions),
            *([acknowledgement.evidence_quote] if acknowledgement else []),
        ]
