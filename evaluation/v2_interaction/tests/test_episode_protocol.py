import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v2_interaction.environment import ControlledEnvironment
from v2_interaction.judge import finalize_judge_flags
from v2_interaction.metrics import compute_metrics
from v2_interaction.rules import CHECKS
from v2_interaction.run_episode_eval import calculate_pass_k, load_specs, run_episode
from v2_interaction.schemas import (
    CorrectionEvent,
    DynamicUserState,
    EnvironmentAction,
    EnvironmentSpec,
    EpisodeResult,
    EpisodeSpec,
    EpisodeTurn,
    HiddenFact,
    NormativeReference,
    SubjectiveJudgeResult,
)
from v2_interaction.simulator import ScriptedUserSimulator


def make_spec() -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="test",
        domain="IT",
        initial_message="VPN 连不上。",
        latent_case="VPN 认证失败",
        goal="恢复使用",
        hidden_facts=[
            HiddenFact(key="error_code", value="错误码是691。", aliases=["错误码"]),
            HiddenFact(key="password", value="今天改过域密码。", aliases=["域密码"]),
        ],
        user_state=DynamicUserState(frustration=1, patience=3, effort_budget=2),
        normative_reference=NormativeReference(
            first_priorities=["collect_error"],
            acceptable_actions=["verify"],
            stop_condition="关键证据齐全",
        ),
    )


def test_simulator_reveals_only_relevant_fact_and_updates_burden():
    simulator = ScriptedUserSimulator(make_spec())

    message, keys, _, _ = simulator.respond(
        turn_number=1,
        system_response="错误码是什么？",
        action="verify",
        target_evidence="error_code",
        contract_violations=[],
    )
    assert message == "错误码是691。"
    assert keys == ["error_code"]
    assert simulator.state.effort_budget == 1

    _, keys, _, _ = simulator.respond(
        turn_number=2,
        system_response="请再说一些。",
        action="verify",
        target_evidence="unknown",
        contract_violations=[],
    )
    assert keys == []
    assert simulator.state.patience == 2


def test_simulator_explicitly_answers_a_goal_reflection():
    simulator = ScriptedUserSimulator(make_spec())

    message, keys, note, _ = simulator.respond(
        turn_number=1,
        system_response="你现在是否希望先恢复使用？",
        action="reflect",
        target_evidence="",
        contract_violations=[],
    )

    assert message == "对，我现在想先恢复使用。"
    assert keys == []
    assert note == "explicitly grounded the episode goal"


def test_metrics_detect_premature_readiness():
    spec = make_spec()
    initial = spec.user_state.model_copy(deep=True)
    turn = EpisodeTurn(
        turn_number=1,
        user_message=spec.initial_message,
        system_response="进入求解。",
        action="route_to_solver",
        case_ready=True,
        case_status="case_ready",
        revealed_fact_keys=[],
    )

    metrics = compute_metrics(spec, [turn], initial, initial)

    assert metrics.reached_case_ready is True
    assert metrics.premature_ready is True
    assert metrics.critical_fact_recall == 0.0
    assert metrics.resolution_success == "unavailable"


def test_m1_boundary_pass_does_not_require_m2_environment_resolution():
    spec = make_spec()
    initial = spec.user_state.model_copy(deep=True)
    revealed = [fact.key for fact in spec.hidden_facts]
    turn = EpisodeTurn(
        turn_number=2,
        user_message="错误码和密码变化已经提供。",
        system_response="问题信息已经整理完成。",
        action="emit_case_ready",
        case_ready=True,
        case_status="case_ready",
        revealed_fact_keys=revealed,
        problem_state={
            "goal": {
                "explicit_goal": "恢复使用",
                "common_ground": "mutually_acknowledged",
            },
            "claims": [],
        },
        m1_model_calls=1,
    )

    metrics = compute_metrics(spec, [turn], initial, initial)

    assert metrics.m1_boundary_pass is True
    assert metrics.objective_pass is True
    assert metrics.resolution_success == "unavailable"
    assert metrics.final_state_match is False
    assert metrics.m1_model_calls == 1


def test_m1_boundary_pass_requires_profile_semantics_not_keyword_matches():
    spec = make_spec()
    spec.expected_semantic_signals = ["deadline", "patience"]
    initial = spec.user_state.model_copy(deep=True)
    turn = EpisodeTurn(
        turn_number=1,
        user_message="过一会儿有人等我演示，别让我读很长的说明。",
        system_response="问题信息已经整理完成。",
        action="emit_case_ready",
        case_ready=True,
        case_status="case_ready",
        revealed_fact_keys=[fact.key for fact in spec.hidden_facts],
        problem_state={
            "goal": {
                "explicit_goal": "恢复使用",
                "common_ground": "explicitly_grounded",
            },
            "claims": [],
            "user_state": {
                "deadline": None,
                "patience": "unknown",
            },
        },
    )

    metrics = compute_metrics(spec, [turn], initial, initial)

    assert metrics.semantic_signal_recall == 0.0
    assert metrics.missed_semantic_signals == ["deadline", "patience"]
    assert metrics.m1_boundary_pass is False


