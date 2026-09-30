import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from core.problem_formulation.interpreter import LangChainTurnInterpreter, TurnInterpreter
from core.problem_formulation.models import (
    ActionOutcome,
    CaseStatus,
    ClaimCandidate,
    ClaimRevision,
    ClaimType,
    CoarseLevel,
    ConversationAct,
    EpistemicClaim,
    EvidenceDisposition,
    EvidenceNeed,
    EvidencePriority,
    EvidenceResolution,
    ExperimentVariant,
    GoalAcknowledgement,
    GoalCandidate,
    GoalKind,
    GoalStatus,
    GroundedValueCandidate,
    InteractionAction,
    InteractionContract,
    InteractionNeed,
    InteractionNeedType,
    IssueCandidate,
    IssueStatus,
    PolicyDecision,
    SharedProblemState,
    SituationUpdate,
    TurnInterpretation,
    UserStateSignal,
)
from core.problem_formulation.policy import FirstAllowedPolicy, LangChainInteractionPolicy
from core.problem_formulation.service import ProblemFormulationService


class QueuedInterpreter(TurnInterpreter):
    def __init__(self, *items: TurnInterpretation):
        self.items = list(items)

    async def interpret(self, message, current_state):
        return self.items.pop(0)


class FailingInterpreter(TurnInterpreter):
    async def interpret(self, message, current_state):
        raise ValueError("invalid structured output")


def test_provider_near_schema_values_are_safely_normalized():
    claim = ClaimCandidate.model_validate(
        {
            "content": "重启过好几次",
            "type": "action",
            "evidence_quote": "我重启过好几次",
            "repetitions": "好几次",
        }
    )
    need = EvidenceNeed.model_validate(
        {
            "key": "error_message",
            "description": "报错信息",
            "question": "有什么报错？",
            "decision_impact": "critical",
        }
    )

    assert claim.repetitions is None
    assert need.decision_impact is CoarseLevel.HIGH


class FakePlannerClient:
    class Messages:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            return type(
                "Response",
                (),
                {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"primary_action":"prioritize",'
                                '"supporting_actions":["challenge"],'
                                '"reason":"model proposed an out-of-scope action",'
                                '"target_evidence":"goal",'
                                '"question":"你要什么？",'
                                '"response_text":"请确认你的目标。"}'
                            ),
                        }
                    ]
                },
            )()

    def __init__(self):
        self.messages = self.Messages()


class QueuedSemanticClient:
    class Messages:
        def __init__(self, payloads):
            self.payloads = list(payloads)
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            payload = self.payloads.pop(0)
            return type(
                "Response",
                (),
                {"content": [{"type": "text", "text": payload}]},
            )()

    def __init__(self, *payloads):
        self.messages = self.Messages(payloads)


def first_turn() -> TurnInterpretation:
    return TurnInterpretation(
        goal=GoalCandidate(
            explicit_goal="先恢复 VPN 使用",
            explicit_goal_quote="我想先恢复VPN",
            ambiguities=["完整隧道可用还是仅访问一个内网服务"],
            operational=True,
            goal_kind=GoalKind.OUTCOME,
        ),
        situation_updates=[
            SituationUpdate(field="what", value="VPN 无法连接", evidence_quote="VPN连不上")
        ],
        claims=[
            ClaimCandidate(
                content="VPN 无法连接",
                type=ClaimType.OBSERVATION,
                evidence_quote="VPN连不上",
            ),
            ClaimCandidate(
                content="已经重启八遍",
                type=ClaimType.ACTION,
                evidence_quote="重启八遍",
                reported_result="仍然无法连接",
                outcome=ActionOutcome.FAILURE,
                repetitions=8,
            ),
            ClaimCandidate(
                content="DNS 故障",
                type=ClaimType.HYPOTHESIS,
                evidence_quote="肯定是DNS",
            ),
            # This candidate must be discarded because its quote is not user-provided.
            ClaimCandidate(
                content="服务器宕机",
                type=ClaimType.OBSERVATION,
                evidence_quote="服务器已经宕机",
            ),
        ],
        user_state_signals=[
            UserStateSignal(field="frustration", value="high", evidence_quote="别再让我重启了"),
            UserStateSignal(field="patience", value="low", evidence_quote="我没时间听长篇解释"),
        ],
        missing_evidence=[
            EvidenceNeed(
                key="error_code",
                description="VPN 客户端错误码",
                question="把当前错误码原文告诉我即可。",
                priority=EvidencePriority.HIGH,
                user_burden=CoarseLevel.LOW,
            )
        ],
    )


def second_turn() -> TurnInterpretation:
    return TurnInterpretation(
        situation_updates=[
            SituationUpdate(field="when", value="修改域密码后", evidence_quote="今天改过域密码")
        ],
        claims=[
            ClaimCandidate(
                content="VPN 显示错误码 691",
                type=ClaimType.OBSERVATION,
                evidence_quote="错误码是691",
            )
        ],
        missing_evidence=[],
        resolved_evidence=[EvidenceResolution(key="error_code", evidence_quote="错误码是691")],
    )


