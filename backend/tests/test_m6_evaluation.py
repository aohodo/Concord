from evaluation.m6_human_collaboration import evaluate_cases


def test_m6_policy_matrix_preserves_information_and_respects_effort_budgets():
    results = evaluate_cases()

    assert len(results) == 12
    assert all(item["passed"] for item in results)
    assert {item["expression_depth"] for item in results} == {
        "minimal",
        "guided",
        "expert",
    }
    assert all(item["detail_preserved"] for item in results)
