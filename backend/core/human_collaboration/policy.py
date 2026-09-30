"""Resource-rational M6 policy compiled from semantic Case state."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .models import (
    ExpressionDepth,
    HumanCollaborationState,
    HumanEffortBudget,
    InitiativeMode,
    InteractionPreferences,
    LayeredResponse,
    ProgressCadence,
)


def derive_human_collaboration_state(
    resolution_context: dict[str, Any] | None,
    *,
    overrides: InteractionPreferences | None = None,
) -> HumanCollaborationState:
    """Derive behavior from structured semantics, never raw-word matching."""

    context = resolution_context or {}
    user_state = dict(context.get("user_state") or {})
    policy = dict(context.get("provisional_resolution_policy") or {})
    explicit = overrides or InteractionPreferences()
    basis: list[str] = []
    result_first = (
        explicit.result_first
        if explicit.result_first is not None
        else bool(
            user_state.get("delivery_preference") == "result_first"
            or policy.get("delivery_mode") == "result_first"
        )
    )

    depth = explicit.expression_depth
    if depth is not None:
        basis.append("explicit_expression_preference")
    elif explicit.result_first is True:
        depth = ExpressionDepth.MINIMAL
        basis.append("explicit_result_first_preference")
    elif (
        user_state.get("deadline")
        or user_state.get("patience") == "low"
        or user_state.get("delivery_preference") == "result_first"
        or user_state.get("explanation_preference") == "minimal"
    ):
        depth = ExpressionDepth.MINIMAL
        basis.append("current_low_patience_or_result_first_state")
    elif (
        user_state.get("domain_knowledge") == "high"
        or user_state.get("explanation_preference") == "detailed"
    ):
        depth = ExpressionDepth.EXPERT
        basis.append("current_expert_or_detailed_state")
    else:
        depth = ExpressionDepth.GUIDED
        basis.append("balanced_default")

    initiative = explicit.initiative_mode
    if initiative is not None:
        basis.append("explicit_initiative_preference")
    else:
        collaboration = str(
            user_state.get("collaboration_preference")
            or policy.get("collaboration_mode")
            or "shared"
        )
        initiative = {
            "agent_led": InitiativeMode.AGENT_LED,
            "step_by_step": InitiativeMode.STEP_BY_STEP,
            "shared": InitiativeMode.SHARED,
        }.get(collaboration, InitiativeMode.SHARED)

    cadence = explicit.progress_cadence
    if cadence is not None:
        basis.append("explicit_progress_preference")
    else:
        progress = str(
            user_state.get("progress_preference") or policy.get("progress_mode") or "milestones"
        )
        cadence = {
            "blockers_only": ProgressCadence.BLOCKERS_ONLY,
            "every_step": ProgressCadence.EVERY_STEP,
            "milestones": ProgressCadence.MILESTONES,
        }.get(progress, ProgressCadence.MILESTONES)

    if depth is ExpressionDepth.MINIMAL:
        budget = HumanEffortBudget(
            max_questions_per_turn=1,
            max_user_actions_per_turn=1,
            max_primary_response_chars=360,
            max_silent_wait_seconds=5,
            allow_nonblocking_questions=False,
        )
    elif depth is ExpressionDepth.EXPERT:
        budget = HumanEffortBudget(
            max_questions_per_turn=2,
            max_user_actions_per_turn=2,
            max_primary_response_chars=1800,
            max_silent_wait_seconds=12,
            allow_nonblocking_questions=True,
        )
    else:
        budget = HumanEffortBudget(
            max_questions_per_turn=1,
            max_user_actions_per_turn=1,
            max_primary_response_chars=800,
            max_silent_wait_seconds=8,
            allow_nonblocking_questions=True,
        )

    # M1's evidence-backed contract is the semantic authority. M6 compiles it
    # into a presentation/execution budget and may only tighten that envelope.
    if policy:
        budget.max_questions_per_turn = min(
            budget.max_questions_per_turn,
            max(0, int(policy.get("max_questions_per_turn", 1))),
        )
        budget.max_user_actions_per_turn = min(
            budget.max_user_actions_per_turn,
            max(0, int(policy.get("max_user_actions_per_turn", 1))),
        )
        budget.allow_nonblocking_questions = bool(
            budget.allow_nonblocking_questions
            and policy.get("allow_nonblocking_questions", True)
        )

    return HumanCollaborationState(
        expression_depth=depth,
        result_first=result_first,
        initiative_mode=initiative,
        progress_cadence=cadence,
        budget=budget,
        preference_overrides=explicit,
        evidence_basis=basis,
        provisional=not bool(
            explicit.expression_depth
            or explicit.initiative_mode
            or explicit.progress_cadence
            or explicit.result_first is not None
        ),
        updated_at=datetime.now(UTC).isoformat(),
    )


def select_visible_progress(
    events: list[dict[str, Any]], state: HumanCollaborationState
) -> list[dict[str, Any]]:
    """Apply the structured progress contract without inspecting message text."""

    if state.progress_cadence is ProgressCadence.EVERY_STEP:
        return list(events)
    blocker_statuses = {
        "blocked",
        "denied",
        "failed",
        "error",
        "waiting",
        "human_required",
        "timed_out",
    }
    if state.progress_cadence is ProgressCadence.BLOCKERS_ONLY:
        return [item for item in events if str(item.get("status")) in blocker_statuses]
    milestone_statuses = blocker_statuses | {"completed", "succeeded", "resolved"}
    milestone_stages = {"evidence_update", "verification", "collaboration", "resolved"}
    return [
        item
        for item in events
        if str(item.get("status")) in milestone_statuses
        or str(item.get("stage")) in milestone_stages
    ]


def layer_response(
    response: str,
    state: HumanCollaborationState,
    *,
    progress_message: str = "",
    next_action: str = "",
) -> LayeredResponse:
    """Bound default reading cost without discarding information."""

    text = (response or "").strip()
    limit = state.budget.max_primary_response_chars
    if not text or len(text) <= limit or state.expression_depth is ExpressionDepth.EXPERT:
        return LayeredResponse(
            primary_message=text,
            expression_depth=state.expression_depth,
            progress_message=progress_message,
            next_action=next_action,
        )

    # Prefer a complete discourse unit over a raw character slice. The budget
    # is a reading-cost target; it must not corrupt a long identifier, error
    # message or sentence just to satisfy an exact count.
    boundaries = [match.end() for match in re.finditer(r"(?:[。！？；;.]|\n+)", text)]
    within_budget = [position for position in boundaries if position <= limit]
    cut = max(within_budget) if within_budget else (boundaries[0] if boundaries else len(text))
    return LayeredResponse(
        # Keep the split lossless. Whitespace at the boundary is part of the
        # model response and the Console may reconstruct the full answer.
        primary_message=text[:cut],
        details=text[cut:],
        expression_depth=state.expression_depth,
        progress_message=progress_message,
        next_action=next_action,
    )


__all__ = ["derive_human_collaboration_state", "layer_response", "select_visible_progress"]
