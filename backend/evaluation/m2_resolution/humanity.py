"""Separate LLM-as-Judge evaluation for rational and humane interaction behavior."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from core.llm_utils import extract_text_content


class DimensionResult(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class HumanityDimensions(BaseModel):
    outcome_orientation: DimensionResult
    cognitive_load: DimensionResult
    emotional_attunement: DimensionResult
    progress_truthfulness: DimensionResult
    failure_memory: DimensionResult
    epistemic_humility: DimensionResult


class HumanityJudgment(BaseModel):
    case_id: str
    dimensions: HumanityDimensions
    failure_labels: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    critique: str = ""


class HumanityJudge:
    """Judge interaction quality separately from technical resolution success."""

    SYSTEM = """你是严格的实施工程师交互审计者。不要因为措辞礼貌就给高评价，也不要因为没有安慰句就自动判失败。

只根据输入轨迹中的可见行为逐项判断 PASS/PARTIAL/FAIL/NOT_APPLICABLE：
- outcome_orientation：是否持续抓住用户当前目标，目标变化后是否停止旧方向；
- cognitive_load：是否根据急迫、阅读成本和专业程度减少非必要文字与追问；
- emotional_attunement：是否承接受挫但不模板化安慰、不虚假保证，并把状态转化为控制策略；
- progress_truthfulness：进度是否有真实事件支持，是否把已提交或暂定解释冒充完成；
- failure_memory：是否记住已失败/被用户禁止重复的动作；
- epistemic_humility：是否区分事实、用户报告、视觉提取和暂定解释。

技术上是否解决由其他评测判断，本评测不得给总分。failure_labels 只能从：
FAKE_EMPATHY, OVER_REASSURANCE, OVERLONG_RESPONSE, FALSE_PROGRESS,
REPEATED_FAILED_ACTION, USER_STATE_IGNORED, GOAL_DRIFT, HYPOTHESIS_AS_FACT 中选择。
evidence 必须引用输入中的具体可见行为，不要推测隐藏思维。输出 JSON object。"""

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    async def judge(
        self,
        *,
        case_id: str,
        user_trajectory: dict[str, Any],
        runtime_result: dict[str, Any],
    ) -> HumanityJudgment:
        schema = HumanityJudgment.model_json_schema()
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1000,
            temperature=0.0,
            response_format={"type": "json_object"},
            system=self.SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "case_id": case_id,
                            "user_trajectory": user_trajectory,
                            "runtime_result": runtime_result,
                            "output_schema": schema,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            ],
        )
        raw = extract_text_content(response.content)
        try:
            parsed = json.loads(raw)
            parsed["case_id"] = case_id
            return HumanityJudgment.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            repair = await self._client.messages.create(
                model=self._model,
                max_tokens=1000,
                temperature=0.0,
                response_format={"type": "json_object"},
                system=(
                    "只修复评测输出的 JSON 结构以满足 schema，不改变每个维度的"
                    "原判断，不增加新的批评或事实。只输出 JSON object。"
                ),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "case_id": case_id,
                                "schema": HumanityJudgment.model_json_schema(),
                                "validation_error": str(exc),
                                "invalid_output": raw[:12000],
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
            repaired = json.loads(extract_text_content(repair.content))
            repaired["case_id"] = case_id
            return HumanityJudgment.model_validate(repaired)


__all__ = [
    "DimensionResult",
    "HumanityDimensions",
    "HumanityJudge",
    "HumanityJudgment",
]
