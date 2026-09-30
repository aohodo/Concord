"""Public M6 contracts for adaptive human collaboration.

These models describe current interaction needs, not permanent user personas.
They intentionally contain no hidden reasoning or inferred demographic labels.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ExpressionDepth(str, Enum):
    MINIMAL = "minimal"
    GUIDED = "guided"
    EXPERT = "expert"


class InitiativeMode(str, Enum):
    AGENT_LED = "agent_led"
    SHARED = "shared"
    STEP_BY_STEP = "step_by_step"


class ProgressCadence(str, Enum):
    BLOCKERS_ONLY = "blockers_only"
    MILESTONES = "milestones"
    EVERY_STEP = "every_step"


class HumanEffortBudget(BaseModel):
    """Per-turn interaction envelope derived from the current Case state."""

    max_questions_per_turn: int = Field(default=1, ge=0, le=3)
    max_user_actions_per_turn: int = Field(default=1, ge=0, le=3)
    max_primary_response_chars: int = Field(default=700, ge=120, le=4000)
    max_silent_wait_seconds: float = Field(default=8.0, ge=1.0, le=120.0)
    allow_nonblocking_questions: bool = True


class HumanEffortUsage(BaseModel):
    questions_asked: int = Field(default=0, ge=0)
    user_actions_requested: int = Field(default=0, ge=0)
    primary_response_chars: int = Field(default=0, ge=0)
    deferred_detail_chars: int = Field(default=0, ge=0)
    visible_progress_updates: int = Field(default=0, ge=0)
    goal_corrections: int = Field(default=0, ge=0)


class InteractionPreferences(BaseModel):
    """Explicit, Case-local user overrides; every field is optional."""

    expression_depth: ExpressionDepth | None = None
    initiative_mode: InitiativeMode | None = None
    progress_cadence: ProgressCadence | None = None
    result_first: bool | None = None


class HumanCollaborationState(BaseModel):
    expression_depth: ExpressionDepth = ExpressionDepth.GUIDED
    result_first: bool = False
    initiative_mode: InitiativeMode = InitiativeMode.SHARED
    progress_cadence: ProgressCadence = ProgressCadence.MILESTONES
    budget: HumanEffortBudget = Field(default_factory=HumanEffortBudget)
    usage: HumanEffortUsage = Field(default_factory=HumanEffortUsage)
    preference_overrides: InteractionPreferences = Field(default_factory=InteractionPreferences)
    evidence_basis: list[str] = Field(default_factory=list)
    provisional: bool = True
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class LayeredResponse(BaseModel):
    """A short default answer plus lossless optional detail for the UI."""

    primary_message: str
    details: str = ""
    expression_depth: ExpressionDepth = ExpressionDepth.GUIDED
    progress_message: str = ""
    next_action: str = ""


class EvidenceArtifact(BaseModel):
    """User-supplied multimodal evidence with explicit epistemic provenance."""

    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    name: str = ""
    media_type: str = "text/plain"
    source: str = "user"
    extraction_method: str = "direct_text"
    observations: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    epistemic_status: str = "user_reported_unverified"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


__all__ = [
    "EvidenceArtifact",
    "ExpressionDepth",
    "HumanCollaborationState",
    "HumanEffortBudget",
    "HumanEffortUsage",
    "InitiativeMode",
    "InteractionPreferences",
    "LayeredResponse",
    "ProgressCadence",
]
