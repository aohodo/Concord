from evaluation.m8_productization.showcase import build_showcase


def test_showcase_detects_collaboration_consumed_on_a_later_user_turn():
    summary = build_showcase(
        {
            "episode_id": "episode-1",
            "case_id": "case-1",
            "passed": True,
            "final_status": "resolved",
            "latency_ms": 1200,
            "m1_model_calls": 1,
            "m2_model_calls": 2,
            "m3_model_calls": 3,
            "failures": [],
            "turns": [{"response": {"outcome_verdict": "verified_success"}}],
            "case_snapshot": {
                "events": [
                    {"event_type": "m1_turn_completed"},
                    {"event_type": "m2_run_completed"},
                    {"event_type": "m3_started"},
                ],
                "m3": {"agents_recruited": ["alternative_explorer"]},
                "m2": {
                    "tool_history": [
                        {
                            "tool_id": "observe",
                            "collaboration_source_ids": ["assignment-1"],
                        }
                    ]
                },
            },
            "tool_trace": {
                "trace": {
                    "events": [
                        {
                            "tool_id": "observe",
                            "result_status": "succeeded",
                            "error_code": None,
                        }
                    ]
                }
            },
        },
        retained=1,
    )

    assert summary["chain"]["m2_resumed"] is True
    assert summary["chain"]["goal_verified"] is True
    assert summary["chain"]["retained_experiences"] == 1
