"""Contracts for M3 adaptive multi-agent collaboration."""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from core.adaptive_resolution.models import CollaborationMode


class CollaborationStatus(str, Enum):
    RUNNING = "running"
    READY_FOR_M2 = "ready_for_m2"
    EVIDENCE_REQUIRED = "evidence_required"
    GOAL_ALIGNMENT_REQUIRED = "goal_alignment_required"
    HUMAN_REQUIRED = "human_required"
    NO_BENEFIT = "no_benefit"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    ERROR = "error"


class CollaborationTopology(str, Enum):
    M2_ONLY = "m2_only"
    FIXED_THREE = "fixed_three"
    ADAPTIVE = "adaptive"


class CollaborationDecision(str, Enum):
    RECRUIT = "recruit"
    RETURN_TO_M2 = "return_to_m2"
    REQUIRE_HUMAN = "require_human"


class ConflictType(str, Enum):
    FACT = "fact"
    HYPOTHESIS = "hypothesis"
    ACTION = "action"
    GOAL = "goal"


class ContextPolicy(str, Enum):
    FULL_HANDOFF = "full_handoff"
    FACTS_ONLY = "facts_only"
    TARGETED = "targeted"


class ContributionKind(str, Enum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    ACTION_PROPOSAL = "action_proposal"
    EVIDENCE_REQUEST = "evidence_request"
    CONSTRAINT = "constraint"


class AgentProfile(BaseModel):
    agent_id: str
    role: str
    mission: str
    capabilities: set[str] = Field(default_factory=set)
    perspective: str
    base_coordination_cost: float = Field(default=0.1, ge=0.0, le=1.0)
    success_prior: float = Field(default=0.5, ge=0.0, le=1.0)


class AgentPerformance(BaseModel):
    agent_id: str
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    samples: int = Field(default=0, ge=0)
    average_latency_ms: float = Field(default=0.0, ge=0.0)
    verified_progress_total: float = 0.0

    @property
    def reliability(self) -> float:
        """Smoothed outcome reliability; self-reported confidence is excluded."""

        return (self.successes + 1.0) / (self.successes + self.failures + 2.0)


class AvailableAgent(BaseModel):
    profile: AgentProfile
    performance: AgentPerformance

    @property
    def reliability(self) -> float:
        return self.performance.reliability


class CollaborationAssignment(BaseModel):
    assignment_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    objective: str
    required_capabilities: set[str] = Field(default_factory=set)
    expected_output: list[str] = Field(default_factory=list)
    context_policy: ContextPolicy = ContextPolicy.TARGETED
    independent: bool = False
    focus_evidence: list[str] = Field(default_factory=list)


class CollaborationPlan(BaseModel):
    decision: CollaborationDecision
    mode: CollaborationMode
    rationale: str
    assignments: list[CollaborationAssignment] = Field(default_factory=list)
    expected_information_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_coordination_cost: float = Field(default=0.0, ge=0.0, le=1.0)
    stop_conditions: list[str] = Field(default_factory=list)

    @field_validator("assignments")
    @classmethod
    def cap_single_round(cls, value: list[CollaborationAssignment]):
        if len(value) > 4:
            raise ValueError("a collaboration round may recruit at most four agents")
        return value


class ContributionItem(BaseModel):
    kind: ContributionKind
    statement: str
    basis_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk: str | None = None
    expected_time: str | None = None
    reversibility: str | None = None
    information_value: str | None = None


class AgentContribution(BaseModel):
    assignment_id: str
    agent_id: str
    items: list[ContributionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    novel_information: list[str] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0, ge=0.0)
    error: str | None = None


class CollaborationConflict(BaseModel):
    conflict_type: ConflictType
    statements: list[str]
    basis_refs: list[str] = Field(default_factory=list)
    resolution_strategy: str = ""
    distinguishing_evidence: list[str] = Field(default_factory=list)
    blocks_action: bool = False


class RoundDecision(str, Enum):
    COMPLETE = "complete"
    EXPAND = "expand"
    NEED_EVIDENCE = "need_evidence"
    REQUIRE_HUMAN = "require_human"


class CollaborationSynthesis(BaseModel):
    round_decision: RoundDecision
    summary: str
    preserved_hypotheses: list[str] = Field(default_factory=list)
    recommended_actions: list[ContributionItem] = Field(default_factory=list)
    evidence_requests: list[str] = Field(default_factory=list)
    conflicts: list[CollaborationConflict] = Field(default_factory=list)
    new_information_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    residual_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    additional_assignments: list[CollaborationAssignment] = Field(default_factory=list)
    shared_source_warnings: list[str] = Field(default_factory=list)


class M2ResumePacket(BaseModel):
    source: str = "m3_adaptive_collaboration"
    collaboration_id: str
    summary: str
    contributions: list[AgentContribution] = Field(default_factory=list)
    preserved_hypotheses: list[str] = Field(default_factory=list)
    recommended_actions: list[ContributionItem] = Field(default_factory=list)
    evidence_requests: list[str] = Field(default_factory=list)
    conflicts: list[CollaborationConflict] = Field(default_factory=list)
    epistemic_status: str = "advisory_until_m2_verifies"
    source_case_revision: int = Field(default=0, ge=0)


class CollaborationRuntimeMetrics(BaseModel):
    coordinator_calls: int = Field(default=0, ge=0)
    worker_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tokens_available: bool = False
    wall_time_ms: float = Field(default=0.0, ge=0.0)
    worker_latency_ms: float = Field(default=0.0, ge=0.0)
    worker_failures: int = Field(default=0, ge=0)
    unique_claims: int = Field(default=0, ge=0)
    duplicate_claims: int = Field(default=0, ge=0)
    evidenced_claims: int = Field(default=0, ge=0)
    shared_source_groups: int = Field(default=0, ge=0)
    novelty_proxy: float = Field(default=0.0, ge=0.0, le=1.0)
    measured_coordination_cost: float = Field(default=0.0, ge=0.0, le=1.0)


class CollaborationProgressEvent(BaseModel):
    stage: str
    message: str
    status: str
    round_index: int = 0
    agents: list[str] = Field(default_factory=list)


class CollaborationResult(BaseModel):
    collaboration_id: str
    case_id: str
    m2_thread_id: str
    thread_id: str
    status: CollaborationStatus
    mode: CollaborationMode
    response: str
    rounds: int = 0
    agents_recruited: list[str] = Field(default_factory=list)
    plans: list[CollaborationPlan] = Field(default_factory=list)
    contributions: list[AgentContribution] = Field(default_factory=list)
    synthesis: CollaborationSynthesis | None = None
    resume_packet: M2ResumePacket | None = None
    progress_events: list[CollaborationProgressEvent] = Field(default_factory=list)
    coordination_cost: float = 0.0
    topology: CollaborationTopology = CollaborationTopology.ADAPTIVE
    source_case_revision: int = Field(default=0, ge=0)
    metrics: CollaborationRuntimeMetrics = Field(default_factory=CollaborationRuntimeMetrics)
    error: str | None = None


class AgentOutcomeFeedback(BaseModel):
    agent_id: str
    successful: bool
    latency_ms: float = Field(default=0.0, ge=0.0)
    collaboration_id: str = ""
    assignment_id: str = ""
    capabilities: set[str] = Field(default_factory=set)
    task_type: str = "general"
    risk_level: str = "unknown"
    verified_claims: int = Field(default=0, ge=0)
    disproven_claims: int = Field(default=0, ge=0)
    progress_delta: float = Field(default=0.0, ge=-1.0, le=1.0)
    source: str = "m2_verified_tool_outcome"


__all__ = [
    "AgentContribution",
    "AgentOutcomeFeedback",
    "AgentPerformance",
    "AgentProfile",
    "AvailableAgent",
    "CollaborationAssignment",
    "CollaborationConflict",
    "CollaborationDecision",
    "CollaborationPlan",
    "CollaborationProgressEvent",
    "CollaborationResult",
    "CollaborationRuntimeMetrics",
    "CollaborationStatus",
    "CollaborationSynthesis",
    "CollaborationTopology",
    "ConflictType",
    "ContextPolicy",
    "ContributionItem",
    "ContributionKind",
    "M2ResumePacket",
    "RoundDecision",
]
