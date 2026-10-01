"""Explicit state contracts for shared problem formulation.

The models deliberately use coarse enums instead of invented confidence scores.  A
reported observation is not promoted to a verified fact merely because either the
user or the model sounds confident.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from core.control_priors import ControlPriorDecision


class StrEnum(str, Enum):
    """String enum that serializes cleanly through LangGraph checkpointers."""


class CaseStatus(StrEnum):
    ALIGNING_GOAL = "aligning_goal"
    RECONSTRUCTING_SITUATION = "reconstructing_situation"
    ASSESSING_EVIDENCE = "assessing_evidence"
    SEEKING_INFORMATION = "seeking_information"
    CASE_READY = "case_ready"
    META_HANDLED = "meta_handled"
    FORMULATION_ERROR = "formulation_error"


class GoalStatus(StrEnum):
    UNKNOWN = "unknown"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"


class GoalKind(StrEnum):
    UNKNOWN = "unknown"
    OUTCOME = "outcome"
    PROCEDURE_PREFERENCE = "procedure_preference"
    COOPERATION = "cooperation"
    EXPLANATION_REQUEST = "explanation_request"


class CommonGroundStatus(StrEnum):
    """How far a goal has progressed from a user report to mutual grounding."""

    UNILATERAL = "unilateral"
    EXPLICITLY_GROUNDED = "explicitly_grounded"
    REFLECTED = "reflected"
    MUTUALLY_ACKNOWLEDGED = "mutually_acknowledged"
    REPAIRED = "repaired"


class ClaimType(StrEnum):
    OBSERVATION = "observation"
    ACTION = "action"
    HYPOTHESIS = "hypothesis"


class ClaimSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"


class VerificationStatus(StrEnum):
    REPORTED = "reported"
    VERIFIED = "verified"
    INFERRED = "inferred"
    CONTESTED = "contested"


class CoarseLevel(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceSufficiency(StrEnum):
    INSUFFICIENT = "insufficient"
    PARTIALLY_SUFFICIENT = "partially_sufficient"
    SUFFICIENT = "sufficient"


class EvidencePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceNeedStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    UNAVAILABLE = "unavailable"
    DEFERRED = "deferred"


class AcquisitionActor(StrEnum):
    USER = "user"
    LATER_STAGE = "later_stage"
    EITHER = "either"


class ActionOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ActionRelevance(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CORRECTED = "corrected"


class ProgressStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NO_PROGRESS = "no_progress"
    PROGRESSED = "progressed"


class ExperimentVariant(StrEnum):
    FULL = "v2_full"
    NO_USER_STATE = "v2_no_user_state"
    NO_EPISTEMIC_SEPARATION = "v2_no_epistemic_separation"
    NO_FAILURE_MEMORY = "v2_no_failure_memory"
    M2_ONLY = "v2_m2_only"
    FIXED_THREE = "v2_fixed_three"
    GATED_FIXED_THREE = "v2_gated_fixed_three"
    ALWAYS_ON_FIXED_THREE = "v2_always_on_fixed_three"


class ConversationAct(StrEnum):
    CASE_FORMULATION = "case_formulation"
    HUMAN_HANDOFF = "human_handoff"
    STOP = "stop"
    SOCIAL = "social"


class IssueStatus(StrEnum):
    REPORTED = "reported"
    ACTIVE = "active"
    DEFERRED = "deferred"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class IssueRelation(StrEnum):
    INDEPENDENT = "independent"
    SYMPTOM_OF = "symptom_of"
    BLOCKS = "blocks"
    UNKNOWN = "unknown"


class DeliveryPreference(StrEnum):
    RESULT_FIRST = "result_first"
    BALANCED = "balanced"
    PROCESS_VISIBLE = "process_visible"
    UNKNOWN = "unknown"


class ExplanationPreference(StrEnum):
    MINIMAL = "minimal"
    ON_DEMAND = "on_demand"
    DETAILED = "detailed"
    UNKNOWN = "unknown"


class CollaborationPreference(StrEnum):
    AGENT_LED = "agent_led"
    SHARED = "shared"
    STEP_BY_STEP = "step_by_step"
    UNKNOWN = "unknown"


class ProgressPreference(StrEnum):
    BLOCKERS_ONLY = "blockers_only"
    MILESTONES = "milestones"
    EVERY_STEP = "every_step"
    UNKNOWN = "unknown"


class InteractionAction(StrEnum):
    REFLECT = "reflect"
    STRUCTURE = "structure"
    SCAFFOLD = "scaffold"
    CALIBRATE = "calibrate"
    CHALLENGE = "challenge"
    VERIFY = "verify"
    RESTORE_AGENCY = "restore_agency"
    PRIORITIZE = "prioritize"
    REPAIR = "repair"
    EMIT_CASE_READY = "emit_case_ready"
    META_RESPONSE = "meta_response"


class InteractionModifier(StrEnum):
    PLAIN_LANGUAGE = "plain_language"
    CONCISE = "concise"
    ACKNOWLEDGE_FAILURE = "acknowledge_failure"
    NO_BLIND_RETRY = "no_blind_retry"
    PROVIDE_ACQUISITION_HINT = "provide_acquisition_hint"
    DISCLOSE_PROGRESS = "disclose_progress"
    RESTORE_AGENCY = "restore_agency"
    INVITE_CORRECTION = "invite_correction"


class InteractionNeedType(StrEnum):
    GOAL_ALIGNMENT = "goal_alignment"
    SITUATION_RECONSTRUCTION = "situation_reconstruction"
    EPISTEMIC_CALIBRATION = "epistemic_calibration"
    CONTRADICTION_REPAIR = "contradiction_repair"
    EVIDENCE_ACQUISITION = "evidence_acquisition"
    USER_SUPPORT = "user_support"
    URGENCY_PRIORITIZATION = "urgency_prioritization"
    READY_FOR_RESOLUTION = "ready_for_resolution"


class GroundedValue(BaseModel):
    value: str
    evidence_quote: str
    turn_id: int


class GoalRevision(BaseModel):
    previous_outcome: str | None = None
    new_outcome: str
    evidence_quote: str
    turn_id: int


class GoalState(BaseModel):
    explicit_goal: str | None = None
    inferred_goal: str | None = None
    original_outcome: str | None = None
    current_outcome: str | None = None
    success_criteria: list[GroundedValue] = Field(default_factory=list)
    revision_history: list[GoalRevision] = Field(default_factory=list)
    status: GoalStatus = GoalStatus.UNKNOWN
    scope_constraints: list[str] = Field(default_factory=list)
    unresolved_ambiguity: list[str] = Field(default_factory=list)
    common_ground: CommonGroundStatus = CommonGroundStatus.UNILATERAL
    reflected_turn_id: int | None = None
    acknowledged_turn_id: int | None = None
    last_grounded_turn_id: int | None = None


class SituationFrame(BaseModel):
    who: str | None = None
    what: str | None = None
    when: str | None = None
    where: str | None = None
    how: str | None = None
    impact: str | None = None


class ReportedIssue(BaseModel):
    issue_id: str = ""
    summary: str
    evidence_quote: str
    first_seen_turn_id: int
    last_seen_turn_id: int
    status: IssueStatus = IssueStatus.REPORTED
    relation: IssueRelation = IssueRelation.UNKNOWN
    related_issue_id: str | None = None
    priority_basis: str | None = None


class EpistemicClaim(BaseModel):
    content: str
    type: ClaimType
    source: ClaimSource = ClaimSource.USER
    verification_status: VerificationStatus = VerificationStatus.REPORTED
    turn_id: int
    evidence_quote: str
    issue_id: str | None = None
    revised_turn_id: int | None = None


class ActionResult(BaseModel):
    action: str
    target: str | None = None
    conditions: list[str] = Field(default_factory=list)
    expected_result: str | None = None
    reported_result: str | None = None
    outcome: ActionOutcome = ActionOutcome.UNKNOWN
    source: ClaimSource = ClaimSource.USER
    evidence_quote: str = ""
    turn_id: int
    issue_id: str | None = None
    repetitions: int | None = None
    relevance: ActionRelevance = ActionRelevance.ACTIVE
    retry_condition: str | None = None


class CaseUserState(BaseModel):
    """Current, case-local snapshot; never a permanent user/persona label."""

    domain_knowledge: CoarseLevel = CoarseLevel.UNKNOWN
    clarity: CoarseLevel = CoarseLevel.UNKNOWN
    frustration: CoarseLevel = CoarseLevel.UNKNOWN
    effort_willingness: CoarseLevel = CoarseLevel.UNKNOWN
    perceived_controllability: CoarseLevel = CoarseLevel.UNKNOWN
    patience: CoarseLevel = CoarseLevel.UNKNOWN
    deadline: str | None = None
    delivery_preference: DeliveryPreference = DeliveryPreference.UNKNOWN
    explanation_preference: ExplanationPreference = ExplanationPreference.UNKNOWN
    collaboration_preference: CollaborationPreference = CollaborationPreference.UNKNOWN
    progress_preference: ProgressPreference = ProgressPreference.UNKNOWN


class UserStateEvidence(BaseModel):
    field: str
    value: str
    evidence_quote: str
    turn_id: int
    is_current: bool = True
    supersedes_turn_id: int | None = None


class UserStateRevision(BaseModel):
    field: str
    previous_value: str
    new_value: str
    evidence_quote: str
    turn_id: int


class EvidenceNeed(BaseModel):
    key: str
    description: str
    question: str = ""
    priority: EvidencePriority = EvidencePriority.MEDIUM
    user_burden: CoarseLevel = CoarseLevel.LOW
    decision_impact: CoarseLevel = CoarseLevel.MEDIUM
    answerability: CoarseLevel = CoarseLevel.MEDIUM
    answer_cues: list[str] = Field(default_factory=list)
    acquisition_hint: str | None = None
    acquisition_actor: AcquisitionActor = AcquisitionActor.USER
    unavailable_reason: str | None = None
    blocking: bool = True
    status: EvidenceNeedStatus = EvidenceNeedStatus.OPEN
    introduced_turn_id: int = 0
    resolved_turn_id: int | None = None
    resolution_quote: str | None = None
    issue_id: str | None = None
    attempt_count: int = 0
    last_asked_turn_id: int | None = None

    @field_validator("user_burden", "decision_impact", "answerability", mode="before")
    @classmethod
    def clamp_extended_coarse_level(cls, value: Any) -> Any:
        """Clamp provider-side superlatives to this contract's coarsest level."""
        if isinstance(value, str) and value.strip().casefold() == "critical":
            return CoarseLevel.HIGH
        return value

    @field_validator("acquisition_actor", mode="before")
    @classmethod
    def normalize_unspecified_acquisition_actor(cls, value: Any) -> Any:
        """Treat an omitted or blank provider value like the field default."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return AcquisitionActor.USER
        return value


class EvidenceResolution(BaseModel):
    key: str
    evidence_quote: str
    issue_id: str | None = None


class EvidenceDisposition(BaseModel):
    key: str
    evidence_quote: str
    reason: str
    issue_id: str | None = None


class GoalAcknowledgement(BaseModel):
    accepted: bool
    evidence_quote: str
    corrected_goal: str | None = None


class InteractionContract(BaseModel):
    question_budget: int = 1
    max_steps_requested: int = 1
    detail_level: str = "concise"
    terminology: str = "plain"
    response_length: str = "short"
    action_burden: CoarseLevel = CoarseLevel.LOW
    allow_nonblocking_questions: bool = True
    max_clarification_turns: int = 3
    result_first: bool = True
    progress_disclosure: bool = False
    forbidden_actions: list[str] = Field(default_factory=list)


class ResolutionPolicy(BaseModel):
    """Provisional M2 interaction policy derived from evidence-backed M1 state."""

    delivery_mode: DeliveryPreference = DeliveryPreference.RESULT_FIRST
    explanation_mode: ExplanationPreference = ExplanationPreference.ON_DEMAND
    collaboration_mode: CollaborationPreference = CollaborationPreference.AGENT_LED
    progress_mode: ProgressPreference = ProgressPreference.MILESTONES
    max_questions_per_turn: int = Field(default=1, ge=0, le=3)
    max_user_actions_per_turn: int = 1
    allow_nonblocking_questions: bool = True
    confirmation_required_for: list[str] = Field(
        default_factory=lambda: ["high_risk", "irreversible", "scope_change"]
    )
    forbidden_retries: list[str] = Field(default_factory=list)
    deadline: str | None = None
    evidence_turn_ids: list[int] = Field(default_factory=list)
    provisional: bool = True


class ContractCompliance(BaseModel):
    compliant: bool = True
    violations: list[str] = Field(default_factory=list)
    repaired: bool = False


class InteractionNeed(BaseModel):
    type: InteractionNeedType
    reason: str
    priority: EvidencePriority = EvidencePriority.MEDIUM


class PolicyDecision(BaseModel):
    action: InteractionAction
    supporting_actions: list[InteractionAction] = Field(default_factory=list)
    modifiers: list[InteractionModifier] = Field(default_factory=list)
    reason: str
    target_evidence: str | None = None
    question: str | None = None
    response_text: str | None = None
    proposed_actions: list[str] = Field(default_factory=list)
    candidate_actions: list[InteractionAction] = Field(default_factory=list)
    contract: InteractionContract = Field(default_factory=InteractionContract)
    addressed_need: InteractionNeedType | None = None
    compliance: ContractCompliance = Field(default_factory=ContractCompliance)
    planning_mode: str = "compiled"
    control_priors: ControlPriorDecision = Field(default_factory=ControlPriorDecision)


class BehaviorProposal(BaseModel):
    primary_action: InteractionAction
    supporting_actions: list[InteractionAction] = Field(default_factory=list)
    reason: str
    target_evidence: str | None = None
    question: str | None = None
    response_text: str
    proposed_actions: list[str] = Field(default_factory=list)


class FormulationHistoryEntry(BaseModel):
    turn_id: int
    status: CaseStatus
    action: InteractionAction
    missing_evidence: list[str] = Field(default_factory=list)
    supporting_actions: list[InteractionAction] = Field(default_factory=list)
    modifiers: list[InteractionModifier] = Field(default_factory=list)
    response: str | None = None
    question: str | None = None
    target_evidence: str | None = None
    addressed_need: InteractionNeedType | None = None
    evidence_added: list[str] = Field(default_factory=list)
    evidence_resolved: list[str] = Field(default_factory=list)
    claims_revised: list[str] = Field(default_factory=list)
    progress: ProgressStatus = ProgressStatus.NOT_APPLICABLE
    compliance: ContractCompliance = Field(default_factory=ContractCompliance)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ReadinessAssessment(BaseModel):
    ready: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    blocking_evidence: list[str] = Field(default_factory=list)
    carried_forward_evidence: list[str] = Field(default_factory=list)
    unavailable_evidence: list[str] = Field(default_factory=list)
    deferred_evidence: list[str] = Field(default_factory=list)
    residual_uncertainty: list[str] = Field(default_factory=list)


class SharedProblemState(BaseModel):
    case_id: str
    user_id: str
    conv_id: str
    domain_prior: str = "other"
    initial_problem_report: str | None = None
    goal: GoalState = Field(default_factory=GoalState)
    situation: SituationFrame = Field(default_factory=SituationFrame)
    reported_issues: list[ReportedIssue] = Field(default_factory=list)
    active_issue_id: str | None = None
    last_focus_change_turn_id: int | None = None
    claims: list[EpistemicClaim] = Field(default_factory=list)
    action_result_ledger: list[ActionResult] = Field(default_factory=list)
    user_state: CaseUserState = Field(default_factory=CaseUserState)
    user_state_evidence: list[UserStateEvidence] = Field(default_factory=list)
    user_state_revisions: list[UserStateRevision] = Field(default_factory=list)
    evidence_sufficiency: EvidenceSufficiency = EvidenceSufficiency.INSUFFICIENT
    missing_evidence: list[EvidenceNeed] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    interaction_needs: list[InteractionNeed] = Field(default_factory=list)
    status: CaseStatus = CaseStatus.ALIGNING_GOAL
    turn_count: int = 0
    formulation_history: list[FormulationHistoryEntry] = Field(default_factory=list)
    fallback_reason: str | None = None
    conversation_act: ConversationAct = ConversationAct.CASE_FORMULATION
    unmapped_user_spans: list[str] = Field(default_factory=list)
    evidence_catalog: list[str] = Field(default_factory=list)
    readiness: ReadinessAssessment = Field(default_factory=ReadinessAssessment)

    def compact_runtime_history(
        self, *, interaction_window: int = 12, revision_window: int = 24
    ) -> None:
        """Bound prompt-facing audit trails without deleting current semantics.

        Full historical states remain available in the LangGraph checkpoint and
        M5 lifecycle store. Current goal, claims, failures and evidence needs are
        intentionally not truncated here.
        """

        self.formulation_history = self.formulation_history[-interaction_window:]
        current_user_evidence = [item for item in self.user_state_evidence if item.is_current]
        historical_user_evidence = [
            item for item in self.user_state_evidence if not item.is_current
        ][-revision_window:]
        self.user_state_evidence = historical_user_evidence + current_user_evidence
        self.user_state_revisions = self.user_state_revisions[-revision_window:]
        self.goal.revision_history = self.goal.revision_history[-revision_window:]

    def to_resolution_context(
        self,
        resolution_policy: ResolutionPolicy | None = None,
    ) -> dict[str, Any]:
        """Return M1's compact, uncertainty-preserving handoff for M2."""
        active_claims = [
            item
            for item in self.claims
            if item.verification_status is not VerificationStatus.CONTESTED
        ]
        unresolved_statuses = {
            EvidenceNeedStatus.OPEN,
            EvidenceNeedStatus.UNAVAILABLE,
            EvidenceNeedStatus.DEFERRED,
        }

        def handoff_evidence(item: EvidenceNeed) -> dict[str, Any]:
            payload = item.model_dump(mode="json", exclude_none=True)
            if item.status is EvidenceNeedStatus.RESOLVED:
                disposition = "resolved"
            elif item.status is EvidenceNeedStatus.UNAVAILABLE:
                disposition = "unavailable_choose_safe_alternative_or_handoff"
            elif item.status is EvidenceNeedStatus.DEFERRED:
                disposition = "acquire_during_resolution"
            elif item.blocking:
                disposition = "required_before_dependent_action"
            else:
                disposition = "acquire_if_information_value_justifies_cost"
            payload.update(
                {
                    "handoff_disposition": disposition,
                    # M1 is a formulation boundary, not an all-fields gate.
                    "blocks_case_entry": False,
                    "blocks_dependent_actions": bool(
                        item.blocking and item.status is not EvidenceNeedStatus.RESOLVED
                    ),
                }
            )
            return payload

        evidence_groups = {
            "open": [
                handoff_evidence(item)
                for item in self.missing_evidence
                if item.status is EvidenceNeedStatus.OPEN
            ],
            "resolved": [
                handoff_evidence(item)
                for item in self.missing_evidence
                if item.status is EvidenceNeedStatus.RESOLVED
            ],
            "unavailable": [
                handoff_evidence(item)
                for item in self.missing_evidence
                if item.status is EvidenceNeedStatus.UNAVAILABLE
            ],
            "deferred": [
                handoff_evidence(item)
                for item in self.missing_evidence
                if item.status is EvidenceNeedStatus.DEFERRED
            ],
        }
        evidence_constraints = [
            {
                "evidence_key": item.key,
                "issue_id": item.issue_id,
                "status": item.status.value,
                "decision_impact": item.decision_impact.value,
                "acquisition_actor": item.acquisition_actor.value,
                "constraint_scope": "actions_dependent_on_this_evidence",
                "reason": item.description,
            }
            for item in self.missing_evidence
            if item.blocking and item.status in unresolved_statuses
        ]
        return {
            "handoff_contract": {
                "name": "concord_m1_to_m2",
                "version": "1.0",
                "entry_policy": "minimum_actionable_case_with_carried_uncertainty",
            },
            "case_id": self.case_id,
            # A prior for structured experience applicability, never proof that
            # the current Case belongs to or is solved by that domain.
            "domain_prior": self.domain_prior,
            "initial_problem_report": self.initial_problem_report,
            "goal": self.goal.model_dump(mode="json"),
            "case_compass": {
                "original_outcome": self.goal.original_outcome,
                "current_outcome": self.goal.current_outcome,
                "success_criteria": [
                    item.model_dump(mode="json") for item in self.goal.success_criteria
                ],
                "active_issue_id": self.active_issue_id,
                "deferred_issue_ids": [
                    item.issue_id
                    for item in self.reported_issues
                    if item.status is IssueStatus.DEFERRED
                ],
                "goal_revision_history": [
                    item.model_dump(mode="json") for item in self.goal.revision_history
                ],
                "last_grounded_turn": (
                    self.goal.last_grounded_turn_id
                    or self.goal.acknowledged_turn_id
                    or self.goal.reflected_turn_id
                ),
            },
            "situation": self.situation.model_dump(mode="json", exclude_none=True),
            "active_issue": next(
                (
                    item.model_dump(mode="json")
                    for item in self.reported_issues
                    if item.issue_id == self.active_issue_id
                ),
                None,
            ),
            "deferred_issues": [
                item.model_dump(mode="json")
                for item in self.reported_issues
                if item.status is IssueStatus.DEFERRED
            ],
            "reported_issues": [item.model_dump(mode="json") for item in self.reported_issues],
            "observations": [
                item.model_dump(mode="json")
                for item in active_claims
                if item.type is ClaimType.OBSERVATION
            ],
            "hypotheses": [
                item.model_dump(mode="json")
                for item in active_claims
                if item.type is ClaimType.HYPOTHESIS
            ],
            "reported_actions": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self.action_result_ledger
            ],
            "evidence_handoff": evidence_groups,
            "action_constraints": {
                "case_entry_allowed": self.readiness.ready,
                "evidence_complete": not any(
                    item.status in unresolved_statuses for item in self.missing_evidence
                ),
                "pre_resolution_whitelist": {
                    "tool_categories": ["observe", "verify", "handoff"],
                    "operation_modes": ["read_only"],
                },
                "conditional_operation_modes": {
                    "simulated_write": [
                        "runtime_authorized",
                        "resettable_case_environment",
                        "expected_observation_declared",
                    ]
                },
                "restricted_operation_modes": ["production_write"],
                "evidence_constraints": evidence_constraints,
                "runtime_policy_remains_authoritative": True,
            },
            "salient_failures": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self.action_result_ledger
                if item.relevance is ActionRelevance.ACTIVE
                and item.outcome in {ActionOutcome.FAILURE, ActionOutcome.PARTIAL}
            ],
            "scope_constraints": self.goal.scope_constraints,
            "contradictions": self.contradictions,
            "residual_uncertainty": self.readiness.residual_uncertainty,
            "unmapped_user_spans": self.unmapped_user_spans,
            "readiness_reason": self.readiness.reason_codes,
            "user_state": self.user_state.model_dump(mode="json"),
            "human_interaction_state": {
                "scope": "current_case",
                "effective_as_of_turn_id": max(
                    (item.turn_id for item in self.user_state_evidence),
                    default=None,
                ),
                "current_snapshot": self.user_state.model_dump(mode="json"),
                "current_evidence": [
                    item.model_dump(mode="json")
                    for item in self.user_state_evidence
                    if item.is_current
                ],
                "evidence_history": [
                    item.model_dump(mode="json") for item in self.user_state_evidence
                ],
                "revision_history": [
                    item.model_dump(mode="json") for item in self.user_state_revisions
                ],
                "unknown_means": "no_current_grounded_evidence",
            },
            "provisional_resolution_policy": (
                resolution_policy or ResolutionPolicy()
            ).model_dump(mode="json"),
        }


