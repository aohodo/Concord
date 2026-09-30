"""Deterministic policy boundary for tool authorization and side effects."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import OperationMode, RiskLevel, SideEffectScope, ToolContext, ToolSpec


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str
    code: str = "ALLOWED"


class ToolPolicy:
    """Enforce non-negotiable boundaries before a model-selected tool executes."""

    def authorize(self, spec: ToolSpec, context: ToolContext) -> ToolPolicyDecision:
        missing = sorted(spec.required_permissions - context.permissions)
        if missing:
            return ToolPolicyDecision(
                False,
                f"missing permissions: {', '.join(missing)}",
                "PERMISSION_DENIED",
            )
        if spec.operation_mode is OperationMode.PRODUCTION_WRITE:
            if not context.allow_external_writes:
                return ToolPolicyDecision(
                    False,
                    "external writes are disabled for this execution context",
                    "EXTERNAL_WRITE_DISABLED",
                )
            if not context.confirmation_granted:
                return ToolPolicyDecision(
                    False,
                    "production writes require explicit confirmation",
                    "CONFIRMATION_REQUIRED",
                )
        if spec.side_effect_scope is SideEffectScope.EXTERNAL and not context.allow_external_writes:
            return ToolPolicyDecision(
                False,
                "external side effects are disabled",
                "EXTERNAL_SIDE_EFFECT_DISABLED",
            )
        if (
            spec.confirmation_required
            or spec.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        ) and not context.confirmation_granted:
            return ToolPolicyDecision(
                False,
                "high-risk tool requires explicit confirmation",
                "CONFIRMATION_REQUIRED",
            )
        return ToolPolicyDecision(True, "tool contract and execution context allow invocation")
