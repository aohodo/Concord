from core.human_collaboration import (
    ExpressionDepth,
    InitiativeMode,
    InteractionPreferences,
    derive_human_collaboration_state,
    extract_textual_artifact,
    layer_response,
    select_visible_progress,
)


def test_semantic_user_state_changes_effort_budget_without_raw_keyword_rules():
    state = derive_human_collaboration_state(
        {
            "user_state": {
                "patience": "low",
                "delivery_preference": "result_first",
                "domain_knowledge": "unknown",
            }
        }
    )

    assert state.expression_depth is ExpressionDepth.MINIMAL
    assert state.result_first is True
    assert state.budget.max_primary_response_chars == 360
    assert state.budget.max_user_actions_per_turn == 1
    assert not state.budget.allow_nonblocking_questions


def test_explicit_case_preference_overrides_provisional_state():
    state = derive_human_collaboration_state(
        {"user_state": {"patience": "low"}},
        overrides=InteractionPreferences(
            expression_depth="expert",
            initiative_mode="step_by_step",
        ),
    )

    assert state.expression_depth is ExpressionDepth.EXPERT
    assert state.initiative_mode is InitiativeMode.STEP_BY_STEP
    assert not state.provisional


def test_layered_response_bounds_default_reading_without_losing_detail():
    state = derive_human_collaboration_state(
        {"user_state": {"delivery_preference": "result_first"}}
    )
    original = "先恢复服务。" + "这是可展开的验证细节。" * 80

    layered = layer_response(original, state)

    assert len(layered.primary_message) <= state.budget.max_primary_response_chars
    assert layered.details
    assert layered.primary_message + layered.details == original


def test_layered_response_never_breaks_a_semantic_unit_at_raw_character_limit():
    state = derive_human_collaboration_state(
        {"user_state": {"delivery_preference": "result_first"}}
    )
    first_sentence = "A" * 500 + "。"
    original = first_sentence + "后续细节。"

    layered = layer_response(original, state)

    assert layered.primary_message == first_sentence
    assert layered.details == "后续细节。"


def test_audio_is_accepted_as_sourced_evidence_but_not_fabricated_as_transcript():
    unavailable = extract_textual_artifact(
        {"kind": "audio", "name": "fault.m4a", "media_type": "audio/mp4"}
    )
    transcript = extract_textual_artifact(
        {
            "kind": "audio",
            "name": "fault.m4a",
            "media_type": "audio/mp4",
            "transcript": "用户报告点击恢复后仍然不可用",
        }
    )

    assert unavailable.epistemic_status == "unprocessed"
    assert unavailable.uncertainties
    assert transcript.extraction_method == "user_supplied_transcript"
    assert transcript.observations == ["用户报告点击恢复后仍然不可用"]


def test_progress_cadence_filters_structured_events_not_message_keywords():
    events = [
        {"stage": "planning", "status": "working", "message": "任意文本"},
        {"stage": "verification", "status": "working", "message": "任意文本"},
        {"stage": "tool", "status": "failed", "message": "任意文本"},
    ]
    milestones = derive_human_collaboration_state(
        {}, overrides=InteractionPreferences(progress_cadence="milestones")
    )
    blockers = derive_human_collaboration_state(
        {}, overrides=InteractionPreferences(progress_cadence="blockers_only")
    )

    assert select_visible_progress(events, milestones) == events[1:]
    assert select_visible_progress(events, blockers) == events[2:]


def test_m6_budget_tightens_but_does_not_override_m1_contract():
    state = derive_human_collaboration_state(
        {
            "user_state": {"domain_knowledge": "high"},
            "provisional_resolution_policy": {
                "max_questions_per_turn": 0,
                "max_user_actions_per_turn": 1,
                "allow_nonblocking_questions": False,
            },
        }
    )

    assert state.expression_depth is ExpressionDepth.EXPERT
    assert state.budget.max_questions_per_turn == 0
    assert state.budget.max_user_actions_per_turn == 1
    assert state.budget.allow_nonblocking_questions is False
