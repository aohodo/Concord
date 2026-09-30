"""Public, chain-of-thought-free contracts for one Concord Case run."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from core.human_collaboration import EvidenceArtifact, HumanCollaborationState


class CasePhase(str, Enum):
    FORMULATION = "formulation"
    RESOLUTION = "resolution"
    COLLABORATION = "collaboration"
    VERIFICATION = "verification"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_EXTERNAL = "waiting_for_external"
    RESOLVED = "resolved"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    HUMAN_REQUIRED = "human_required"
    TIMED_OUT = "timed_out"
    ERROR = "error"


class CaseJobKind(str, Enum):
    COLLABORATE_THEN_RESUME = "collaborate_then_resume"


class CaseJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class CaseBudgetLimits(BaseModel):
    """Case-local resource envelope; limits control search, not truth."""

    wall_time_seconds: float = Field(default=86_400.0, gt=0)
    model_calls: int = Field(default=120, ge=1)
    tool_calls: int = Field(default=120, ge=1)
    collaboration_rounds: int = Field(default=8, ge=0)
    user_waits: int = Field(default=20, ge=0)


class CaseBudgetUsage(BaseModel):
    wall_time_seconds: float = Field(default=0.0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    collaboration_rounds: int = Field(default=0, ge=0)
    user_waits: int = Field(default=0, ge=0)


class CaseContextSummary(BaseModel):
    """Compact working memory derived from structured state, never raw CoT."""

    goal: dict[str, Any] = Field(default_factory=dict)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    reported_observations: list[dict[str, Any]] = Field(default_factory=list)
    provisional_explanations: list[dict[str, Any]] = Field(default_factory=list)
    salient_failures: list[dict[str, Any]] = Field(default_factory=list)
    open_evidence: list[dict[str, Any]] = Field(default_factory=list)
    user_state: dict[str, Any] = Field(default_factory=dict)
    action_constraints: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CaseJob(BaseModel):
    """Durable unit of asynchronous Case work with a renewable lease."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    user_id: str
    tenant_id: str = "local"
    kind: CaseJobKind
    status: CaseJobStatus = CaseJobStatus.PENDING
    case_revision: int = Field(default=0, ge=0)
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    available_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    lease_owner: str = ""
    lease_expires_at: str | None = None
    lease_generation: int = Field(default=0, ge=0)
    execution_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    last_error: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CaseLifecycleEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = Field(default=0, ge=0)
    case_id: str
    phase: CasePhase
    event_type: str
    status: str
    trace_id: str = ""
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CaseRunSnapshot(BaseModel):
    """Unified M1→M2→M3→M2 view exposed to clients and evaluators."""

    case_id: str
    conv_id: str
    user_id: str
    tenant_id: str = "local"
    phase: CasePhase = CasePhase.FORMULATION
    status: str = "aligning_goal"
    response: str = ""
    m1_thread_id: str
    m2_thread_id: str
    m3_thread_id: str = ""
    collaboration_id: str = ""
    case_revision: int = Field(default=0, ge=0)
    request_generation: int = Field(default=0, ge=0)
    goal_revision: int = Field(default=0, ge=0)
    trace_ids: list[str] = Field(default_factory=list)
    m1: dict[str, Any] = Field(default_factory=dict)
    m2: dict[str, Any] = Field(default_factory=dict)
    m3: dict[str, Any] = Field(default_factory=dict)
    context_summary: CaseContextSummary = Field(default_factory=CaseContextSummary)
    human_collaboration: HumanCollaborationState = Field(default_factory=HumanCollaborationState)
    evidence_artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    budget_limits: CaseBudgetLimits = Field(default_factory=CaseBudgetLimits)
    budget_usage: CaseBudgetUsage = Field(default_factory=CaseBudgetUsage)
    invalidated_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[CaseLifecycleEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


__all__ = [
    "CaseBudgetLimits",
    "CaseBudgetUsage",
    "CaseContextSummary",
    "CaseJob",
    "CaseJobKind",
    "CaseJobStatus",
    "CaseLifecycleEvent",
    "CasePhase",
    "CaseRunSnapshot",
]
