import csv

from evaluation.m4_end_to_end import M4EpisodeRun, load_episodes
from evaluation.m4_end_to_end.report import build_report, save_report_bundle


def test_m4_catalog_has_30_plus_multiturn_episodes_and_vertical_collaboration():
    episodes = load_episodes()

    assert len(episodes) >= 30
    assert len({item.case_id for item in episodes}) == len(episodes)
    assert len({item.domain for item in episodes}) >= 7
    assert {item.user_condition for item in episodes} == {
        "fragmented_novice",
        "deadline_result_first",
        "self_correction",
    }
    assert all(len(item.turns) >= 2 for item in episodes)
    assert all(item.action_rules for item in episodes)
    assert any(
        item.expected.require_m3 and "vertical_m3_resume" in item.tags
        for item in episodes
    )


def test_report_keeps_environment_failure_separate_from_writing_quality(tmp_path):
    runs = [
        M4EpisodeRun(
            episode_id="episode-1",
            case_id="case-1",
            domain="enterprise_it",
            user_condition="fragmented_novice",
            variant="v2_full",
            passed=False,
            final_phase="resolved",
            final_status="resolved",
            latency_ms=12.0,
            failures=["UNVERIFIED_SUCCESS_CLAIM"],
        )
    ]

    save_report_bundle(tmp_path, runs)

    report = build_report(runs)
    assert "UNVERIFIED_SUCCESS_CLAIM" in report
    assert "environment-grounded success" in report
    summary = (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert '"failed": 1' in summary
    with (tmp_path / "result_matrix.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    assert rows[1][1] == "FAIL"


def test_missing_optional_m3_branch_is_a_coverage_note_not_a_failure():
    run = M4EpisodeRun(
        episode_id="episode-m2-solved",
        case_id="case-m2-solved",
        domain="enterprise_it",
        user_condition="self_correction",
        variant="v2_full",
        passed=True,
        final_phase="resolved",
        final_status="resolved",
        latency_ms=10.0,
        coverage_notes=["M3_COVERAGE_NOT_REACHED"],
    )

    report = build_report([run])

    assert "Passed: 1" in report
    assert "M3_COVERAGE_NOT_REACHED" in report


def test_evaluation_context_exposes_affordances_without_hidden_truth():
    episode = load_episodes()[0]
    context = episode.evaluation_context()

    assert "hidden_state" not in context
    assert context["available_actions"]
    assert "evidence_catalog" not in context
    assert "success_criteria" not in context