def test_graph_keeps_epistemic_boundaries_and_adapts_contract():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(first_turn()),
        policy=FirstAllowedPolicy(),
    )
    message = (
        "我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了，别再让我重启了，我没时间听长篇解释。"
    )

    result = asyncio.run(
        service.process(
            message=message,
            user_id="u1",
            conv_id="c1",
            domain_prior="technical",
        )
    )

    assert result.case_ready is True
    assert result.state.evidence_sufficiency.value == "partially_sufficient"
    assert result.state.readiness.carried_forward_evidence == ["error_code"]
    assert result.state.goal.status.value == "confirmed"
    assert result.state.user_state.frustration is CoarseLevel.HIGH
    assert result.state.user_state.patience is CoarseLevel.LOW
    assert result.policy.contract.response_length == "minimal"
    assert result.policy.contract.max_steps_requested == 1
    assert "已经重启八遍" in result.policy.contract.forbidden_actions
    assert {claim.type for claim in result.state.claims} == {
        ClaimType.OBSERVATION,
        ClaimType.ACTION,
        ClaimType.HYPOTHESIS,
    }
    assert all(claim.content != "服务器宕机" for claim in result.state.claims)
    assert [item.summary for item in result.state.reported_issues] == ["VPN 无法连接"]
    dns = next(claim for claim in result.state.claims if claim.content == "DNS 故障")
    assert dns.verification_status.value == "reported"


def test_multiple_problems_accumulate_across_turns_without_overwriting():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    first = reducer.merge_problem(
        state,
        TurnInterpretation(
            situation_updates=[
                SituationUpdate(field="what", value="VPN 无法连接", evidence_quote="VPN连不上"),
                SituationUpdate(
                    field="what", value="共享盘无法打开", evidence_quote="共享盘也打不开"
                ),
            ]
        ),
        "VPN连不上，共享盘也打不开。",
    )
    second = reducer.merge_problem(
        first,
        TurnInterpretation(
            situation_updates=[
                SituationUpdate(
                    field="what", value="邮箱同步延迟", evidence_quote="邮箱现在也不同步"
                )
            ]
        ),
        "邮箱现在也不同步。",
    )

    assert second.situation.what == "VPN 无法连接"
    assert [item.summary for item in second.reported_issues] == [
        "VPN 无法连接",
        "共享盘无法打开",
        "邮箱同步延迟",
    ]


def test_reported_issue_prevents_duplicate_from_situation_what():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = ProblemStateReducer().merge_problem(
        SharedProblemState(case_id="case-dedupe", user_id="u", conv_id="c"),
        TurnInterpretation(
            reported_issues=[
                IssueCandidate(
                    summary="内网访问工具突然无法登录",
                    evidence_quote="公司那个连内网的东西突然登不上了",
                )
            ],
            situation_updates=[
                SituationUpdate(
                    field="what",
                    value="公司内网访问工具突然无法登录",
                    evidence_quote="公司那个连内网的东西突然登不上了",
                )
            ],
        ),
        "公司那个连内网的东西突然登不上了。",
    )

    assert len(state.reported_issues) == 1
    assert state.situation.what == "内网访问工具突然无法登录"


def test_case_compass_keeps_mainline_when_a_new_issue_is_added():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    first = reducer.merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="恢复 VPN 完成演示",
                explicit_goal_quote="先恢复VPN完成演示",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
                success_criteria=[
                    GroundedValueCandidate(
                        value="可以访问演示所需的内网页面",
                        evidence_quote="能打开演示页面就行",
                    )
                ],
            ),
            reported_issues=[
                IssueCandidate(summary="VPN 无法连接", evidence_quote="VPN连不上")
            ],
            claims=[
                ClaimCandidate(
                    content="VPN 无法连接",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="VPN连不上",
                )
            ],
        ),
        "先恢复VPN完成演示，VPN连不上，能打开演示页面就行。",
    )
    second = reducer.merge_problem(
        first,
        TurnInterpretation(
            reported_issues=[
                IssueCandidate(summary="邮箱同步延迟", evidence_quote="邮箱也不同步")
            ],
            claims=[
                ClaimCandidate(
                    content="邮箱同步延迟",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="邮箱也不同步",
                )
            ],
        ),
        "对了，邮箱也不同步。",
    )

    assert second.goal.original_outcome == "恢复 VPN 完成演示"
    assert second.goal.current_outcome == "恢复 VPN 完成演示"
    assert second.goal.revision_history == []
    assert second.active_issue_id == "issue-1"
    assert second.situation.what == "VPN 无法连接"
    assert second.reported_issues[1].status is IssueStatus.DEFERRED


def test_explicit_goal_revision_is_auditable_instead_of_silent_overwrite():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    first = reducer.merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="恢复 VPN",
                explicit_goal_quote="先恢复VPN",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
            )
        ),
        "先恢复VPN。",
    )
    revised = reducer.merge_problem(
        first,
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="先恢复邮箱收信",
                explicit_goal_quote="现在改成先恢复邮箱收信",
                operational=True,
                replaces_current_goal=True,
                goal_kind=GoalKind.OUTCOME,
            )
        ),
        "现在改成先恢复邮箱收信。",
    )

    assert revised.goal.original_outcome == "恢复 VPN"
    assert revised.goal.current_outcome == "先恢复邮箱收信"
    assert revised.goal.revision_history[0].previous_outcome == "恢复 VPN"
    assert revised.goal.revision_history[0].evidence_quote == "现在改成先恢复邮箱收信"


