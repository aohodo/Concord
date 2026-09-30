"""Resource-rational filtering and ranking for already-understood tool needs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .contracts import OperationMode, RiskLevel, ToolContext
from .policy import ToolPolicy
from .registry import ToolRegistry

_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class ToolNeed(BaseModel):
    """Structured need produced by M2; it is semantic state, not keyword matching."""

    capabilities: set[str]
    expected_information_gain: dict[str, float] = Field(default_factory=dict)
    expected_progress: dict[str, float] = Field(default_factory=dict)
    allowed_modes: set[OperationMode] = Field(
        default_factory=lambda: {OperationMode.READ_ONLY, OperationMode.SIMULATED_WRITE}
    )
    max_risk: RiskLevel = RiskLevel.MEDIUM
    latency_budget_ms: float | None = None
    user_burden_budget: float | None = None
    information_weight: float = 1.0
    progress_weight: float = 1.0
    latency_weight: float = 0.15
    risk_weight: float = 0.4
    burden_weight: float = 0.5


class ToolSelection(BaseModel):
    tool_id: str
    score: float
    reason: str


class ToolExclusion(BaseModel):
    tool_id: str
    code: str
    reason: str
    operation_mode: OperationMode


class ToolSelectionAssessment(BaseModel):
    """Ranked candidates plus machine-readable reasons why others were excluded."""

    matching_tool_ids: list[str] = Field(default_factory=list)
    ranked: list[ToolSelection] = Field(default_factory=list)
    exclusions: list[ToolExclusion] = Field(default_factory=list)


class ResourceRationalToolSelector:
    """Choose the cheapest sufficient capability under current constraints."""

    def __init__(self, registry: ToolRegistry, policy: ToolPolicy | None = None):
        self._registry = registry
        self._policy = policy or ToolPolicy()

    def assess(
        self, need: ToolNeed, context: ToolContext
    ) -> ToolSelectionAssessment:
        ranked: list[ToolSelection] = []
        exclusions: list[ToolExclusion] = []
        matching = self._registry.matching(need.capabilities)
        for spec in matching:
            if not spec.is_available(context):
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code="TOOL_UNAVAILABLE",
                        reason="tool availability predicate rejected this context",
                        operation_mode=spec.operation_mode,
                    )
                )
                continue
            if spec.operation_mode not in need.allowed_modes:
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code="OPERATION_MODE_NOT_ALLOWED",
                        reason=f"{spec.operation_mode.value} is outside allowed modes",
                        operation_mode=spec.operation_mode,
                    )
                )
                continue
            if _RISK_ORDER[spec.risk_level] > _RISK_ORDER[need.max_risk]:
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code="RISK_LIMIT_EXCEEDED",
                        reason=f"{spec.risk_level.value} exceeds the current risk limit",
                        operation_mode=spec.operation_mode,
                    )
                )
                continue
            if need.latency_budget_ms is not None and spec.expected_latency_ms > need.latency_budget_ms:
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code="LATENCY_BUDGET_EXCEEDED",
                        reason="expected latency exceeds the current budget",
                        operation_mode=spec.operation_mode,
                    )
                )
                continue
            if need.user_burden_budget is not None and spec.user_burden > need.user_burden_budget:
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code="USER_BURDEN_BUDGET_EXCEEDED",
                        reason="expected user burden exceeds the current budget",
                        operation_mode=spec.operation_mode,
                    )
                )
                continue
            decision = self._policy.authorize(spec, context)
            if not decision.allowed:
                exclusions.append(
                    ToolExclusion(
                        tool_id=spec.tool_id,
                        code=decision.code,
                        reason=decision.reason,
                        operation_mode=spec.operation_mode,
                    )
                )
                continue

            information_gain = need.expected_information_gain.get(
                spec.tool_id, spec.expected_information_gain
            )
            progress = need.expected_progress.get(spec.tool_id, spec.expected_progress)
            latency_cost = spec.expected_latency_ms / max(need.latency_budget_ms or 1000.0, 1.0)
            risk_cost = _RISK_ORDER[spec.risk_level] / max(_RISK_ORDER[RiskLevel.CRITICAL], 1)
            score = (
                need.information_weight * information_gain
                + need.progress_weight * progress
                - need.latency_weight * latency_cost
                - need.risk_weight * risk_cost
                - need.burden_weight * spec.user_burden
            )
            ranked.append(
                ToolSelection(
                    tool_id=spec.tool_id,
                    score=round(score, 6),
                    reason=(
                        f"information_gain={information_gain:.3f}; progress={progress:.3f}; "
                        f"latency_ms={spec.expected_latency_ms:.1f}; "
                        f"risk={spec.risk_level.value}; burden={spec.user_burden:.3f}"
                    ),
                )
            )
        return ToolSelectionAssessment(
            matching_tool_ids=[spec.tool_id for spec in matching],
            ranked=sorted(ranked, key=lambda item: (-item.score, item.tool_id)),
            exclusions=exclusions,
        )

    def rank(self, need: ToolNeed, context: ToolContext) -> list[ToolSelection]:
        return self.assess(need, context).ranked

    def select(self, need: ToolNeed, context: ToolContext) -> ToolSelection | None:
        ranked = self.assess(need, context).ranked
        return ranked[0] if ranked else None
