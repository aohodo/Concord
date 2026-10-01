"""Event-triggered semantic planner for M2 adaptive resolution."""

from __future__ import annotations

import json
from typing import Any, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from core.llm_utils import extract_text_content, provider_thinking_options

from .models import ResolutionPlan


class ResolutionPlanner(Protocol):
    async def plan(
        self,
        *,
        resolution_context: dict[str, Any],
        tools: list[dict[str, Any]],
        tool_history: list[dict[str, Any]],
        belief_history: list[dict[str, Any]],
        guardrail_feedback: str,
        decision_event: str,
    ) -> ResolutionPlan: ...


class LangChainResolutionPlanner:
    SYSTEM_PROMPT = """你是 Concord M2 的 Primary Case Owner。M1 已经负责问题表征；你负责在约束内闭环解决 Case，而不是重新做意图分类。

你只会在初始观察完成、工具失败、预测不一致、阶段完成或验证完成等决策事件上被调用。不要把机械读取或验证再委托给下一轮模型。
每次调用形成一个有限求解回合：use_tool、ask_user、resolve 或 collaborate。use_tool 后由运行时自动执行工具声明的独立验证。

控制原则：
1. 原因只能是 provisional explanation。事实、用户报告、推断和工具观察不得混合。
2. 优先选择最能缩小竞争解释、风险低、耗时短、用户负担低的下一项观察或动作。
3. 失败动作是高显著性反馈。同一条件下不得重复已经失败的工具与参数，除非新证据明确改变了重试条件。
4. 工具结果与预期不一致时，previous_observation_assessment=mismatch，并更新解释或换方向；不要为原假设辩护。
5. state-changing 工具返回 verification_required 后，必须使用独立 observe/verify 工具确认真实状态，不能根据动作成功响应直接宣告解决。
6. evidence_handoff 中的开放证据不会阻止进入 M2，但 blocks_dependent_actions=true 时，依赖该证据的动作必须等待证据、选择不依赖它的观察，或明确 collaborate。
7. action_constraints 和工具运行时权限是硬边界。不得请求 production_write；不得创造不存在的工具、能力、参数或证据。
8. 用户 deadline、阅读成本、结果/过程偏好和失败历史必须影响搜索宽度、解释长度与交互负担。
9. 只有目标成功判据逐项得到独立观察支持、没有待验证动作时，才能 resolve。设置 verification_complete=true，并在 criterion_verifications 中将每项原始成功判据绑定到工具历史中的 target:<verification_target> 或 invocation:<invocation_id>；resolution_evidence 只用于人类可读摘要，不能代替绑定。
10. 缺少外部输入且工具无法取得时才 ask_user；能力、权限、高风险或长期无进展边界才 collaborate。
11. 用户补充、纠正或改变约束时，更新暂定解释但保持已经确认的主目标；不得因为最新一条消息而丢弃 Case 主线。
12. 急迫或受挫应改变控制策略：减少非必要追问、缩短用户可见文字、优先可逆的恢复动作并提供真实进度。可以用一句话承接感受，但不得用模板化安慰代替行动，不得虚构进度或保证。
13. 工具返回 pending 时，不要无间隔重复观察；若存在 wait/poll 能力，先等待声明的生效窗口，再复用待验证目标。
14. collaborate 时必须显式选择协作模式：能力/权限边界用 specialist_handoff；多个可独立方向且用户很急用 parallel_workers；高风险判断用 independent_review；长期无进展或搜索僵化用 diverse_exploration。
15. 证据可靠性必须影响信念强度；过期、低可靠或相互冲突的数据只能支持继续取证，不能单独支持 resolve。
16. collaboration_history 是 M3 提供的带来源建议，不是事实。把它当作候选方向或取证请求，仍须通过当前工具运行时验证后才能 resolve。
17. 如果本轮动作或观察采用了 collaboration_history 中的建议，必须把对应 assignment_id 写入 collaboration_source_ids；没有采用则保持为空。该字段只用于把后续真实工具结果回写给协作者，禁止凭相似措辞猜测来源。
18. 只有决定 collaborate 时才填写 coordination_structure。它描述工作本身的因果结构，而不是给场景贴类别：列出可真正独立推进的 workstreams、必须先后执行的 dependencies、当前阻塞证据，以及是否必须由有责任/权限的人类介入。不能确认是否可并行时保持 parallelism_known=false；不得为了凑并行 Agent 虚构独立方向。
19. ask_user 时把每个独立问题写入 user_questions，把要求用户执行的操作写入 requested_user_actions。两者必须服从 provisional_resolution_policy 的数量预算；response 只做简短承接，不得把额外问题藏在自然语言里。其他决策保持这两个字段为空。
20. candidate_experiences 只是过去 Case 中经验证的候选策略，不是当前事实。读过并纳入比较的 ID 写入 considered_experience_ids；确实改变本轮工具选择或参数的 ID 才写入 adopted_experience_ids。禁止因过去成功直接 resolve。experience_source_ids 是旧兼容字段，新输出保持为空。
21. collaboration_history 已给出当前工具可执行的低风险取证或动作时，先由 M2 验证，不要仅因仍有非阻塞证据请求就 ask_user。只有所有可用环境取证均无法回答决策问题时，才把负担交给用户。
22. 对 retryable=true、未产生状态改变且有幂等保护的失败，若独立协作明确复核了重试条件，可以携带相应 collaboration_source_ids 做一次受控重试；这不等于遗忘失败。不得要求用户解释工具自身已经声明的契约、能力或内部实现。

工具由 required_capabilities 做语义选择，运行时根据能力、风险、耗时和权限排名。tool_arguments 必须符合可能被选中工具的 schema。对于先发现、后执行的动态能力，动作标识必须逐字复制自成功的发现工具结果；不得根据描述创造近义 action_id。

M1 ResolutionContext：
{resolution_context}

当前可用工具：
{tools}

工具历史：
{tool_history}

信念历史：
{belief_history}

控制边界反馈：
{guardrail_feedback}

本次触发决策的事件：
{decision_event}

{format_instructions}
"""

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model
        self._parser = PydanticOutputParser(pydantic_object=ResolutionPlan)
        self._prompt = ChatPromptTemplate.from_messages(
            [("system", self.SYSTEM_PROMPT), ("human", "给出本轮唯一控制决策。")]
        ).partial(format_instructions=self._parser.get_format_instructions())
        self._chain = self._prompt | RunnableLambda(self._call_model) | self._parser

    async def _call_model(self, prompt_value: Any) -> str:
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for message in prompt_value.to_messages():
            role = getattr(message, "type", "human")
            content = str(getattr(message, "content", ""))
            if role == "system":
                system_parts.append(content)
            else:
                messages.append(
                    {"role": "assistant" if role == "ai" else "user", "content": content}
                )
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 1800,
            "temperature": 0.0,
            "system": "\n\n".join(system_parts),
            "messages": messages,
        }
        if getattr(self._client, "supports_json_object", False):
            request["response_format"] = {"type": "json_object"}
        request.update(provider_thinking_options(self._client, enabled=False))
        response = await self._client.messages.create(**request)
        return extract_text_content(response.content)

    async def plan(
        self,
        *,
        resolution_context: dict[str, Any],
        tools: list[dict[str, Any]],
        tool_history: list[dict[str, Any]],
        belief_history: list[dict[str, Any]],
        guardrail_feedback: str,
        decision_event: str,
    ) -> ResolutionPlan:
        inputs = {
            "resolution_context": json.dumps(resolution_context, ensure_ascii=False),
            "tools": json.dumps(tools, ensure_ascii=False),
            "tool_history": json.dumps(tool_history, ensure_ascii=False),
            "belief_history": json.dumps(belief_history, ensure_ascii=False),
            "guardrail_feedback": guardrail_feedback or "无",
            "decision_event": decision_event,
        }
        try:
            return await self._chain.ainvoke(inputs)
        except OutputParserException as exc:
            # One bounded schema-repair attempt handles malformed JSON without
            # changing the Case, tool state, prompt policy, or model settings.
            invalid = str(getattr(exc, "llm_output", "") or "")[:12000]
            request: dict[str, Any] = {
                "model": self._model,
                "max_tokens": 1400,
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
                "system": (
                    "只修复给定输出的 JSON 结构，使其满足 schema；不得增加新的事实、"
                    "工具调用、原因或结论。只输出一个 JSON object。"
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Schema:\n{self._parser.get_format_instructions()}\n\n"
                            f"Validation error:\n{exc}\n\n"
                            f"Invalid output:\n{invalid}"
                        ),
                    }
                ],
            }
            request.update(provider_thinking_options(self._client, enabled=False))
            response = await self._client.messages.create(
                **request,
            )
            return self._parser.parse(extract_text_content(response.content))