def test_explicit_focus_switch_defers_old_issue_without_losing_it():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    first = reducer.merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            reported_issues=[
                IssueCandidate(summary="VPN 无法连接", evidence_quote="VPN连不上")
            ]
        ),
        "VPN连不上。",
    )
    switched = reducer.merge_problem(
        first,
        TurnInterpretation(
            reported_issues=[
                IssueCandidate(
                    summary="邮箱同步延迟",
                    evidence_quote="先处理邮箱不同步",
                    make_active=True,
                    priority_basis="用户明确要求先处理",
                )
            ]
        ),
        "先处理邮箱不同步。",
    )

    assert switched.active_issue_id == "issue-2"
    assert switched.reported_issues[0].status is IssueStatus.DEFERRED
    assert switched.reported_issues[1].status is IssueStatus.ACTIVE
    assert switched.situation.what == "邮箱同步延迟"
    assert switched.last_focus_change_turn_id == 2


def test_langgraph_persists_case_and_emits_resolution_context_when_ready():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(first_turn(), second_turn()),
        policy=FirstAllowedPolicy(),
    )

    first = asyncio.run(
        service.process(
            message="我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了，别再让我重启了，我没时间听长篇解释。",
            user_id="u1",
            conv_id="same-case",
            domain_prior="technical",
        )
    )
    second = asyncio.run(
        service.process(
            message="错误码是691，今天改过域密码。",
            user_id="u1",
            conv_id="same-case",
            domain_prior="technical",
        )
    )

    assert first.case_ready is True
    assert second.case_ready is True
    assert second.state.turn_count == 2
    assert second.state.goal.explicit_goal == "先恢复 VPN 使用"
    assert second.state.situation.when == "修改域密码后"
    assert second.policy.action is InteractionAction.EMIT_CASE_READY
    assert second.resolution_context is not None
    assert second.resolution_context["goal"]["explicit_goal"] == "先恢复 VPN 使用"
    assert second.policy.control_priors.stopping == "emit_case_ready"


def test_behavior_planner_cannot_escape_need_capabilities():
    state = SharedProblemState(
        case_id="case-1",
        user_id="u1",
        conv_id="c1",
        interaction_needs=[
            InteractionNeed(
                type=InteractionNeedType.GOAL_ALIGNMENT,
                reason="goal is ambiguous",
                priority=EvidencePriority.CRITICAL,
            )
        ],
    )
    policy = LangChainInteractionPolicy(FakePlannerClient(), "test-model")

    decision = asyncio.run(policy.select_action(state, InteractionContract()))

    assert decision.action is InteractionAction.REFLECT
    assert decision.supporting_actions == []
    assert set(decision.candidate_actions) == {
        InteractionAction.REFLECT,
        InteractionAction.STRUCTURE,
    }
    assert policy._client.messages.calls == 0


def test_open_evidence_cannot_disappear_without_grounded_resolution():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    first = ProblemStateReducer().merge_problem(
        state,
        first_turn(),
        "我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了，别再让我重启了，我没时间听长篇解释。",
    )
    second = ProblemStateReducer().merge_problem(
        first,
        TurnInterpretation(),
        "我再想想。",
    )

    assert [item.key for item in second.missing_evidence if item.status.value == "open"] == [
        "error_code"
    ]


def test_inferred_goal_requires_grounding_before_it_is_confirmed():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    inferred = reducer.merge_problem(
        state,
        TurnInterpretation(
            goal=GoalCandidate(
                inferred_goal="恢复 VPN 使用",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
            )
        ),
        "VPN坏了。",
    )
    inferred.goal.reflected_turn_id = 1
    accepted = reducer.merge_problem(
        inferred,
        TurnInterpretation(
            goal_acknowledgement=GoalAcknowledgement(
                accepted=True,
                evidence_quote="对，先恢复使用",
            )
        ),
        "对，先恢复使用。",
    )

    assert inferred.goal.status.value == "inferred"
    assert accepted.goal.status.value == "confirmed"
    assert accepted.goal.common_ground.value == "mutually_acknowledged"


def test_problem_report_creates_only_a_confirmable_default_goal():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = ProblemStateReducer().merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            situation_updates=[
                SituationUpdate(
                    field="what",
                    value="账号无法登录",
                    evidence_quote="账号突然登不上",
                )
            ],
            claims=[
                ClaimCandidate(
                    content="账号无法登录",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="账号突然登不上",
                )
            ],
        ),
        "账号突然登不上。",
    )

    assert state.goal.explicit_goal is None
    assert state.goal.inferred_goal == "解决当前问题：账号无法登录"
    assert state.goal.status.value == "inferred"
    assert state.goal.common_ground.value == "unilateral"


def test_explicit_outcome_can_replace_an_unconfirmed_inferred_goal():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    state.goal.inferred_goal = "解决当前问题：账号无法登录"
    state.goal.status = GoalStatus.INFERRED
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="恢复账号登录",
                explicit_goal_quote="想先恢复账号登录",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
            )
        ),
        "对，我现在想先恢复账号登录。",
    )

    assert updated.goal.explicit_goal == "恢复账号登录"
    assert updated.goal.status.value == "confirmed"
    assert updated.goal.common_ground.value == "explicitly_grounded"


def test_null_goal_from_model_is_a_noop_instead_of_a_formulation_failure():
    interpretation = TurnInterpretation.model_validate({"goal": None})

    assert interpretation.goal == GoalCandidate()


