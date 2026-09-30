"""M6 interaction-policy cases built from structured, current-turn state."""

from __future__ import annotations

from pydantic import BaseModel, Field


class M6InteractionCase(BaseModel):
    case_id: str
    domain: str
    user_condition: str
    user_state: dict[str, str | None] = Field(default_factory=dict)
    overrides: dict[str, str | bool | None] = Field(default_factory=dict)
    response: str
    expected_depth: str
    expected_max_actions: int
    expected_progress: str = "milestones"


def load_cases() -> list[M6InteractionCase]:
    long_detail = "已完成低风险检查并保留证据。" + "验证记录显示当前状态正在收敛。" * 35
    raw = [
        ("it_deadline", "it", "deadline", {"deadline": "15 minutes", "patience": "low"}, {}, "minimal", 1, "milestones"),
        ("saas_frustrated", "saas", "frustrated", {"frustration": "high", "delivery_preference": "result_first"}, {}, "minimal", 1, "milestones"),
        ("logistics_novice", "logistics", "novice", {"domain_knowledge": "low", "clarity": "low"}, {}, "guided", 1, "milestones"),
        ("education_fragmented", "education", "fragmented", {"clarity": "low", "progress_preference": "every_step"}, {}, "guided", 1, "every_step"),
        ("finance_expert", "finance", "expert", {"domain_knowledge": "high"}, {}, "expert", 2, "milestones"),
        ("medical_detail", "medical", "detail_request", {"explanation_preference": "detailed"}, {}, "expert", 2, "milestones"),
        ("sales_result", "sales", "result_first", {"delivery_preference": "result_first"}, {}, "minimal", 1, "milestones"),
        ("repair_shared", "repair", "shared", {"collaboration_preference": "shared"}, {}, "guided", 1, "milestones"),
        ("merchant_steps", "merchant", "procedural", {"collaboration_preference": "step_by_step"}, {}, "guided", 1, "milestones"),
        ("cross_border_blockers", "cross_border", "low_patience", {"progress_preference": "blockers_only", "patience": "low"}, {}, "minimal", 1, "blockers_only"),
        ("membership_override", "membership", "preference_correction", {"patience": "low"}, {"expression_depth": "expert"}, "expert", 2, "milestones"),
        ("promotion_override", "promotion", "preference_correction", {"domain_knowledge": "high"}, {"expression_depth": "minimal", "progress_cadence": "every_step"}, "minimal", 1, "every_step"),
    ]
    return [
        M6InteractionCase(
            case_id=case_id,
            domain=domain,
            user_condition=condition,
            user_state=user_state,
            overrides=overrides,
            response=long_detail,
            expected_depth=depth,
            expected_max_actions=actions,
            expected_progress=progress,
        )
        for case_id, domain, condition, user_state, overrides, depth, actions, progress in raw
    ]


__all__ = ["M6InteractionCase", "load_cases"]