class GroundedValueCandidate(BaseModel):
    value: str
    evidence_quote: str


class GoalCandidate(BaseModel):
    explicit_goal: str | None = None
    explicit_goal_quote: str | None = None
    inferred_goal: str | None = None
    ambiguities: list[str] = Field(default_factory=list)
    scope_constraints: list[str] = Field(default_factory=list)
    operational: bool = False
    replaces_current_goal: bool = False
    goal_kind: GoalKind = GoalKind.UNKNOWN
    success_criteria: list[GroundedValueCandidate] = Field(default_factory=list)

    @field_validator("scope_constraints", mode="before")
    @classmethod
    def normalize_grounded_scope_constraints(cls, value: Any) -> Any:
        """Accept a provider's grounded-value object without losing its value."""
        if not isinstance(value, list):
            return value
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                normalized.append(item.strip())
            elif isinstance(item, dict):
                candidate = item.get("value") or item.get("constraint")
                if isinstance(candidate, str) and candidate.strip():
                    normalized.append(candidate.strip())
        return normalized


class IssueCandidate(BaseModel):
    summary: str
    evidence_quote: str
    existing_issue_id: str | None = None
    relation: IssueRelation = IssueRelation.UNKNOWN
    related_issue_id: str | None = None
    make_active: bool = False
    priority_basis: str | None = None