def test_empty_semantic_extraction_gets_one_model_based_recovery_not_keywords():
    client = QueuedSemanticClient(
        ('{"missing_evidence":[{"key":"status","description":"状态","question":"状态是什么？"}]}'),
        (
            '{"claims":[{"content":"已尝试重启但没有恢复",'
            '"type":"action","evidence_quote":"已经重启过，还是不行",'
            '"reported_result":"还是不行","outcome":"failure"}],'
            '"user_state_signals":[{"field":"deadline","value":"很快要演示",'
            '"evidence_quote":"很快要演示"}]}'
        ),
    )
    interpreter = LangChainTurnInterpreter(client, "test-model")

    result = asyncio.run(
        interpreter.interpret(
            "已经重启过，还是不行，很快要演示。",
            SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        )
    )

    assert client.messages.calls == 2
    assert result.extraction_attempts == 2
    assert result.claims[0].outcome.value == "failure"
    assert result.user_state_signals[0].field == "deadline"


def test_partial_semantic_extraction_retries_when_a_clause_has_no_grounding_quote():
    client = QueuedSemanticClient(
        (
            '{"claims":[{"content":"服务登录失败","type":"observation",'
            '"evidence_quote":"服务登录失败"}]}'
        ),
        (
            '{"claims":[{"content":"服务登录失败","type":"observation",'
            '"evidence_quote":"服务登录失败"}],'
            '"user_state_signals":[{"field":"deadline","value":"一会儿要演示",'
            '"evidence_quote":"一会儿要演示"}]}'
        ),
    )
    interpreter = LangChainTurnInterpreter(client, "test-model")

    result = asyncio.run(
        interpreter.interpret(
            "服务登录失败，一会儿要演示。",
            SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        )
    )

    assert client.messages.calls == 2
    assert result.extraction_attempts == 2
    assert result.user_state_signals[0].field == "deadline"


def test_semantic_recovery_can_revise_an_existing_hypothesis_without_keyword_rules():
    client = QueuedSemanticClient(
        "{}",
        (
            '{"claim_revisions":[{"original_content":"Docker 缓存坏了",'
            '"replacement_content":"原因目前还不确定",'
            '"evidence_quote":"我纠正一下，刚才说“Docker缓存损坏”只是猜测，不是事实；原因目前还不确定"}]}'
        ),
    )
    interpreter = LangChainTurnInterpreter(client, "test-model")
    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        claims=[
            EpistemicClaim(
                content="Docker 缓存坏了",
                type=ClaimType.HYPOTHESIS,
                evidence_quote="Docker缓存坏了",
                turn_id=1,
            )
        ],
    )

    result = asyncio.run(
        interpreter.interpret(
            "我纠正一下，刚才说“Docker缓存损坏”只是猜测，不是事实；原因目前还不确定。",
            state,
        )
    )

    assert client.messages.calls == 2
    assert result.extraction_attempts == 2
    assert result.claim_revisions == [
        ClaimRevision(
            original_content="Docker 缓存坏了",
            replacement_content="原因目前还不确定",
            evidence_quote="我纠正一下，刚才说“Docker缓存损坏”只是猜测，不是事实；原因目前还不确定",
        )
    ]


def test_partial_semantic_recovery_keeps_grounded_progress_and_records_residual():
    client = QueuedSemanticClient(
        "{}",
        (
            '{"situation_updates":[{"field":"what","value":"VPN 无法连接",'
            '"evidence_quote":"VPN还是连不上"}]}'
        ),
    )
    interpreter = LangChainTurnInterpreter(client, "test-model")

    result = asyncio.run(
        interpreter.interpret(
            "VPN还是连不上，另外共享盘也有点怪，我一下说不清。",
            SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        )
    )

    assert result.extraction_attempts == 2
    assert result.situation_updates[0].value == "VPN 无法连接"
    assert result.semantic_coverage_complete is False
    assert result.unmapped_spans == ["另外共享盘也有点怪", "我一下说不清"]


def test_goal_confirmation_is_not_guessed_when_semantic_extraction_is_empty():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(case_id="case", user_id="u", conv_id="c", turn_count=1)
    state.goal.inferred_goal = "恢复账号登录"
    state.goal.status = GoalStatus.INFERRED
    state.formulation_history = [
        FormulationHistoryEntry(
            turn_id=1,
            status=CaseStatus.ALIGNING_GOAL,
            action=InteractionAction.REFLECT,
            addressed_need=InteractionNeedType.GOAL_ALIGNMENT,
        )
    ]

    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(),
        "对，我现在想先恢复账号登录。",
    )

    assert updated.goal.explicit_goal is None
    assert updated.goal.status.value == "inferred"
    assert updated.goal.common_ground.value == "unilateral"


def test_interpreter_failure_stays_in_m1_instead_of_falling_back_to_solver():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=FailingInterpreter(),
        policy=FirstAllowedPolicy(),
    )

    result = asyncio.run(
        service.process(
            message="VPN 连不上。",
            user_id="u",
            conv_id="degraded-m1",
            domain_prior="technical",
        )
    )

    assert result.case_ready is False
    assert result.state.status is CaseStatus.FORMULATION_ERROR
    assert result.policy.action is InteractionAction.REPAIR
    assert result.policy.planning_mode == "safe_degraded"
    assert result.state.turn_count == 1
    assert any(item["status"] == "degraded" for item in result.trace)