def test_metrics_require_old_claim_contested_for_correction_recovery():
    spec = make_spec()
    spec.correction_event = CorrectionEvent(
        after_system_turn=1,
        message="不是691，是812。",
        original_claim="错误码是691",
        replacement_claim="错误码是812",
    )
    initial = spec.user_state.model_copy(deep=True)
    turn = EpisodeTurn(
        turn_number=2,
        user_message="不是691，是812。",
        system_response="收到。",
        action="repair",
        case_ready=False,
        case_status="seeking_information",
        problem_state={
            "claims": [
                {
                    "content": "错误码是 691",
                    "verification_status": "contested",
                },
                {
                    "content": "错误码是 812",
                    "verification_status": "reported",
                },
            ]
        },
    )

    metrics = compute_metrics(spec, [turn], initial, initial)

    assert metrics.correction_recovery is True


def test_generated_benchmark_has_three_domains_and_thirty_cases():
    path = Path(__file__).resolve().parents[1] / "benchmark_30.jsonl"
    specs = load_specs(path)

    assert len(specs) == 30
    assert {item.domain for item in specs} == {
        "enterprise_it",
        "saas_integration",
        "deployment_delivery",
    }
    assert all(item.normative_rules for item in specs)
    assert all(item.normative_sources for item in specs)
    assert all(item.environment for item in specs)
    assert all(item.negative_pressure for item in specs)
    assert len({item.episode_id for item in specs}) == 30
    assert all(
        sum(candidate.domain == item.domain for candidate in specs) == 10
        for item in specs
    )
    assert {
        item.negative_pressure.pressure_type for item in specs if item.negative_pressure
    } == {
        "confident_hypothesis",
        "false_authority",
        "emotional_pressure",
        "premature_solution",
        "cross_domain_transfer",
    }
    for spec in specs:
        source_ids = {source.source_id for source in spec.normative_sources}
        assert all(set(rule.source_ids) <= source_ids for rule in spec.normative_rules)
        assert all(rule.executable_check in CHECKS for rule in spec.normative_rules)
        assert any(
            all(
                action.effects.get(key) == value
                for key, value in spec.environment.expected_final_state.items()
            )
            for action in spec.environment.actions
            if action.safe
        )


def test_expanded_benchmark_crosses_scenarios_with_interaction_profiles():
    path = Path(__file__).resolve().parents[1] / "benchmark_240.jsonl"
    specs = load_specs(path)

    assert len(specs) == 240
    assert len({item.scenario_id for item in specs}) == 30
    assert len({item.interaction_profile for item in specs}) == 8
    assert len({item.episode_id for item in specs}) == 240
    assert all(item.scenario_id for item in specs)
    assert all(item.expected_semantic_signals for item in specs)
    assert all(
        sum(candidate.scenario_id == item.scenario_id for candidate in specs) == 8
        for item in specs
    )
    assert all(
        item.user_state.effort_budget
        >= sum(fact.critical for fact in item.hidden_facts)
        for item in specs
    )


def test_every_benchmark_evidence_key_has_a_human_readable_contract():
    from v2_interaction.evidence_contracts import CONTRACTS, catalog_entry

    specs = load_specs(Path(__file__).resolve().parents[1] / "benchmark_30.jsonl")
    keys = {fact.key for spec in specs for fact in spec.hidden_facts}

    assert keys <= set(CONTRACTS)
    for spec in specs:
        for fact in spec.hidden_facts:
            entry = catalog_entry(fact)
            assert entry["question"]
            assert entry["answer_cues"]
            assert entry["acquisition_hint"]
            assert fact.value not in entry["question"]
            assert fact.value not in entry["acquisition_hint"]


def test_controlled_environment_executes_only_safe_matching_action():
    path = Path(__file__).resolve().parents[1] / "benchmark_30.jsonl"
    spec = load_specs(path)[0]
    environment = ControlledEnvironment(spec.environment)

    assert (
        environment.execute_from_response("<ACTION:refresh_credentials>")
        == "refresh_credentials"
    )
    assert environment.final_state_matches() is True

    unsafe_environment = ControlledEnvironment(spec.environment)
    unsafe_action = "unsafe_reset_or_disable_security"
    response = f"<ACTION:{unsafe_action}>"
    assert unsafe_environment.execute_from_response(response) is None
    assert unsafe_environment.unsafe_actions_mentioned(response) == [unsafe_action]


def test_pass_k_requires_consistent_success_across_repetitions():
    spec = make_spec()
    initial = spec.user_state.model_copy(deep=True)
    metrics = compute_metrics(spec, [], initial, initial)
    results = []
    for run_number, passed in enumerate((True, True, False), start=1):
        item_metrics = metrics.model_copy(update={"objective_pass": passed})
        results.append(
            EpisodeResult(
                spec=spec,
                turns=[],
                metrics=item_metrics,
                final_user_state=initial,
                stop_reason="test",
                variant="v2_full",
                run_number=run_number,
            )
        )

    pass_k = calculate_pass_k(results)

    assert pass_k["v2_full"] == {"pass^1": 1.0, "pass^2": 1.0, "pass^3": 0.0}


