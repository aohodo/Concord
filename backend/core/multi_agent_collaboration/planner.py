"""LangChain structured coordination and collaborator implementations for M3."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ValidationError

from core.llm_utils import extract_text_content

from .models import (
    AgentContribution,
    AgentProfile,
    AvailableAgent,
    CollaborationAssignment,
    CollaborationPlan,
    CollaborationSynthesis,
    ContextPolicy,
)
from .usage import record_model_response


class CollaborationCoordinator(Protocol):
    async def plan(
        self,
        *,
        handoff: dict[str, Any],
        available_agents: list[AvailableAgent],
        prior_contributions: list[AgentContribution],
        round_index: int,
        recruited_agents: list[str],
    ) -> CollaborationPlan: ...

    async def synthesize(
        self,
        *,
        handoff: dict[str, Any],
        plans: list[CollaborationPlan],
        contributions: list[AgentContribution],
        round_index: int,
    ) -> CollaborationSynthesis: ...


class CollaborationWorker(Protocol):
    async def contribute(
        self,
        *,
        profile: AgentProfile,
        assignment: CollaborationAssignment,
        context: dict[str, Any],
    ) -> AgentContribution: ...


T = TypeVar("T", bound=BaseModel)


class _StructuredLLM:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    async def invoke(
        self,
        *,
        schema: type[T],
        system: str,
        payload: dict[str, Any],
        max_tokens: int,
    ) -> T:
        parser = PydanticOutputParser(pydantic_object=schema)
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "请严格按照 output_schema 输出一个 JSON object。\n"
                        + json.dumps(
                            {
                                **payload,
                                "output_schema": schema.model_json_schema(),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                }
            ],
        }
        if getattr(self._client, "supports_json_object", False):
            request["response_format"] = {"type": "json_object"}
        response = await self._client.messages.create(**request)
        record_model_response(response)
        raw = extract_text_content(response.content)
        try:
            return parser.parse(raw)
        except (OutputParserException, ValidationError, json.JSONDecodeError) as exc:
            repair = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=0.0,
                response_format={"type": "json_object"},
                system=(
                    "只修复给定输出的 JSON 结构以符合 schema。不得增加事实、"
                    "冲突或结论。只能使用 original_payload 中已有的 Agent、"
                    "证据与约束；允许补全与 decision 一致的必要结构字段和任务。"
                    "只输出 JSON object。"
                ),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "schema": schema.model_json_schema(),
                                "validation_error": str(exc),
                                "invalid_output": raw[:12000],
                                "original_payload": payload,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
            record_model_response(repair)
            return parser.parse(extract_text_content(repair.content))


class LangChainCollaborationCoordinator:
    PLAN_SYSTEM = """你是 Concord M3 的协作负责人，不是答案生成器。M2 Primary Case Owner 仍拥有 Case 和所有环境动作。

你的任务是判断协作是否比 M2 单独继续更有价值，并按当前认知瓶颈组织最小团队：
- 能力或权限边界：specialist_handoff；
- 多个真正独立的方向且时间紧：parallel_workers；
- 高风险方案需要第二意见：independent_review；
- 连续无进展或搜索锚定：diverse_exploration。

团队规模契约：specialist_handoff 只能 1 人；parallel_workers 首轮 2-4 人；independent_review 1-2 人；diverse_exploration 首轮 2-4 人。第二轮只补一个明确互补缺口也可以。handoff.coordination_structure 是环境提供的结构事实：parallelism_known=true 且 independent_workstreams 少于 2 时禁止 parallel_workers；human_authority_required=true 时必须 require_human。

只在 expected_information_gain > estimated_coordination_cost 时 recruit。简单问题、强依赖串行任务和已有充分证据不得为了形式拉多个 Agent。任务必须互不重复，有明确目标、输出和边界。独立复核与替代探索必须 independent=true，并使用 facts_only 或 targeted，避免把当前多数假设作为锚点。优先根据能力与经下游结果验证的 reliability 选人，不使用 Agent 自报 confidence 作为历史可靠性。最多四人。

这是 transactive memory 式“知道谁擅长什么”的调度，不是固定领域 fan-out。输出结构化计划。"""

    SYNTHESIS_SYSTEM = """你是 Concord M3 的 Case 协作合成者。所有 Agent 输出都只是带来源的建议或假设，不是事实；M2 才能通过环境工具执行与验证。

必须显式识别四类冲突：
- FACT：不得投票，要求重新取得更可靠或更接近源头的证据，并阻塞依赖动作；
- HYPOTHESIS：保留竞争解释，寻找能区分它们的证据；
- ACTION：比较风险、时间、成本、可逆性和信息价值；
- GOAL：停止技术裁决，回到用户目标与硬约束。