def test_adjacent_observation_closes_the_last_asked_evidence():
    from core.problem_formulation.models import (
        FormulationHistoryEntry,
        ProgressStatus,
    )
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["peer_scope"],
        missing_evidence=[
            EvidenceNeed(
                key="peer_scope",
                description="影响范围",
                question="其他账号能否正常登录？",
                answer_cues=["其他账号"],
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="peer_scope",
                progress=ProgressStatus.NO_PROGRESS,
            )
        ],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            claims=[
                ClaimCandidate(
                    content="其他账号登录正常",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="其他账号正常",
                )
            ],
            resolved_evidence=[EvidenceResolution(key="peer_scope", evidence_quote="其他账号正常")],
        ),
        "其他账号正常。",
    )

    need = updated.missing_evidence[0]
    assert need.status.value == "resolved"
    assert need.resolution_quote == "其他账号正常"


def test_adjacent_hypothesis_does_not_close_observation_evidence():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["peer_scope"],
        missing_evidence=[
            EvidenceNeed(
                key="peer_scope",
                description="影响范围",
                question="其他账号能否正常登录？",
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="peer_scope",
            )
        ],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            claims=[
                ClaimCandidate(
                    content="管理员删除了权限",
                    type=ClaimType.HYPOTHESIS,
                    evidence_quote="肯定是管理员删了权限",
                )
            ]
        ),
        "肯定是管理员删了权限。",
    )

    assert updated.missing_evidence[0].status.value == "open"


def test_short_adjacent_answer_stays_open_when_semantic_parser_emits_no_resolution():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["account_status"],
        missing_evidence=[
            EvidenceNeed(
                key="account_status",
                description="账号状态",
                question="账号状态显示什么？",
                answer_cues=["账号状态", "locked"],
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="account_status",
            )
        ],
    )

    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(),
        "账号状态显示 locked。",
    )

    assert updated.missing_evidence[0].status.value == "open"


def test_generic_acknowledgement_cannot_close_adjacent_evidence():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["account_status"],
        missing_evidence=[
            EvidenceNeed(
                key="account_status",
                description="账号状态",
                question="账号状态显示什么？",
                answer_cues=["账号状态", "locked"],
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="account_status",
            )
        ],
    )

    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(),
        "对，你先按这个理解继续问。",
    )

    assert updated.missing_evidence[0].status.value == "open"


def test_unknown_evidence_key_cannot_close_a_catalog_item():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        evidence_catalog=["failed_attempts"],
        missing_evidence=[
            EvidenceNeed(
                key="failed_attempts",
                description="失败认证次数",
                question="近期有多少次失败认证？",
                answer_cues=["失败认证", "失败登录", "尝试"],
            )
        ],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            resolved_evidence=[
                EvidenceResolution(
                    key="admin_permission",
                    evidence_quote="资深工程师认为是管理员删除权限",
                )
            ]
        ),
        "资深工程师认为是管理员删除权限。",
    )

    assert updated.missing_evidence[0].status.value == "open"


def test_explicit_semantic_resolution_preserves_its_grounded_quote():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["failed_attempts"],
        missing_evidence=[
            EvidenceNeed(
                key="failed_attempts",
                description="失败认证次数",
                question="近期有多少次失败认证？",
                answer_cues=["失败认证", "失败登录", "尝试"],
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="failed_attempts",
            )
        ],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            claims=[
                ClaimCandidate(
                    content="十分钟内有12次失败认证",
                    type=ClaimType.ACTION,
                    evidence_quote="十分钟内有12次失败认证",
                )
            ],
            resolved_evidence=[EvidenceResolution(key="failed_attempts", evidence_quote="12次")],
        ),
        "十分钟内有12次失败认证。",
    )

    assert updated.missing_evidence[0].status.value == "resolved"
    assert updated.missing_evidence[0].resolution_quote == "12次"


def test_policy_harness_repairs_priority_and_contract_violations():
    from core.problem_formulation.guardrails import PolicyHarness
    from core.problem_formulation.policy import BEHAVIOR_CAPABILITIES

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        interaction_needs=[
            InteractionNeed(
                type=InteractionNeedType.GOAL_ALIGNMENT,
                reason="goal unknown",
                priority=EvidencePriority.CRITICAL,
            ),
            InteractionNeed(
                type=InteractionNeedType.USER_SUPPORT,
                reason="frustrated",
                priority=EvidencePriority.HIGH,
            ),
        ],
    )
    decision = PolicyDecision(
        action=InteractionAction.RESTORE_AGENCY,
        reason="model proposal",
        response_text="请重启。你看到什么？为什么？",
        proposed_actions=["重启"],
        contract=InteractionContract(
            question_budget=1,
            response_length="short",
            forbidden_actions=["重启"],
        ),
    )

    repaired = PolicyHarness(BEHAVIOR_CAPABILITIES).enforce(state, decision)

    assert repaired.action is InteractionAction.REFLECT
    assert repaired.addressed_need is InteractionNeedType.GOAL_ALIGNMENT
    assert repaired.compliance.compliant is True
    assert repaired.compliance.repaired is True
    assert set(repaired.compliance.violations) == {
        "highest_priority_need_not_addressed",
        "question_budget_exceeded",
        "forbidden_action_repeated",
    }


