"""M7 contracts for outcome-grounded, non-parametric continual improvement."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class OutcomeVerdict(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    VERIFIED_BOUNDARY = "verified_boundary"
    UNVERIFIED = "unverified"


class OutcomeAuthority(str, Enum):
    INDEPENDENT_TOOL = "independent_tool"
    ENVIRONMENT = "environment"
    RUNTIME_BOUNDARY = "runtime_boundary"
    USER_REPORT = "user_report"
    MODEL_SELF_ASSESSMENT = "model_self_assessment"
    NONE = "none"


class ExperienceKind(str, Enum):
    SUCCESSFUL_PROCEDURE = "successful_procedure"
    FAILURE_AVOIDANCE = "failure_avoidance"
    BOUNDARY_HANDOFF = "boundary_handoff"


class ExperienceStatus(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class FailureCategory(str, Enum):
    REPEATED_FAILED_ACTION = "repeated_failed_action"
    KNOWLEDGE_GAP = "knowledge_gap"
    TOOL_GAP = "tool_gap"
    PERMISSION_BOUNDARY = "permission_boundary"
    VERIFICATION_GAP = "verification_gap"
    NO_PROGRESS = "no_progress"
    COLLABORATION_NEGATIVE_GAIN = "collaboration_negative_gain"


class ImprovementKind(str, Enum):
    KNOWLEDGE = "knowledge"
    SKILL = "skill"
    TOOL = "tool"
    POLICY = "policy"
    DATASET = "dataset"
    EVALUATION = "evaluation"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    APPROVED = "approved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class OutcomeVerification(BaseModel):
    outcome_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    tenant_id: str = "local"
    case_revision: int = Field(default=0, ge=0)
    trace_id: str = ""
    verdict: OutcomeVerdict
    authority: OutcomeAuthority
    eligible_for_experience: bool = False
    verification_evidence: list[dict[str, Any]] = Field(default_factory=list)
    failure_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rejected_reasons: list[str] = Field(default_factory=list)
    verified_at: str = Field(default_factory=utc_now)


class ExperienceApplicability(BaseModel):
    """Structured state signature; raw phrasing is deliberately excluded."""

    domain: str = "other"
    goal_kind: str = "unknown"
    situation_keys: set[str] = Field(default_factory=set)
    evidence_keys: set[str] = Field(default_factory=set)
    operation_modes: set[str] = Field(default_factory=set)
    required_capabilities: set[str] = Field(default_factory=set)
    permission_requirements: set[str] = Field(default_factory=set)
    tool_contract_versions: dict[str, str] = Field(default_factory=dict)
    state_constraints: dict[str, Any] = Field(default_factory=dict)
    forbidden_conditions: set[str] = Field(default_factory=set)
    capabilities_known: bool = False
    permissions_known: bool = False
    contract_versions_known: bool = False


class ExperienceAction(BaseModel):
    tool_id: str
    category: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str = ""
    actual_observation: str = ""
    status: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class StructuredExperience(BaseModel):
    experience_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "local"
    source_case_id: str
    source_case_revision: int = Field(default=0, ge=0)
    source_trace_id: str = ""
    kind: ExperienceKind
    status: ExperienceStatus = ExperienceStatus.ACTIVE
    applicability: ExperienceApplicability
    strategy: list[ExperienceAction] = Field(default_factory=list)
    failed_actions: list[ExperienceAction] = Field(default_factory=list)
    outcome: OutcomeVerification
    provisional_explanations: list[dict[str, Any]] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    valid_until: str | None = None
    sanitization: dict[str, Any] = Field(default_factory=dict)
    reuse_count: int = Field(default=0, ge=0)
    supported_reuse_count: int = Field(default=0, ge=0)
    contradicted_reuse_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ExperienceMatch(BaseModel):
    experience: StructuredExperience
    score: float = Field(ge=0, le=1)
    why_applicable: list[str] = Field(default_factory=list)
    mismatches: list[str] = Field(default_factory=list)
    must_reverify: bool = True


class ExperienceFeedback(BaseModel):
    experience_id: str
    target_case_id: str
    outcome: str
    verification_evidence: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class FailureSignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "local"
    source_case_id: str
    category: FailureCategory
    signature: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ImprovementProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "local"
    kind: ImprovementKind
    status: ProposalStatus = ProposalStatus.DRAFT
    title: str
    problem_statement: str
    evidence_signal_ids: list[str] = Field(default_factory=list)
    suggested_artifact: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    evaluation_scenarios: list[str] = Field(default_factory=list)
    evaluation_results: list[dict[str, Any]] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    decision_by: str = ""
    decision_reason: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


__all__ = [
    "ExperienceAction",
    "ExperienceApplicability",
    "ExperienceFeedback",
    "ExperienceKind",
    "ExperienceMatch",
    "ExperienceStatus",
    "FailureCategory",
    "FailureSignal",
    "ImprovementKind",
    "ImprovementProposal",
    "OutcomeAuthority",
    "OutcomeVerdict",
    "OutcomeVerification",
    "ProposalStatus",
    "StructuredExperience",
    "utc_now",
]
