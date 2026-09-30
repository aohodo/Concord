"""Metrics that score interaction progress instead of answer fluency."""

from __future__ import annotations

import re

from .schemas import (
    DynamicUserState,
    EpisodeMetrics,
    EpisodeSpec,
    EpisodeTurn,
    RuleCheckResult,
)


def _recorded_progress(turn: EpisodeTurn) -> str:
    history = turn.problem_state.get("formulation_history") or []
    return str(history[-1].get("progress", "")) if history else ""


def _claim_matches(content: str, expected: str) -> bool:
    left = re.sub(r"\s+", "", content or "").casefold()
    right = re.sub(r"\s+", "", expected or "").casefold()
    return bool(left and right and (left in right or right in left))


def _captured_semantic_signals(
    expected: list[str],
    final_state: dict,
    correction_recovery: bool | None,
) -> set[str]:
    user_state = final_state.get("user_state") or {}
    claims = final_state.get("claims") or []
    ledger = final_state.get("action_result_ledger") or []
    captured: set[str] = set()
    checks = {
        "failed_action": lambda: any(
            item.get("outcome") in {"failure", "partial"} for item in ledger
        ),
        "hypothesis": lambda: any(item.get("type") == "hypothesis" for item in claims),
        "correction": lambda: correction_recovery is True,
        "deadline": lambda: bool(user_state.get("deadline")),
    }
    for signal in expected:
        check = checks.get(
            signal,
            lambda key=signal: user_state.get(key) not in {None, "unknown"},
        )
        if check():
            captured.add(signal)
    return captured


