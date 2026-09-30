import asyncio

from core.case_orchestration import CaseContextSummary, CasePhase, CaseRunSnapshot
from core.continual_improvement import (
    ContinualImprovementRepository,
    ContinualImprovementService,
    ExperienceApplicability,
    ExperienceExtractor,
    ExperienceKind,
    ExperienceMatcher,
    ExperienceStatus,
    FailureCategory,
    FailureMiner,
    ImprovementProposer,
    OutcomeVerdict,
    OutcomeVerifier,
    applicability_from_context,
)
from core.tools import RuntimeToolProfile
from evaluation.m7_continual_improvement import build_scenarios, evaluate_scenarios


def run(coro):
    return asyncio.run(coro)


def context(case_id: str = "case-target", *, domain: str = "enterprise_it"):
    return {
        "case_id": case_id,
        "domain_prior": domain,
        "goal": {"goal_kind": "restore_access"},
        "situation": {
            "affected_resource": "vpn",
            "observed_state": "authentication_failed",
            "irrelevant_empty": [],
        },
        "evidence_handoff": {
            "resolved": [{"key": "network_reachable", "value": True}],
            "open": [{"key": "credential_validity"}],
            "deferred": [],
            "unavailable": [],
        },
        "action_constraints": {
            "pre_resolution_whitelist": {"operation_modes": ["read_only"]},
            "conditional_operation_modes": {"simulated_write": "confirmation"},
            "restricted_operation_modes": ["real_write"],
        },
    }


def snapshot(
    *,
    case_id: str = "case-source",
    phase: CasePhase = CasePhase.RESOLVED,
    status: str = "resolved",
    verified: bool = True,
    tool_status: str = "succeeded",
    domain: str = "enterprise_it",
    experience_source_ids: list[str] | None = None,
) -> CaseRunSnapshot:
    tool = {
        "tool_id": "simulation_observe",
        "category": "verify",
        "arguments": {"target": "vpn"},
        "status": tool_status,
        "data": {"value": "connected"} if tool_status == "succeeded" else None,
        "evidence": [{"key": "vpn", "value": "connected", "reliability": 1.0}],
    }
    return CaseRunSnapshot(
        case_id=case_id,
        conv_id=f"conv-{case_id}",
        user_id="user-1",
        tenant_id="tenant-1",
        phase=phase,
        status=status,
        response="用户说看起来好了",
        m1_thread_id=f"m1:{case_id}",
        m2_thread_id=f"m2:{case_id}",
        case_revision=2,
        m1={"resolution_context": context(case_id, domain=domain)},
        m2={
            "trace_id": f"trace-{case_id}",
            "status": status,
            "verification_complete": verified,
            "resolution_evidence": ["vpn=connected"] if verified else [],
            "tool_history": [tool],
            "experience_source_ids": experience_source_ids or [],
            "adopted_experience_ids": experience_source_ids or [],
            "provisional_explanations": [{"claim": "cached credentials may be stale"}],
        },
        context_summary=CaseContextSummary(goal={"goal_kind": "restore_access"}),
    )


def test_only_independently_verified_resolution_is_retainable():
    verifier = OutcomeVerifier()
    accepted = verifier.verify(snapshot())
    self_claim = verifier.verify(snapshot(verified=False))
    user_report = verifier.verify(
        snapshot(
            phase=CasePhase.WAITING_FOR_USER,
            status="waiting_for_user",
            verified=False,
        )
    )

    assert accepted.verdict is OutcomeVerdict.VERIFIED_SUCCESS
    assert accepted.eligible_for_experience is True
    assert self_claim.verdict is OutcomeVerdict.UNVERIFIED
    assert user_report.verdict is OutcomeVerdict.UNVERIFIED
    assert "NO_INDEPENDENT_TOOL_OBSERVATION" not in user_report.rejected_reasons


def test_verified_runtime_boundary_and_objective_failure_can_be_learned():
    verifier = OutcomeVerifier()
    boundary = verifier.verify(
        snapshot(
            phase=CasePhase.HUMAN_REQUIRED,
            status="human_required",
            verified=False,
            tool_status="denied",
        )
    )
    failure = verifier.verify(
        snapshot(
            phase=CasePhase.ERROR,
            status="error",
            verified=False,
            tool_status="failed",
        )
    )

    assert boundary.verdict is OutcomeVerdict.VERIFIED_BOUNDARY
    assert boundary.eligible_for_experience is True
    assert failure.verdict is OutcomeVerdict.VERIFIED_FAILURE
    assert failure.eligible_for_experience is True