class SituationUpdate(BaseModel):
    field: str
    value: str
    evidence_quote: str


class ClaimCandidate(BaseModel):
    content: str
    type: ClaimType
    evidence_quote: str
    target: str | None = None
    conditions: list[str] = Field(default_factory=list)
    expected_result: str | None = None
    reported_result: str | None = None
    outcome: ActionOutcome = ActionOutcome.UNKNOWN
    repetitions: int | None = None
    retry_condition: str | None = None
    issue_id: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def normalize_unknown_claim_type(cls, value: Any) -> Any:
        """Unknown provider labels must never promote a claim to an observation."""
        allowed = {item.value for item in ClaimType}
        if not isinstance(value, str) or value.strip().casefold() not in allowed:
            return ClaimType.HYPOTHESIS
        return value.strip().casefold()

    @field_validator("outcome", mode="before")
    @classmethod
    def normalize_unknown_action_outcome(cls, value: Any) -> Any:
        allowed = {item.value for item in ActionOutcome}
        if not isinstance(value, str) or value.strip().casefold() not in allowed:
            return ActionOutcome.UNKNOWN
        return value.strip().casefold()

    @field_validator("repetitions", mode="before")
    @classmethod
    def normalize_repetitions(cls, value: Any) -> Any:
        """Keep an exact count only; qualitative repetition stays in the quote."""
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None


