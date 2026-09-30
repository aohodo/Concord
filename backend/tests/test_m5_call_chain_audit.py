from evaluation.m5_long_horizon.call_chain_audit import audit_case_snapshots


def event(mechanism: str, status: str, behavior_changes=None):
    return {
        "event_type": "control_mechanism_evaluated",
        "status": status,
        "data": {
            "mechanism": mechanism,
            "behavior_changes": behavior_changes or [],
        },
    }


def test_audit_distinguishes_missing_untriggered_and_consumed_mechanisms():
    rows = {
        item["mechanism"]: item
        for item in audit_case_snapshots(
            [
                {
                    "events": [
                        event("case_user_state", "activated", ["latency_weight=0.55"]),
                        event("failure_memory", "not_activated"),
                        event("m3_invocation_policy", "activated", ["scheduled"]),
                    ]
                }
            ]
        )
    }

    assert rows["case_user_state"]["classification"] == "ACTIVATED_AND_CONSUMED"
    assert rows["failure_memory"]["classification"] == "EVALUATED_NOT_TRIGGERED"
    assert rows["adaptive_m3"]["classification"] == "NOT_EVALUATED_CALL_CHAIN_MISSING"
    assert rows["adaptive_m3"]["effectiveness_evaluable"] is False


def test_m3_activation_without_m2_resume_is_not_effectiveness_evidence():
    snapshots = [
        {
            "events": [
                event("adaptive_m3", "activated", ["recruited:reviewer"]),
                {"event_type": "m3_completed", "status": "evidence_required"},
            ]
        }
    ]
    row = {
        item["mechanism"]: item for item in audit_case_snapshots(snapshots)
    }["adaptive_m3"]
    assert row["classification"] == "ACTIVATED_AND_CONSUMED"
    assert row["downstream_outcomes"] == 0
    assert row["effectiveness_evaluable"] is False


def test_m3_becomes_evaluable_only_after_m2_consumes_resume_packet():
    snapshots = [
        {
            "events": [
                event("adaptive_m3", "activated", ["recruited:reviewer"]),
                {"event_type": "m3_completed", "status": "ready_for_m2"},
                {
                    "event_type": "m2_resumed_after_collaboration",
                    "status": "resolved",
                },
            ]
        }
    ]
    row = {
        item["mechanism"]: item for item in audit_case_snapshots(snapshots)
    }["adaptive_m3"]
    assert row["downstream_outcomes"] == 1
    assert row["effectiveness_evaluable"] is True