def test_extractor_keeps_structured_state_and_not_raw_user_wording():
    source = snapshot()
    experience = ExperienceExtractor().extract(source, OutcomeVerifier().verify(source))

    assert experience is not None
    assert experience.kind is ExperienceKind.SUCCESSFUL_PROCEDURE
    assert experience.applicability.domain == "enterprise_it"
    assert "affected_resource" in experience.applicability.situation_keys
    assert "用户说" not in experience.model_dump_json()
    assert experience.strategy[0].tool_id == "simulation_observe"


def test_experience_persistence_uses_tool_allowlist_and_redacts_personal_data():
    source = snapshot()
    tool = source.m2["tool_history"][0]
    tool["arguments"] = {
        "target": "vpn",
        "password": "should-never-persist",
        "contact": "person@example.com",
    }
    tool["experience_safe_arguments"] = {
        "target": "vpn",
        "contact": "person@example.com",
    }
    tool["evidence"] = [
        {"key": "vpn", "value": "person@example.com", "reliability": 1.0}
    ]

    experience = ExperienceExtractor().extract(
        source, OutcomeVerifier().verify(source)
    )

    assert experience is not None
    serialized = experience.model_dump_json()
    assert "should-never-persist" not in serialized
    assert "person@example.com" not in serialized
    assert "[REDACTED_EMAIL]" in serialized


def test_success_outcome_requires_every_declared_user_criterion_binding():
    source = snapshot()
    source.m1["resolution_context"]["goal"]["success_criteria"] = [
        {"value": "vpn=connected"}
    ]

    rejected = OutcomeVerifier().verify(source)
    source.m2["criterion_verifications"] = [
        {"criterion": "vpn=connected", "evidence_refs": ["target:vpn"]}
    ]
    accepted = OutcomeVerifier().verify(source)

    assert rejected.verdict is OutcomeVerdict.UNVERIFIED
    assert "SUCCESS_CRITERIA_NOT_COVERED" in rejected.rejected_reasons
    assert accepted.verdict is OutcomeVerdict.VERIFIED_SUCCESS


def test_structured_match_rejects_cross_domain_and_disjoint_state():
    source = snapshot()
    experience = ExperienceExtractor().extract(source, OutcomeVerifier().verify(source))
    assert experience is not None
    matcher = ExperienceMatcher()

    same = matcher.match(applicability_from_context(context()), experience)
    cross_domain = matcher.match(
        applicability_from_context(context(domain="logistics")), experience
    )
    disjoint = matcher.match(
        ExperienceApplicability(
            domain="enterprise_it",
            goal_kind="restore_access",
            situation_keys={"shipment_temperature"},
        ),
        experience,
    )

    assert same.score >= 0.5
    assert cross_domain.score == 0
    assert cross_domain.mismatches == ["DOMAIN_MISMATCH"]
    assert disjoint.score == 0
    assert disjoint.mismatches == ["STATE_SIGNATURE_DISJOINT"]


def test_known_runtime_boundary_rejects_an_unexecutable_past_strategy():
    source = snapshot()
    tool = source.m2["tool_history"][0]
    tool["tool_capabilities"] = ["vpn:repair"]
    tool["required_permissions"] = ["vpn:write"]
    tool["tool_contract_version"] = "2"
    experience = ExperienceExtractor().extract(source, OutcomeVerifier().verify(source))
    assert experience is not None

    unknown_runtime = ExperienceMatcher().match(
        applicability_from_context(context()), experience
    )
    known_runtime_context = context()
    known_runtime_context.update(
        {
            "runtime_capabilities_known": True,
            "runtime_permissions_known": True,
            "runtime_contract_versions_known": True,
            "available_tool_capabilities": ["vpn:read"],
            "available_permissions": [],
            "tool_contract_versions": {"vpn_reader": "1"},
        }
    )
    known_runtime = ExperienceMatcher().match(
        applicability_from_context(known_runtime_context), experience
    )

    assert unknown_runtime.score >= 0.5
    assert known_runtime.score == 0
    assert known_runtime.mismatches == ["CAPABILITY_CONTRACT_MISMATCH"]


