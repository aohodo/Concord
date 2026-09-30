from evaluation.m3_collaboration import load_cases


def test_m3_catalog_covers_dynamic_modes_and_conflicts():
    cases = load_cases()
    modes = {mode.value for case in cases for mode in case.expected_modes}
    statuses = {status.value for case in cases for status in case.allowed_statuses}

    assert len(cases) >= 8
    assert {
        "specialist_handoff",
        "parallel_workers",
        "independent_review",
        "diverse_exploration",
    } <= modes
    assert "evidence_required" in statuses
    assert "goal_alignment_required" in statuses
    assert any(case.maximum_agents == 1 for case in cases)