class ClaimRevision(BaseModel):
    original_content: str
    replacement_content: str
    evidence_quote: str


class UserStateSignal(BaseModel):
    field: str
    value: str
    evidence_quote: str


class TurnInterpretation(BaseModel):
    _extraction_attempts: int = PrivateAttr(default=1)

    conversation_act: ConversationAct = ConversationAct.CASE_FORMULATION
    semantic_coverage_complete: bool = True
    unmapped_spans: list[str] = Field(default_factory=list)
    goal: GoalCandidate = Field(default_factory=GoalCandidate)
    situation_updates: list[SituationUpdate] = Field(default_factory=list)
    reported_issues: list[IssueCandidate] = Field(default_factory=list)
    claims: list[ClaimCandidate] = Field(default_factory=list)
    claim_revisions: list[ClaimRevision] = Field(default_factory=list)
    user_state_signals: list[UserStateSignal] = Field(default_factory=list)
    missing_evidence: list[EvidenceNeed] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    resolved_evidence: list[EvidenceResolution] = Field(default_factory=list)
    unavailable_evidence: list[EvidenceDisposition] = Field(default_factory=list)
    resolved_contradictions: list[EvidenceResolution] = Field(default_factory=list)
    goal_acknowledgement: GoalAcknowledgement | None = None

    @property
    def extraction_attempts(self) -> int:
        return self._extraction_attempts

    @field_validator("goal", mode="before")
    @classmethod
    def normalize_null_goal(cls, value: Any) -> Any:
        """Models commonly emit null when a turn does not discuss the goal."""
        return {} if value is None else value

    @field_validator("unmapped_spans", mode="before")
    @classmethod
    def normalize_unmapped_spans(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @field_validator("user_state_signals", mode="before")
    @classmethod
    def discard_ungrounded_user_state_signals(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        return [
            item
            for item in value
            if isinstance(item, UserStateSignal)
            or (
                isinstance(item, dict)
                and all(
                    isinstance(item.get(field), str) and item[field].strip()
                    for field in ("field", "value", "evidence_quote")
                )
            )
        ]


class SemanticRecoveryInterpretation(BaseModel):
    """Compact schema used only when the full semantic extraction is incomplete."""

    conversation_act: ConversationAct = ConversationAct.CASE_FORMULATION
    goal: GoalCandidate = Field(default_factory=GoalCandidate)
    situation_updates: list[SituationUpdate] = Field(default_factory=list)
    reported_issues: list[IssueCandidate] = Field(default_factory=list)
    claims: list[ClaimCandidate] = Field(default_factory=list)
    claim_revisions: list[ClaimRevision] = Field(default_factory=list)
    user_state_signals: list[UserStateSignal] = Field(default_factory=list)
    resolved_evidence: list[EvidenceResolution] = Field(default_factory=list)
    unavailable_evidence: list[EvidenceDisposition] = Field(default_factory=list)
    resolved_contradictions: list[EvidenceResolution] = Field(default_factory=list)
    goal_acknowledgement: GoalAcknowledgement | None = None

    @field_validator("goal", mode="before")
    @classmethod
    def normalize_null_goal(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("user_state_signals", mode="before")
    @classmethod
    def discard_ungrounded_user_state_signals(cls, value: Any) -> Any:
        return TurnInterpretation.discard_ungrounded_user_state_signals(value)

    def to_turn_interpretation(self) -> TurnInterpretation:
        return TurnInterpretation.model_validate(self.model_dump(mode="json"))


class FormulationResult(BaseModel):
    trace_id: str = ""
    response: str
    case_ready: bool
    state: SharedProblemState
    policy: PolicyDecision
    trace: list[dict[str, Any]] = Field(default_factory=list)
    resolution_context: dict[str, Any] | None = None
    m1_model_calls: int = 0