只有新增贡献能减少不确定性或风险时才扩员。贡献重复、边际收益不高、协调成本过高、达到证据/权限边界或已有可验证下一步时停止。不得用多数票制造共识，不得把动作建议写成已经完成。多个结论如果依赖相同 basis_refs，只算一个证据来源，并写入 shared_source_warnings。只有确实仍缺少互补能力时才给出 additional_assignments。输出结构化合成。"""

    def __init__(self, client: Any, model: str) -> None:
        self._structured = _StructuredLLM(client, model)

    async def plan(
        self,
        *,
        handoff: dict[str, Any],
        available_agents: list[AvailableAgent],
        prior_contributions: list[AgentContribution],
        round_index: int,
        recruited_agents: list[str],
    ) -> CollaborationPlan:
        return await self._structured.invoke(
            schema=CollaborationPlan,
            system=self.PLAN_SYSTEM,
            payload={
                "m2_handoff": handoff,
                "available_agents": [
                    {
                        **item.profile.model_dump(mode="json"),
                        "observed_reliability": item.reliability,
                        "verified_samples": item.performance.samples,
                        "average_latency_ms": item.performance.average_latency_ms,
                    }
                    for item in available_agents
                ],
                "prior_contributions": [
                    item.model_dump(mode="json") for item in prior_contributions
                ],
                "round_index": round_index,
                "already_recruited": recruited_agents,
            },
            max_tokens=2800,
        )

    async def synthesize(
        self,
        *,
        handoff: dict[str, Any],
        plans: list[CollaborationPlan],
        contributions: list[AgentContribution],
        round_index: int,
    ) -> CollaborationSynthesis:
        return await self._structured.invoke(
            schema=CollaborationSynthesis,
            system=self.SYNTHESIS_SYSTEM,
            payload={
                "m2_handoff": handoff,
                "plans": [item.model_dump(mode="json") for item in plans],
                "contributions": [
                    item.model_dump(mode="json") for item in contributions
                ],
                "round_index": round_index,
            },
            max_tokens=2600,
        )


class LangChainCollaborationWorker:
    SYSTEM = """你是 Concord M3 中被临时招募的协作者。严格完成 assignment，不争夺 Case Owner，不直接操作环境，不宣称问题已解决。

规则：
1. 区分 confirmed fact、tool observation、user-reported evidence 和 hypothesis。
2. 你的新内容只能标为 hypothesis、action_proposal、evidence_request 或 constraint；不得凭推断生成 observation。
3. basis_refs 必须指向输入中可追溯的证据键、工具记录或贡献；没有依据就写 limitation。
4. 动作候选应说明风险、预期时间、可逆性和信息价值。
5. 如果 assignment.independent=true，不猜测其他 Agent 会怎么回答，也不迎合当前主假设。
6. 简短输出真正新增的信息；不要复述整个 Case。"""

    def __init__(self, client: Any, model: str) -> None:
        self._structured = _StructuredLLM(client, model)

    async def contribute(
        self,
        *,
        profile: AgentProfile,
        assignment: CollaborationAssignment,
        context: dict[str, Any],
    ) -> AgentContribution:
        started = time.monotonic()
        result = await self._structured.invoke(
            schema=AgentContribution,
            system=self.SYSTEM,
            payload={
                "role": profile.model_dump(mode="json"),
                "assignment": assignment.model_dump(mode="json"),
                "case_context": context,
            },
            max_tokens=1400,
        )
        return result.model_copy(
            update={
                "assignment_id": assignment.assignment_id,
                "agent_id": profile.agent_id,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
        )


def build_worker_context(
    handoff: dict[str, Any], assignment: CollaborationAssignment
) -> dict[str, Any]:
    """Apply deliberate context isolation to reduce anchoring and herding."""

    base = {
        "case_id": handoff.get("case_id"),
        "goal": handoff.get("goal", {}),
        "action_constraints": handoff.get("action_constraints", {}),
        "reported_evidence": handoff.get("reported_evidence", []),
        "observed_evidence": handoff.get("observed_evidence", []),
        "confirmed_facts": handoff.get("confirmed_facts", []),
        "failed_directions": handoff.get("failed_directions", []),
        "open_evidence": handoff.get("open_evidence", []),
        "permission_boundary": handoff.get("permission_boundary", []),
        "focus_evidence": assignment.focus_evidence,
    }
    if assignment.context_policy is ContextPolicy.FULL_HANDOFF:
        return {
            **handoff,
            "assignment_focus": assignment.objective,
        }
    if assignment.context_policy is ContextPolicy.FACTS_ONLY:
        return base
    return {**base, "assignment_focus": assignment.objective}


__all__ = [
    "CollaborationCoordinator",
    "CollaborationWorker",
    "LangChainCollaborationCoordinator",
    "LangChainCollaborationWorker",
    "build_worker_context",
]
