"""HTTP boundary schemas kept separate from Concord's orchestration entrypoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.case_orchestration import CaseBudgetLimits
from core.human_collaboration import InteractionPreferences


class ChatImageInput(BaseModel):
    url: str


class ChatAttachmentInput(BaseModel):
    kind: str = "file"
    name: str = ""
    media_type: str = "text/plain"
    content: str = ""
    transcript: str = ""
    url: str = ""
    size: int = Field(default=0, ge=0, le=25_000_000)


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    conv_id: str | None = None
    case_id: str | None = None
    evaluation_variant: str = "v2_full"
    evaluation_context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = "local"
    images: list[ChatImageInput] = Field(default_factory=list, max_length=4)
    attachments: list[ChatAttachmentInput] = Field(default_factory=list, max_length=8)
    confirmation_granted: bool = False


class ChatResponse(BaseModel):
    conv_id: str
    case_id: str = ""
    case_phase: str = ""
    case_revision: int = 0
    request_generation: int = 0
    case_progress_url: str = ""
    request_id: str = ""
    trace_id: str = ""
    response: str
    response_details: str = ""
    latency_ms: float
    stage_latency_ms: dict[str, float] = Field(default_factory=dict)
    case_status: str = ""
    case_ready: bool = False
    evidence_sufficiency: str = ""
    clarification_triggered: bool = False
    interaction_action: str = ""
    supporting_actions: list[str] = Field(default_factory=list)
    interaction_needs: list[str] = Field(default_factory=list)
    addressed_need: str = ""
    target_evidence: str = ""
    contract_compliant: bool = True
    contract_violations: list[str] = Field(default_factory=list)
    interaction_progress: str = ""
    evaluation_variant: str = "v2_full"
    problem_state: dict[str, Any] = Field(default_factory=dict)
    resolution_context: dict[str, Any] | None = None
    m1_model_calls: int = 0
    behavior_planning_mode: str = ""
    control_priors: dict[str, Any] = Field(default_factory=dict)
    resolution_status: str = ""
    resolution_thread_id: str = ""
    m2_model_calls: int = 0
    progress_events: list[dict[str, Any]] = Field(default_factory=list)
    collaboration_handoff: dict[str, Any] | None = None
    m3_status: str = ""
    collaboration_id: str = ""
    m3_agents: list[str] = Field(default_factory=list)
    m3_rounds: int = 0
    m3_thread_id: str = ""
    collaboration_result: dict[str, Any] | None = None
    human_collaboration: dict[str, Any] = Field(default_factory=dict)
    evidence_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    visible_progress: list[dict[str, Any]] = Field(default_factory=list)
    experience_candidates: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_experience_ids: list[str] = Field(default_factory=list)
    considered_experience_ids: list[str] = Field(default_factory=list)
    adopted_experience_ids: list[str] = Field(default_factory=list)
    verified_experience_ids: list[str] = Field(default_factory=list)
    experience_source_ids: list[str] = Field(default_factory=list)
    outcome_verdict: str = ""
    improvement_signal_count: int = 0
    improvement_proposal_count: int = 0


class ToolTraceResponse(BaseModel):
    request_id: str
    found: bool
    trace: dict[str, Any] = Field(default_factory=dict)


class RecentToolTracesResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class SimulationCaseInput(BaseModel):
    case_id: str | None = None
    visible_state: dict[str, Any] = Field(default_factory=dict)
    hidden_state: dict[str, Any] = Field(default_factory=dict)
    action_rules: list[dict[str, Any]] = Field(default_factory=list)
    observation_rules: list[dict[str, Any]] = Field(default_factory=list)
    replace: bool = False


class CollaborationCancelInput(BaseModel):
    thread_id: str


class RuntimeToolInput(BaseModel):
    tool_id: str
    case_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    permissions: list[str] = Field(default_factory=list)
    confirmation_granted: bool = False


class RuntimeFaultInput(BaseModel):
    case_id: str
    tool_id: str
    kind: str
    phase: str = "before"
    message: str | None = None
    retryable: bool = True


class ResolutionRunInput(BaseModel):
    resolution_context: dict[str, Any]
    thread_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    confirmation_granted: bool = False
    episode_decision_budget: int = Field(default=6, ge=1, le=12)
    additional_evidence: list[dict[str, Any]] = Field(default_factory=list)
    user_state_update: dict[str, Any] = Field(default_factory=dict)
    max_cycles: int | None = Field(default=None, ge=1, le=12)
    actor_id: str = "anonymous"
    tenant_id: str = "local"


class ResolutionEventInput(BaseModel):
    thread_id: str
    case_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None
    actor_id: str = "anonymous"
    tenant_id: str = "local"


class ResolutionCancelInput(BaseModel):
    thread_id: str
    case_id: str
    event_id: str | None = None
    actor_id: str = "anonymous"
    tenant_id: str = "local"


class CollaborationRunInput(BaseModel):
    handoff: dict[str, Any]
    thread_id: str | None = None
    max_rounds: int = Field(default=2, ge=1, le=3)
    max_agents: int = Field(default=4, ge=1, le=6)
    minimum_gain_margin: float = Field(default=0.05, ge=0.0, le=1.0)


class CollaborationFeedbackInput(BaseModel):
    outcomes: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class CaseControlInput(BaseModel):
    action: str
    reason: str = ""
    user_id: str = "anonymous"
    tenant_id: str = "local"


class CaseBudgetInput(BaseModel):
    limits: CaseBudgetLimits
    user_id: str = "anonymous"
    tenant_id: str = "local"


class CasePreferencesInput(BaseModel):
    preferences: InteractionPreferences
    user_id: str = "anonymous"
    tenant_id: str = "local"


class ImprovementEvaluationInput(BaseModel):
    tenant_id: str = "local"
    evaluator: str
    expected_version: int = Field(ge=1)
    result: dict[str, Any]


class ImprovementDecisionInput(BaseModel):
    tenant_id: str = "local"
    actor_id: str
    decision: str
    reason: str
    expected_version: int = Field(ge=1)


class DocInput(BaseModel):
    title: str
    content: str


class BatchDocInput(BaseModel):
    documents: list[DocInput]


class EvalIntentInput(BaseModel):
    message: str
    expected_intent: str
    context: dict[str, Any] | None = None


class EvalDialogInput(BaseModel):
    question: str | None = None
    turns: list[str] | None = None
    user_id: str | None = None
    conv_id: str | None = None


class EvalRunInput(BaseModel):
    intent_cases: list[EvalIntentInput] | None = None
    dialog_cases: list[EvalDialogInput] | None = None


__all__ = [name for name in globals() if name.endswith(("Input", "Request", "Response"))]