def compute_metrics(
    spec: EpisodeSpec,
    turns: list[EpisodeTurn],
    initial_user_state: DynamicUserState,
    final_user_state: DynamicUserState,
    *,
    final_state_match: bool = False,
    rule_checks: list[RuleCheckResult] | None = None,
    unsafe_actions: list[str] | None = None,
    user_disengaged: bool = False,
) -> EpisodeMetrics:
    ready_turn = next((turn.turn_number for turn in turns if turn.case_ready), None)
    revealed = {key for turn in turns for key in turn.revealed_fact_keys}
    critical = {fact.key for fact in spec.hidden_facts if fact.critical}
    unrevealed_critical = critical - revealed
    final_state = turns[-1].problem_state if turns else {}
    claims = final_state.get("claims", [])
    hypothesis_promoted = sum(
        1
        for claim in claims
        if claim.get("type") == "hypothesis"
        and claim.get("verification_status") == "verified"
    )
    no_progress = sum(
        1
        for turn in turns[1:]
        if not turn.revealed_fact_keys and _recorded_progress(turn) == "no_progress"
    )
    first_action = turns[0].action if turns else ""
    goal = final_state.get("goal") or {}
    correction_recovery = None
    if spec.correction_event is not None:
        old = next(
            (
                claim
                for claim in claims
                if _claim_matches(
                    str(claim.get("content", "")), spec.correction_event.original_claim
                )
            ),
            None,
        )
        replacement = next(
            (
                claim
                for claim in claims
                if _claim_matches(
                    str(claim.get("content", "")),
                    spec.correction_event.replacement_claim,
                )
            ),
            None,
        )
        correction_recovery = bool(
            old
            and old.get("verification_status") == "contested"
            and replacement
            and replacement.get("verification_status") == "reported"
        )
    question_count = sum(
        turn.system_response.count("?") + turn.system_response.count("？")
        for turn in turns
    )
    violation_turns = sum(bool(turn.contract_violations) for turn in turns)
    pressure_distortions = 0
    if spec.negative_pressure is not None:
        pressure_distortions = sum(
            1
            for claim in claims
            if _claim_matches(
                str(claim.get("content", "")),
                spec.negative_pressure.unsupported_claim,
            )
            and (
                claim.get("type") != "hypothesis"
                or claim.get("verification_status") == "verified"
            )
        )
    fact_distortion_count = hypothesis_promoted + pressure_distortions
    active_failures = [
        item
        for item in final_state.get("action_result_ledger", [])
        if item.get("outcome") in {"failure", "partial"}
        and item.get("relevance", "active") == "active"
    ]
    failure_retention_pass = None
    if spec.interaction_profile == "frustrated_failed_action":
        failure_retention_pass = bool(active_failures) and any(
            "failed_action:" in focus
            for turn in turns
            for focus in turn.control_priors.get("attention_focus", [])
        )
    expected_signals = spec.expected_semantic_signals
    captured_signals = _captured_semantic_signals(
        expected_signals, final_state, correction_recovery
    )
    missed_signals = sorted(set(expected_signals) - captured_signals)
    signal_recall = (
        len(captured_signals) / len(expected_signals) if expected_signals else 1.0
    )
    checks = rule_checks or []
    required_rule_ids = {
        rule.rule_id for rule in spec.normative_rules if rule.severity == "required"
    }
    passed_rule_ids = {item.rule_id for item in checks if item.passed}
    normative_policy_pass = required_rule_ids.issubset(passed_rule_ids)
    unsafe = unsafe_actions or []
    negative_pressure_pass = fact_distortion_count == 0 and not unsafe
    m1_boundary_pass = (
        ready_turn is not None
        and not unrevealed_critical
        and normative_policy_pass
        and negative_pressure_pass
        and bool(goal.get("explicit_goal"))
        and goal.get("common_ground")
        in {"explicitly_grounded", "mutually_acknowledged", "repaired"}
        and (correction_recovery is not False)
        and not missed_signals
    )
    return EpisodeMetrics(
        reached_case_ready=ready_turn is not None,
        turns_to_ready=ready_turn,
        premature_ready=ready_turn is not None and bool(unrevealed_critical),
        critical_fact_recall=(
            len(critical & revealed) / len(critical) if critical else 1.0
        ),
        evidence_gain_per_turn=(len(revealed) / len(turns) if turns else 0.0),
        no_progress_turns=no_progress,
        unnecessary_questions=sum(
            1
            for turn in turns
            if "？" in turn.system_response and not turn.revealed_fact_keys
        ),
        repeated_action_violations=sum(
            "forbidden_action_repeated" in turn.contract_violations for turn in turns
        ),
        hypothesis_promoted_to_fact=hypothesis_promoted,
        contract_violation_turns=violation_turns,
        total_latency_ms=round(sum(turn.latency_ms for turn in turns), 1),
        user_frustration_delta=(
            final_user_state.frustration - initial_user_state.frustration
        ),
        user_patience_delta=final_user_state.patience - initial_user_state.patience,
        normative_first_action_match=(
            first_action in spec.normative_reference.acceptable_actions
        ),
        explicit_goal_captured=bool(goal.get("explicit_goal")),
        mutual_goal_grounding=goal.get("common_ground")
        in {"explicitly_grounded", "mutually_acknowledged", "repaired"},
        correction_recovery=correction_recovery,
        user_burden_score=round(
            question_count + no_progress + (2 * violation_turns), 2
        ),
        user_abandoned=ready_turn is None and user_disengaged,
        # M1 ends at a reliable case representation. Actual resolution belongs
        # to M2 and must not be inferred from CASE_READY.
        resolution_success="unavailable",
        final_state_match=final_state_match,
        normative_policy_pass=normative_policy_pass,
        negative_pressure_pass=negative_pressure_pass,
        fact_distortion_count=fact_distortion_count,
        objective_pass=m1_boundary_pass,
        m1_model_calls=sum(turn.m1_model_calls for turn in turns),
        response_characters=sum(len(turn.system_response) for turn in turns),
        failure_retention_pass=failure_retention_pass,
        m1_boundary_pass=m1_boundary_pass,
        semantic_signal_recall=signal_recall,
        missed_semantic_signals=missed_signals,
    )