def test_user_correction_contests_old_claim_and_preserves_replacement():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    first = reducer.merge_problem(
        state,
        TurnInterpretation(
            claims=[
                ClaimCandidate(
                    content="错误码是 691",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="错误码是691",
                )
            ]
        ),
        "错误码是691。",
    )
    corrected = reducer.merge_problem(
        first,
        TurnInterpretation(
            claim_revisions=[
                ClaimRevision(
                    original_content="错误码是 691",
                    replacement_content="错误码是 812",
                    evidence_quote="刚才看错了，其实是812",
                )
            ]
        ),
        "刚才看错了，其实是812。",
    )

    old = next(item for item in corrected.claims if item.content == "错误码是 691")
    new = next(item for item in corrected.claims if item.content == "错误码是 812")
    assert old.verification_status.value == "contested"
    assert old.revised_turn_id == 2
    assert new.verification_status.value == "reported"


def test_no_user_state_ablation_skips_case_conditioned_signals():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(first_turn()),
        policy=FirstAllowedPolicy(),
    )
    result = asyncio.run(
        service.process(
            message="我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了。",
            user_id="u",
            conv_id="no-user-state",
            domain_prior="technical",
            experiment_variant=ExperimentVariant.NO_USER_STATE,
        )
    )

    assert result.state.user_state.frustration is CoarseLevel.UNKNOWN
    assert result.state.user_state_evidence == []


def test_no_epistemic_ablation_promotes_hypothesis_to_observation():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(first_turn()),
        policy=FirstAllowedPolicy(),
    )
    result = asyncio.run(
        service.process(
            message="我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了。",
            user_id="u",
            conv_id="no-epistemic",
            domain_prior="technical",
            experiment_variant=ExperimentVariant.NO_EPISTEMIC_SEPARATION,
        )
    )

    dns = next(claim for claim in result.state.claims if claim.content == "DNS 故障")
    assert dns.type is ClaimType.OBSERVATION


def test_process_cooperation_cannot_replace_operational_goal():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    state = reducer.merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="恢复 VPN",
                explicit_goal_quote="先恢复VPN",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
            )
        ),
        "先恢复VPN。",
    )
    updated = reducer.merge_problem(
        state,
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="愿意继续回答问题",
                explicit_goal_quote="继续问吧",
                operational=False,
                goal_kind=GoalKind.COOPERATION,
            )
        ),
        "继续问吧。",
    )

    assert updated.goal.explicit_goal == "恢复 VPN"
    assert updated.goal.common_ground.value == "explicitly_grounded"


def test_evidence_catalog_rejects_invented_synonym_keys():
    from core.problem_formulation.sufficiency import ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        evidence_catalog=["error_code"],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            missing_evidence=[
                EvidenceNeed(
                    key="full_error_detail",
                    description="完整错误",
                    question="完整报错是什么？",
                ),
                EvidenceNeed(
                    key="error_code",
                    description="错误码",
                    question="错误码是什么？",
                ),
            ]
        ),
        "VPN 连不上。",
    )

    assert [item.key for item in updated.missing_evidence] == ["error_code"]


def test_repeated_no_progress_question_switches_to_untried_evidence():
    from core.problem_formulation.guardrails import PolicyHarness
    from core.problem_formulation.policy import BEHAVIOR_CAPABILITIES

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        interaction_needs=[
            InteractionNeed(
                type=InteractionNeedType.EVIDENCE_ACQUISITION,
                reason="blocking evidence",
                priority=EvidencePriority.HIGH,
            )
        ],
        missing_evidence=[
            EvidenceNeed(
                key="error_code",
                description="错误码",
                question="请提供错误码。",
                priority=EvidencePriority.HIGH,
                attempt_count=1,
            ),
            EvidenceNeed(
                key="impact_scope",
                description="影响范围",
                question="其他人是否正常？",
                priority=EvidencePriority.HIGH,
            ),
        ],
    )
    decision = PolicyDecision(
        action=InteractionAction.VERIFY,
        reason="retry",
        target_evidence="error_code",
        question="请再提供一次错误码？",
        response_text="请再提供一次错误码？",
        contract=InteractionContract(),
    )

    repaired = PolicyHarness(BEHAVIOR_CAPABILITIES).enforce(state, decision)

    assert repaired.target_evidence == "impact_scope"
    assert "repeated_no_progress_target" in repaired.compliance.violations


def test_urgent_contract_blocks_nonessential_clarification():
    from core.problem_formulation.needs import InteractionContractBuilder

    state = SharedProblemState(case_id="case", user_id="u", conv_id="c")
    state.user_state.patience = CoarseLevel.LOW
    contract = InteractionContractBuilder().build(state)

    assert contract.response_length == "minimal"
    assert contract.allow_nonblocking_questions is False
    assert contract.max_clarification_turns == 1
    assert contract.progress_disclosure is True


