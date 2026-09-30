"""Auditable contracts for interactive evaluation episodes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


class HiddenFact(BaseModel):
    key: str
    value: str
    aliases: list[str] = Field(default_factory=list)
    critical: bool = True
    reveal_after_turn: int = 1
    revealed: bool = False


class DynamicUserState(BaseModel):
    domain_knowledge: Literal["low", "medium", "high"] = "medium"
    clarity: Literal["low", "medium", "high"] = "medium"
    frustration: int = Field(default=0, ge=0, le=4)
    patience: int = Field(default=3, ge=0, le=4)
    effort_budget: int = Field(default=3, ge=0, le=4)
    deadline_turns: int | None = Field(default=None, ge=1)


class NormativeSource(BaseModel):
    source_id: str
    title: str
    url: str
    version: str


class NormativeRule(BaseModel):
    rule_id: str
    statement: str
    applies_when: str
    executable_check: str
    source_ids: list[str]
    severity: Literal["required", "recommended"] = "required"


class NormativeReference(BaseModel):
    first_priorities: list[str]
    acceptable_actions: list[str]
    prohibited_actions: list[str] = Field(default_factory=list)
    stop_condition: str
    rule_ids: list[str] = Field(default_factory=list)


class NegativePressure(BaseModel):
    pressure_type: Literal[
        "false_authority",
        "confident_hypothesis",
        "emotional_pressure",
        "premature_solution",
        "cross_domain_transfer",
    ]
    unsupported_claim: str
    must_remain_unverified: bool = True
    after_system_turn: int = 1
    message: str | None = None


class EnvironmentAction(BaseModel):
    action_id: str
    aliases: list[str]
    requires: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] = Field(default_factory=dict)
    safe: bool = True


class EnvironmentSpec(BaseModel):
    initial_state: dict[str, Any]
    observable_keys: list[str]
    expected_final_state: dict[str, Any]
    actions: list[EnvironmentAction]


class CorrectionEvent(BaseModel):
    after_system_turn: int = Field(ge=1)
    message: str
    original_claim: str
    replacement_claim: str


class EpisodeSpec(BaseModel):
    episode_id: str
    scenario_id: str = ""
    interaction_profile: str = "baseline"
    expected_semantic_signals: list[str] = Field(default_factory=list)
    domain: str
    initial_message: str
    latent_case: str
    goal: str
    initial_hypothesis: str | None = None
    hidden_facts: list[HiddenFact]
    user_state: DynamicUserState = Field(default_factory=DynamicUserState)
    normative_reference: NormativeReference = Field(
        validation_alias=AliasChoices("normative_reference", "professional_reference")
    )
    normative_rules: list[NormativeRule] = Field(default_factory=list)
    normative_sources: list[NormativeSource] = Field(default_factory=list)
    negative_pressure: NegativePressure | None = None
    environment: EnvironmentSpec | None = None
    correction_event: CorrectionEvent | None = None
    max_turns: int = Field(default=6, ge=2, le=12)


class EpisodeTurn(BaseModel):
    turn_number: int
    user_message: str
    system_response: str
    action: str
    addressed_need: str = ""
    target_evidence: str = ""
    case_ready: bool
    case_status: str
    problem_state: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    contract_compliant: bool = True
    contract_violations: list[str] = Field(default_factory=list)
    revealed_fact_keys: list[str] = Field(default_factory=list)
    simulator_note: str = ""
    environment_observations: dict[str, Any] = Field(default_factory=dict)
    executed_action: str | None = None
    m1_model_calls: int = 0
    control_priors: dict[str, Any] = Field(default_factory=dict)


class EpisodeMetrics(BaseModel):
    reached_case_ready: bool
    turns_to_ready: int | None = None
    premature_ready: bool = False
    critical_fact_recall: float = 0.0
    evidence_gain_per_turn: float = 0.0
    no_progress_turns: int = 0
    unnecessary_questions: int = 0
    repeated_action_violations: int = 0
    hypothesis_promoted_to_fact: int = 0
    contract_violation_turns: int = 0
    total_latency_ms: float = 0.0
    user_frustration_delta: int = 0
    user_patience_delta: int = 0
    normative_first_action_match: bool = False
    explicit_goal_captured: bool = False
    mutual_goal_grounding: bool = False
    correction_recovery: bool | None = None
    user_burden_score: float = 0.0
    user_abandoned: bool = False
    resolution_success: str = "unavailable"
    token_usage: str = "unavailable"
    final_state_match: bool = False
    normative_policy_pass: bool = False
    negative_pressure_pass: bool = False
    fact_distortion_count: int = 0
    semantic_response_fact_fidelity: Literal["PASS", "FAIL", "UNAVAILABLE"] = (
        "UNAVAILABLE"
    )
    objective_pass: bool = False
    m1_model_calls: int = 0
    response_characters: int = 0
    failure_retention_pass: bool | None = None
    m1_boundary_pass: bool = False
    semantic_signal_recall: float = 1.0
    missed_semantic_signals: list[str] = Field(default_factory=list)


class SubjectiveJudgeResult(BaseModel):
    judge_id: str
    comprehensibility: Literal["PASS", "PARTIAL", "FAIL"]
    interaction_burden: Literal["PASS", "PARTIAL", "FAIL"]
    professional_impression: Literal["PASS", "PARTIAL", "FAIL"]
    fact_fidelity: Literal["PASS", "FAIL"]
    fact_violations: list[str] = Field(default_factory=list)
    notes: str = ""


class RuleCheckResult(BaseModel):
    rule_id: str
    passed: bool
    evidence: str


class EpisodeResult(BaseModel):
    spec: EpisodeSpec
    turns: list[EpisodeTurn]
    metrics: EpisodeMetrics
    final_user_state: DynamicUserState
    stop_reason: str
    exception: str | None = None
    variant: str = "v2_full"
    run_number: int = 1
    subjective_judges: list[SubjectiveJudgeResult] = Field(default_factory=list)
    judge_disagreement: bool = False
    style_truth_conflict: bool = False
    rule_checks: list[RuleCheckResult] = Field(default_factory=list)
