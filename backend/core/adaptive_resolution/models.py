"""Explicit contracts for M2 belief, action and progress state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from core.tools import OperationMode, RiskLevel


class ResolutionDecision(str, Enum):
    USE_TOOL = "use_tool"
    ASK_USER = "ask_user"
    RESOLVE = "resolve"
    COLLABORATE = "collaborate"


class CollaborationMode(str, Enum):
    SPECIALIST_HANDOFF = "specialist_handoff"
    PARALLEL_WORKERS = "parallel_workers"
    INDEPENDENT_REVIEW = "independent_review"
    DIVERSE_EXPLORATION = "diverse_exploration"


class ResolutionStatus(str, Enum):
    INVESTIGATING = "investigating"
    WAITING_FOR_USER = "waiting_for_user"
    RESOLVED = "resolved"
    COLLABORATION_REQUIRED = "collaboration_required"
    PAUSED = "paused"
    EXHAUSTED = "exhausted"
    ERROR = "error"
    CANCELLED = "cancelled"


class PredictionAssessment(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    MATCH = "match"
    PARTIAL = "partial"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class DecisionEvent(str, Enum):
    """Events important enough to spend another model decision."""

    INITIAL = "initial"
    BOOTSTRAP_COMPLETE = "bootstrap_complete"
    TOOL_FAILURE = "tool_failure"
    PREDICTION_MISMATCH = "prediction_mismatch"
    VERIFICATION_COMPLETE = "verification_complete"
    STAGE_COMPLETE = "stage_complete"
    GUARDRAIL_REJECTION = "guardrail_rejection"
    USER_EVIDENCE_UPDATED = "user_evidence_updated"
    ENVIRONMENT_UPDATED = "environment_updated"
    EPISODE_BUDGET_REACHED = "episode_budget_reached"
    USER_GOAL_UPDATED = "user_goal_updated"
    USER_CANCELLED = "user_cancelled"
    NO_PROGRESS = "no_progress"
    COLLABORATION_TRIGGERED = "collaboration_triggered"
    COLLABORATION_RESULT = "collaboration_result"


class CaseEventType(str, Enum):
    """External facts that may change an active M2 episode."""

    USER_EVIDENCE = "user_evidence"
    USER_STATE_UPDATE = "user_state_update"
    GOAL_UPDATE = "goal_update"
    ENVIRONMENT_UPDATE = "environment_update"
    CANCEL = "cancel"


class CaseEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = 0
    thread_id: str
    case_id: str
    tenant_id: str = "local"
    actor_id: str = "anonymous"
    type: CaseEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class BeliefStrength(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UserProgressEvent(BaseModel):
    """Grounded progress that a UI may stream without exposing chain-of-thought."""

    cycle: int
    stage: str
    message: str
    status: str
    tool_id: str | None = None


class ProvisionalExplanation(BaseModel):
    statement: str
    strength: BeliefStrength = BeliefStrength.LOW
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    distinguishing_evidence: list[str] = Field(default_factory=list)


class CoordinationStructure(BaseModel):
    """Explicit work dependencies; absent knowledge must not be guessed from words."""

    parallelism_known: bool = False
    independent_workstreams: list[str] = Field(default_factory=list)
    serial_dependencies: list[str] = Field(default_factory=list)
    blocking_evidence: list[str] = Field(default_factory=list)
    human_authority_required: bool = False


class CollaborationHandoff(BaseModel):
    """M2's uncertainty-preserving output for conditional M3 collaboration."""

    case_id: str
    thread_id: str
    case_revision: int = Field(default=0, ge=0)
    reason: str
    goal: dict[str, Any] = Field(default_factory=dict)
    action_constraints: dict[str, Any] = Field(default_factory=dict)
    reported_evidence: list[dict[str, Any]] = Field(default_factory=list)
    observed_evidence: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    competing_explanations: list[ProvisionalExplanation] = Field(default_factory=list)
    failed_directions: list[dict[str, Any]] = Field(default_factory=list)
    prohibited_retries: list[str] = Field(default_factory=list)
    open_evidence: list[dict[str, Any]] = Field(default_factory=list)
    permission_boundary: list[str] = Field(default_factory=list)
    coordination_structure: CoordinationStructure = Field(
        default_factory=CoordinationStructure
    )
    suggested_collaboration_mode: CollaborationMode = CollaborationMode.SPECIALIST_HANDOFF