def test_resolution_context_preserves_uncertainty_for_m2():
    from core.problem_formulation.sufficiency import EvidenceAssessor, ProblemStateReducer

    state = ProblemStateReducer().merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            goal=GoalCandidate(
                explicit_goal="恢复 VPN",
                explicit_goal_quote="恢复VPN",
                operational=True,
                goal_kind=GoalKind.OUTCOME,
            ),
            claims=[
                ClaimCandidate(
                    content="错误码 691",
                    type=ClaimType.OBSERVATION,
                    evidence_quote="错误码691",
                ),
                ClaimCandidate(
                    content="DNS 故障",
                    type=ClaimType.HYPOTHESIS,
                    evidence_quote="肯定是DNS",
                ),
            ],
        ),
        "请恢复VPN，错误码691，肯定是DNS。",
    )
    state.missing_evidence = [
        EvidenceNeed(
            key="vpn_client_name",
            description="具体 VPN 客户端决定可用诊断路径",
            question="使用的是什么 VPN 客户端？",
            blocking=True,
            decision_impact="high",
            issue_id=state.active_issue_id,
        ),
        EvidenceNeed(
            key="restart_target",
            description="重启对象只影响解释完整度",
            question="重启的是电脑还是客户端？",
            blocking=False,
            issue_id=state.active_issue_id,
        ),
    ]
    state = EvidenceAssessor().assess(state)
    context = state.to_resolution_context()

    assert context["goal"]["explicit_goal"] == "恢复 VPN"
    assert [item["content"] for item in context["observations"]] == ["错误码 691"]
    assert [item["content"] for item in context["hypotheses"]] == ["DNS 故障"]
    assert context["handoff_contract"]["version"] == "1.0"
    assert [item["key"] for item in context["evidence_handoff"]["open"]] == [
        "vpn_client_name",
        "restart_target",
    ]
    assert context["evidence_handoff"]["open"][0]["blocks_case_entry"] is False
    assert context["evidence_handoff"]["open"][0]["blocks_dependent_actions"] is True
    assert context["evidence_handoff"]["open"][1]["blocks_dependent_actions"] is False
    constraints = context["action_constraints"]
    assert constraints["case_entry_allowed"] is True
    assert constraints["evidence_complete"] is False
    assert constraints["pre_resolution_whitelist"]["tool_categories"] == [
        "observe",
        "verify",
        "handoff",
    ]
    assert constraints["evidence_constraints"][0]["evidence_key"] == "vpn_client_name"
    assert constraints["evidence_constraints"][0]["constraint_scope"] == (
        "actions_dependent_on_this_evidence"
    )


def test_resolved_contract_evidence_survives_model_observation_omission():
    from core.problem_formulation.sufficiency import EvidenceAssessor

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        missing_evidence=[
            EvidenceNeed(
                key="error_code",
                description="客户端错误码",
                question="错误码是什么？",
                status="resolved",
                resolution_quote="错误码是691",
                blocking=True,
            )
        ],
    )
    state.goal.status = GoalStatus.CONFIRMED
    state.goal.explicit_goal = "恢复 VPN"

    assessed = EvidenceAssessor().assess(state)
    context = assessed.to_resolution_context()

    assert assessed.status is CaseStatus.CASE_READY
    resolved = context["evidence_handoff"]["resolved"][0]
    assert resolved["key"] == "error_code"
    assert resolved["resolution_quote"] == "错误码是691"
    assert resolved["handoff_disposition"] == "resolved"


def test_need_detector_uses_the_same_grounding_and_observation_rules_as_readiness():
    from core.problem_formulation.models import CommonGroundStatus
    from core.problem_formulation.needs import InteractionNeedDetector

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        missing_evidence=[
            EvidenceNeed(
                key="error_code",
                description="客户端错误码",
                question="错误码是什么？",
                status="resolved",
                resolution_quote="错误码是691",
            )
        ],
    )
    state.goal.inferred_goal = "恢复 VPN"
    state.goal.status = GoalStatus.INFERRED
    state.goal.common_ground = CommonGroundStatus.REFLECTED

    needs = InteractionNeedDetector().detect(state)
    types = {item.type for item in needs}

    assert InteractionNeedType.GOAL_ALIGNMENT not in types
    assert InteractionNeedType.SITUATION_RECONSTRUCTION not in types


def test_model_deliberation_is_reserved_for_material_contradictions():
    state = SharedProblemState(
        case_id="case-1",
        user_id="u1",
        conv_id="c1",
        contradictions=["用户先后报告账号已锁定和账号状态正常"],
        interaction_needs=[
            InteractionNeed(
                type=InteractionNeedType.CONTRADICTION_REPAIR,
                reason="material contradiction",
                priority=EvidencePriority.CRITICAL,
            )
        ],
    )
    client = FakePlannerClient()
    decision = asyncio.run(
        LangChainInteractionPolicy(client, "test-model").select_action(state, InteractionContract())
    )

    assert client.messages.calls == 1
    assert decision.planning_mode == "model_deliberation"
    assert decision.control_priors.deliberation == "complex_repair"


def test_unavailable_evidence_closes_question_loop_but_preserves_uncertainty():
    from core.problem_formulation.models import FormulationHistoryEntry
    from core.problem_formulation.sufficiency import EvidenceAssessor, ProblemStateReducer

    state = SharedProblemState(
        case_id="case",
        user_id="u",
        conv_id="c",
        turn_count=1,
        evidence_catalog=["server_log"],
        missing_evidence=[
            EvidenceNeed(
                key="server_log",
                description="服务端日志",
                question="能否查看服务端日志？",
                answer_cues=["日志"],
                attempt_count=1,
                last_asked_turn_id=1,
            )
        ],
        formulation_history=[
            FormulationHistoryEntry(
                turn_id=1,
                status=CaseStatus.SEEKING_INFORMATION,
                action=InteractionAction.VERIFY,
                target_evidence="server_log",
            )
        ],
    )
    updated = ProblemStateReducer().merge_problem(
        state,
        TurnInterpretation(
            unavailable_evidence=[
                EvidenceDisposition(
                    key="server_log",
                    evidence_quote="我没有权限看服务端日志",
                    reason="没有服务端权限",
                )
            ]
        ),
        "我没有权限看服务端日志。",
    )
    assessed = EvidenceAssessor().assess(updated)

    assert assessed.missing_evidence[0].status.value == "unavailable"
    assert assessed.readiness.unavailable_evidence == ["server_log"]
    assert "服务端日志" in assessed.readiness.residual_uncertainty