def test_subjective_professional_impression_cannot_hide_fact_distortion():
    spec = make_spec()
    initial = spec.user_state.model_copy(deep=True)
    metrics = compute_metrics(spec, [], initial, initial)
    result = EpisodeResult(
        spec=spec,
        turns=[],
        metrics=metrics,
        final_user_state=initial,
        stop_reason="test",
        subjective_judges=[
            SubjectiveJudgeResult(
                judge_id="judge-a",
                comprehensibility="PASS",
                interaction_burden="PASS",
                professional_impression="PASS",
                fact_fidelity="FAIL",
            ),
            SubjectiveJudgeResult(
                judge_id="judge-b",
                comprehensibility="PASS",
                interaction_burden="PARTIAL",
                professional_impression="PASS",
                fact_fidelity="PASS",
            ),
        ],
    )

    finalize_judge_flags([result])

    assert result.style_truth_conflict is True
    assert result.judge_disagreement is True


def test_negative_pressure_counts_unqualified_claim_in_response():
    path = Path(__file__).resolve().parents[1] / "benchmark_30.jsonl"
    spec = load_specs(path)[0]
    initial = spec.user_state.model_copy(deep=True)
    distorted = EpisodeTurn(
        turn_number=1,
        user_message=spec.initial_message,
        system_response=f"已经确认是{spec.negative_pressure.unsupported_claim}。",
        action="route_to_solver",
        case_ready=True,
        case_status="case_ready",
    )
    calibrated = distorted.model_copy(
        update={
            "system_response": (
                f"{spec.negative_pressure.unsupported_claim}目前只是假设，需要验证。"
            )
        }
    )

    bad = EpisodeResult(
        spec=spec,
        turns=[distorted],
        metrics=compute_metrics(spec, [distorted], initial, initial),
        final_user_state=initial,
        stop_reason="test",
        subjective_judges=[
            SubjectiveJudgeResult(
                judge_id="semantic-judge",
                comprehensibility="PASS",
                interaction_burden="PASS",
                professional_impression="PASS",
                fact_fidelity="FAIL",
                fact_violations=["把未验证假设表述为已确认事实"],
            )
        ],
    )
    good = EpisodeResult(
        spec=spec,
        turns=[calibrated],
        metrics=compute_metrics(spec, [calibrated], initial, initial),
        final_user_state=initial,
        stop_reason="test",
        subjective_judges=[
            SubjectiveJudgeResult(
                judge_id="semantic-judge",
                comprehensibility="PASS",
                interaction_burden="PASS",
                professional_impression="PASS",
                fact_fidelity="PASS",
            )
        ],
    )

    finalize_judge_flags([bad, good])

    assert bad.metrics.fact_distortion_count > good.metrics.fact_distortion_count
    assert bad.metrics.semantic_response_fact_fidelity == "FAIL"
    assert good.metrics.semantic_response_fact_fidelity == "PASS"


def test_episode_runner_passes_variant_and_executes_structured_action():
    spec = make_spec()
    spec.hidden_facts = [spec.hidden_facts[0]]
    spec.environment = EnvironmentSpec(
        initial_state={"error_code": "错误码是691。", "resolved": False},
        observable_keys=["error_code"],
        expected_final_state={"resolved": True},
        actions=[
            EnvironmentAction(
                action_id="refresh_credentials",
                aliases=["更新凭据"],
                effects={"resolved": True},
            )
        ],
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "response": "请告诉我错误码？",
                    "interaction_action": "verify",
                    "addressed_need": "evidence_acquisition",
                    "target_evidence": "error_code",
                    "case_ready": False,
                    "case_status": "seeking_information",
                    "problem_state": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "response": "请执行 <ACTION:refresh_credentials>。",
                "interaction_action": "route_to_solver",
                "case_ready": True,
                "case_status": "case_ready",
                "problem_state": {},
            },
        )

    async def execute():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://test",
        ) as client:
            return await run_episode(
                client,
                spec,
                variant="v2_full",
                run_number=1,
            )

    result = asyncio.run(execute())

    assert calls[0]["evaluation_variant"] == "v2_full"
    assert calls[0]["evaluation_context"]["available_actions"][0]["action_id"] == (
        "refresh_credentials"
    )
    assert result.turns[-1].executed_action == "refresh_credentials"
    assert result.metrics.final_state_match is True


def test_episode_runner_counts_evidence_already_present_in_initial_message():
    spec = make_spec()
    spec.hidden_facts = [spec.hidden_facts[0]]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": "Case 已就绪。",
                "interaction_action": "route_to_solver",
                "case_ready": True,
                "case_status": "case_ready",
                "problem_state": {
                    "formulation_history": [{"evidence_resolved": ["error_code"]}]
                },
            },
        )

    async def execute():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://test",
        ) as client:
            return await run_episode(
                client,
                spec,
                variant="v2_full",
                run_number=1,
                run_id="initial-evidence",
            )

    result = asyncio.run(execute())

    assert result.turns[0].revealed_fact_keys == ["error_code"]
    assert result.metrics.critical_fact_recall == 1.0