class CriterionVerification(BaseModel):
    """Runtime-checkable link from one user success criterion to tool evidence."""

    criterion: str
    evidence_refs: list[str] = Field(default_factory=list, min_length=1)


class ResolutionPlan(BaseModel):
    """One semantic control decision; the runtime still owns authorization."""

    decision: ResolutionDecision
    rationale: str
    provisional_explanations: list[ProvisionalExplanation] = Field(default_factory=list)
    previous_observation_assessment: PredictionAssessment = (
        PredictionAssessment.NOT_APPLICABLE
    )
    required_capabilities: set[str] = Field(default_factory=set)
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    allowed_modes: set[OperationMode] = Field(
        default_factory=lambda: {OperationMode.READ_ONLY, OperationMode.SIMULATED_WRITE}
    )
    max_risk: RiskLevel = RiskLevel.MEDIUM
    expected_observation: str | None = None
    response: str | None = None
    user_questions: list[str] = Field(default_factory=list, max_length=3)
    requested_user_actions: list[str] = Field(default_factory=list, max_length=3)
    collaboration_reason: str | None = None
    suggested_collaboration_mode: CollaborationMode = CollaborationMode.SPECIALIST_HANDOFF
    coordination_structure: CoordinationStructure = Field(
        default_factory=CoordinationStructure
    )
    verification_complete: bool = False
    resolution_evidence: list[str] = Field(default_factory=list)
    criterion_verifications: list[CriterionVerification] = Field(default_factory=list)
    collaboration_source_ids: list[str] = Field(default_factory=list)
    considered_experience_ids: list[str] = Field(default_factory=list)
    adopted_experience_ids: list[str] = Field(default_factory=list)
    # Compatibility input for M7 runs produced before lifecycle attribution existed.
    experience_source_ids: list[str] = Field(default_factory=list)

    @field_validator("suggested_collaboration_mode", mode="before")
    @classmethod
    def normalize_null_collaboration_mode(cls, value: Any) -> Any:
        return value or CollaborationMode.SPECIALIST_HANDOFF


class ResolutionResult(BaseModel):
    case_id: str
    thread_id: str
    trace_id: str
    status: ResolutionStatus
    response: str
    cycles: int
    episode_decisions: int = 0
    final_decision: ResolutionDecision | None = None
    user_questions: list[str] = Field(default_factory=list)
    requested_user_actions: list[str] = Field(default_factory=list)
    provisional_explanations: list[ProvisionalExplanation] = Field(default_factory=list)
    tool_history: list[dict[str, Any]] = Field(default_factory=list)
    belief_history: list[dict[str, Any]] = Field(default_factory=list)
    progress_history: list[dict[str, Any]] = Field(default_factory=list)
    collaboration_reason: str | None = None
    guardrail_events: list[dict[str, Any]] = Field(default_factory=list)
    decision_events: list[dict[str, Any]] = Field(default_factory=list)
    control_events: list[dict[str, Any]] = Field(default_factory=list)
    progress_events: list[UserProgressEvent] = Field(default_factory=list)
    verification_complete: bool = False
    resolution_evidence: list[str] = Field(default_factory=list)
    criterion_verifications: list[CriterionVerification] = Field(default_factory=list)
    retrieved_experience_ids: list[str] = Field(default_factory=list)
    considered_experience_ids: list[str] = Field(default_factory=list)
    adopted_experience_ids: list[str] = Field(default_factory=list)
    verified_experience_ids: list[str] = Field(default_factory=list)
    experience_source_ids: list[str] = Field(default_factory=list)
    collaboration_handoff: CollaborationHandoff | None = None
    error: str | None = None