def test_failed_action_is_an_attention_and_experience_prior():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(first_turn()),
        policy=FirstAllowedPolicy(),
    )
    result = asyncio.run(
        service.process(
            message="我想先恢复VPN。VPN连不上，肯定是DNS。我重启八遍了，还是连不上。",
            user_id="u",
            conv_id="failure-prior",
            domain_prior="technical",
        )
    )

    assert "重启" in "".join(result.policy.control_priors.attention_focus)
    assert "重启" in "".join(result.policy.control_priors.experience_refs)
    assert "已经重启八遍" in result.policy.contract.forbidden_actions
    assert result.resolution_context is not None
    assert result.resolution_context["salient_failures"][0]["action"] == "已经重启八遍"
    assert "已经重启八遍" in result.resolution_context["provisional_resolution_policy"][
        "forbidden_retries"
    ]


def test_user_profile_is_a_revisable_case_snapshot_not_a_permanent_label():
    from core.problem_formulation.needs import (
        InteractionContractBuilder,
        ResolutionPolicyBuilder,
    )
    from core.problem_formulation.sufficiency import ProblemStateReducer

    reducer = ProblemStateReducer()
    state = reducer.merge(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            situation_updates=[
                SituationUpdate(field="what", value="VPN 无法连接", evidence_quote="VPN连不上")
            ],
            user_state_signals=[
                UserStateSignal(field="patience", value="low", evidence_quote="我现在很急"),
                UserStateSignal(
                    field="explanation_preference",
                    value="minimal",
                    evidence_quote="先别解释",
                ),
                UserStateSignal(field="deadline", value="十分钟后演示", evidence_quote="十分钟后演示"),
            ],
        ),
        "VPN连不上，我现在很急，十分钟后演示，先别解释。",
    )
    updated = reducer.merge(
        state,
        TurnInterpretation(
            user_state_signals=[
                UserStateSignal(field="patience", value="high", evidence_quote="现在不赶了"),
                UserStateSignal(
                    field="explanation_preference",
                    value="detailed",
                    evidence_quote="想听一下详细原因",
                ),
                UserStateSignal(field="deadline", value="none", evidence_quote="演示取消了"),
            ]
        ),
        "演示取消了，现在不赶了，我想听一下详细原因。",
    )

    contract = InteractionContractBuilder().build(updated)
    policy = ResolutionPolicyBuilder.build(updated, contract)
    handoff = updated.to_resolution_context(policy)["human_interaction_state"]

    assert updated.user_state.patience is CoarseLevel.HIGH
    assert updated.user_state.explanation_preference.value == "detailed"
    assert updated.user_state.deadline is None
    assert {item.field for item in updated.user_state_revisions} == {
        "patience",
        "explanation_preference",
        "deadline",
    }
    assert all(item["is_current"] for item in handoff["current_evidence"])
    assert {item["turn_id"] for item in handoff["current_evidence"]} == {2}
    assert policy.explanation_mode.value == "detailed"
    assert policy.deadline is None
    assert policy.evidence_turn_ids == [2]


def test_m1_handoff_allows_partial_case_and_carries_uncertainty_to_m2():
    from core.problem_formulation.sufficiency import EvidenceAssessor, ProblemStateReducer

    state = ProblemStateReducer().merge_problem(
        SharedProblemState(case_id="case", user_id="u", conv_id="c"),
        TurnInterpretation(
            situation_updates=[
                SituationUpdate(field="what", value="VPN 无法连接", evidence_quote="VPN连不上")
            ],
            missing_evidence=[
                EvidenceNeed(
                    key="error_code",
                    description="客户端错误码",
                    question="错误码是什么？",
                    priority=EvidencePriority.HIGH,
                    blocking=True,
                )
            ],
        ),
        "VPN连不上。",
    )

    assessed = EvidenceAssessor().assess(state)

    assert assessed.status is CaseStatus.CASE_READY
    assert assessed.evidence_sufficiency.value == "partially_sufficient"
    assert assessed.readiness.blocking_evidence == []
    assert assessed.readiness.carried_forward_evidence == ["error_code"]
    assert "uncertainty_carried_forward" in assessed.readiness.reason_codes


def test_meta_route_comes_from_semantic_interpretation():
    service = ProblemFormulationService(
        checkpointer=InMemorySaver(),
        interpreter=QueuedInterpreter(TurnInterpretation(conversation_act=ConversationAct.SOCIAL)),
        policy=FirstAllowedPolicy(),
    )
    result = asyncio.run(
        service.process(
            message="你好",
            user_id="u",
            conv_id="meta",
            domain_prior="other",
        )
    )

    assert result.state.status is CaseStatus.META_HANDLED
    assert result.state.conversation_act is ConversationAct.SOCIAL
    assert result.policy.planning_mode == "semantic_route"
    assert result.m1_model_calls == 1