def test_context_enrichment_consumes_runtime_profile_before_retrieval(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        service = ContinualImprovementService(repository)
        try:
            source = snapshot()
            tool = source.m2["tool_history"][0]
            tool["tool_capabilities"] = ["vpn:repair"]
            tool["required_permissions"] = ["vpn:write"]
            tool["tool_contract_version"] = "2"
            await service.observe_case(source)
            return await service.enrich_resolution_context(
                context("target"),
                tenant_id="tenant-1",
                runtime_tool_profile=RuntimeToolProfile(
                    authorized_tool_ids={"vpn_reader"},
                    available_capabilities={"vpn:read"},
                    granted_permissions=set(),
                    tool_contract_versions={"vpn_reader": "1"},
                ),
            )
        finally:
            await repository.close()

    enriched, matches = run(scenario())
    assert matches == []
    assert enriched["candidate_experiences"] == []
    assert enriched["runtime_permissions_known"] is True


def test_repository_is_idempotent_and_filters_non_active_experience(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        try:
            source = snapshot()
            outcome = await repository.save_outcome(OutcomeVerifier().verify(source))
            duplicate = await repository.save_outcome(OutcomeVerifier().verify(source))
            experience = ExperienceExtractor().extract(source, outcome)
            assert experience is not None
            stored = await repository.save_experience(experience)
            await repository.save_experience(experience)
            quarantined = stored.model_copy(
                update={
                    "experience_id": "quarantined-1",
                    "source_case_id": "case-quarantined",
                    "status": ExperienceStatus.QUARANTINED,
                },
                deep=True,
            )
            await repository.save_experience(quarantined)
            active = await repository.list_experiences(tenant_id="tenant-1")
            return outcome, duplicate, active
        finally:
            await repository.close()

    first, second, active = run(scenario())
    assert first.outcome_id == second.outcome_id
    assert len(active) == 1
    assert active[0].status is ExperienceStatus.ACTIVE


def test_service_does_not_retrieve_the_current_case_as_its_own_memory(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        service = ContinualImprovementService(repository)
        try:
            source = snapshot(case_id="case-same")
            await service.observe_case(source)
            same = await service.retrieve(
                context("case-same"), tenant_id="tenant-1"
            )
            other = await service.retrieve(
                context("case-other"), tenant_id="tenant-1"
            )
            return same, other
        finally:
            await repository.close()

    same, other = run(scenario())
    assert same == []
    assert len(other) == 1
    assert other[0].must_reverify is True


def test_reused_experience_gets_outcome_feedback(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        service = ContinualImprovementService(repository)
        try:
            await service.observe_case(snapshot(case_id="source"))
            source = (await repository.list_experiences(tenant_id="tenant-1"))[0]
            await service.observe_case(
                snapshot(case_id="target", experience_source_ids=[source.experience_id])
            )
            experiences = await repository.list_experiences(tenant_id="tenant-1")
            return next(item for item in experiences if item.source_case_id == "source")
        finally:
            await repository.close()

    experience = run(scenario())
    assert experience.reuse_count == 1
    assert experience.supported_reuse_count == 1


def test_legacy_model_self_report_alone_does_not_receive_reuse_credit(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        service = ContinualImprovementService(repository)
        try:
            await service.observe_case(snapshot(case_id="source"))
            source = (await repository.list_experiences(tenant_id="tenant-1"))[0]
            target = snapshot(
                case_id="target", experience_source_ids=[source.experience_id]
            )
            target.m2["adopted_experience_ids"] = []
            await service.observe_case(target)
            experiences = await repository.list_experiences(tenant_id="tenant-1")
            return next(item for item in experiences if item.source_case_id == "source")
        finally:
            await repository.close()

    experience = run(scenario())
    assert experience.reuse_count == 0


def test_failure_miner_detects_structural_failures_without_word_lists():
    source = snapshot(
        phase=CasePhase.ERROR,
        status="error",
        verified=False,
        tool_status="failed",
    )
    failed = source.m2["tool_history"][0]
    source.m2["tool_history"] = [failed, failed]
    source.m2["guardrail_events"] = [{"code": "UNVERIFIED_RESOLUTION_REJECTED"}]
    signals = FailureMiner().mine(source)
    categories = {item.category for item in signals}

    assert FailureCategory.REPEATED_FAILED_ACTION in categories
    assert FailureCategory.VERIFICATION_GAP in categories


def test_proposals_require_recurrence_across_distinct_cases_and_remain_drafts():
    miner = FailureMiner()
    first = snapshot(
        case_id="first",
        phase=CasePhase.ERROR,
        status="error",
        verified=False,
        tool_status="failed",
    )
    second = snapshot(
        case_id="second",
        phase=CasePhase.ERROR,
        status="error",
        verified=False,
        tool_status="failed",
    )
    for item in (first, second):
        failed = item.m2["tool_history"][0]
        item.m2["tool_history"] = [failed, failed]

    assert ImprovementProposer().propose(miner.mine(first)) == []
    proposals = ImprovementProposer().propose(miner.mine(first) + miner.mine(second))

    assert len(proposals) == 1
    assert proposals[0].status.value == "draft"
    assert proposals[0].suggested_artifact["release_policy"] == (
        "isolated_evaluation_then_human_decision"
    )


def test_applicability_handles_nested_situation_values():
    payload = context()
    payload["situation"]["nested"] = {"region": "cn-east"}
    payload["situation"]["sequence"] = ["login", "failure"]

    applicability = applicability_from_context(payload)

    assert {"nested", "sequence"} <= applicability.situation_keys


def test_improvement_lifecycle_requires_evaluation_and_supports_rollback(tmp_path):
    async def scenario():
        repository = ContinualImprovementRepository(tmp_path / "m7.sqlite3")
        await repository.setup()
        service = ContinualImprovementService(repository)
        try:
            signals = []
            for case_id in ("first", "second"):
                item = snapshot(
                    case_id=case_id,
                    phase=CasePhase.ERROR,
                    status="error",
                    verified=False,
                    tool_status="failed",
                )
                failed = item.m2["tool_history"][0]
                item.m2["tool_history"] = [failed, failed]
                signals.extend(FailureMiner().mine(item))
            proposal = ImprovementProposer().propose(signals)[0]
            await repository.save_proposal(proposal)
            try:
                await service.decide_proposal(
                    tenant_id="tenant-1",
                    proposal_id=proposal.proposal_id,
                    actor_id="reviewer",
                    decision="approve",
                    reason="premature",
                    expected_version=proposal.version,
                )
                premature_approval = False
            except ValueError:
                premature_approval = True
            evaluated = await service.record_isolated_evaluation(
                tenant_id="tenant-1",
                proposal_id=proposal.proposal_id,
                evaluator="eval-runner",
                result={"passed": True, "regressions": 0},
                expected_version=proposal.version,
            )
            approved = await service.decide_proposal(
                tenant_id="tenant-1",
                proposal_id=proposal.proposal_id,
                actor_id="reviewer",
                decision="approve",
                reason="held-out controls passed",
                expected_version=evaluated.version,
            )
            rolled_back = await service.decide_proposal(
                tenant_id="tenant-1",
                proposal_id=proposal.proposal_id,
                actor_id="reviewer",
                decision="rollback",
                reason="downstream regression",
                expected_version=approved.version,
            )
            return premature_approval, evaluated, approved, rolled_back
        finally:
            await repository.close()

    premature, evaluated, approved, rolled_back = run(scenario())
    assert premature is True
    assert evaluated.status.value == "evaluating"
    assert approved.status.value == "approved"
    assert rolled_back.status.value == "rolled_back"
    assert rolled_back.version == 4


def test_m7_cross_domain_control_matrix_has_104_scenarios_and_safe_structured_retrieval():
    scenarios = build_scenarios()
    runs = evaluate_scenarios(scenarios)
    structured = [item for item in runs if item.variant == "structured_verified"]
    coarse = [item for item in runs if item.variant == "coarse_structured"]

    assert len(scenarios) == 104
    assert len(runs) == 312
    assert all(item.correct for item in structured)
    assert sum(not item.safe for item in coarse) >= 24
