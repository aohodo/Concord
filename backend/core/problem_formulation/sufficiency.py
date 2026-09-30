"""Deterministic merge and readiness rules for M1."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    AcquisitionActor,
    ActionRelevance,
    ActionResult,
    CaseStatus,
    ClaimSource,
    ClaimType,
    CoarseLevel,
    CollaborationPreference,
    CommonGroundStatus,
    DeliveryPreference,
    EpistemicClaim,
    EvidenceNeed,
    EvidenceNeedStatus,
    EvidencePriority,
    EvidenceSufficiency,
    ExplanationPreference,
    GoalKind,
    GoalRevision,
    GoalStatus,
    GroundedValue,
    IssueCandidate,
    IssueRelation,
    IssueStatus,
    ProgressPreference,
    ReadinessAssessment,
    ReportedIssue,
    SharedProblemState,
    TurnInterpretation,
    UserStateEvidence,
    UserStateRevision,
    VerificationStatus,
)

_SITUATION_FIELDS = {"who", "what", "when", "where", "how", "impact"}
_USER_STATE_FIELDS = {
    "domain_knowledge",
    "clarity",
    "frustration",
    "effort_willingness",
    "perceived_controllability",
    "patience",
}
_PREFERENCE_FIELDS = {
    "delivery_preference": DeliveryPreference,
    "explanation_preference": ExplanationPreference,
    "collaboration_preference": CollaborationPreference,
    "progress_preference": ProgressPreference,
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _quote_is_supported(quote: str, message: str) -> bool:
    return bool(quote and _normalized(quote) in _normalized(message))


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = _normalized(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value.strip())
    return result


class ProblemStateReducer:
    """Merge only source-grounded candidates into durable case state."""

    @staticmethod
    def _next_issue_id(state: SharedProblemState) -> str:
        used = {item.issue_id for item in state.reported_issues}
        index = len(used) + 1
        while f"issue-{index}" in used:
            index += 1
        return f"issue-{index}"

    @staticmethod
    def _migrate_issue_identity(state: SharedProblemState) -> None:
        """Upgrade persisted pre-issue snapshots without changing their meaning."""
        used = {item.issue_id for item in state.reported_issues if item.issue_id}
        next_index = 1
        for item in state.reported_issues:
            if item.issue_id:
                continue
            while f"issue-{next_index}" in used:
                next_index += 1
            item.issue_id = f"issue-{next_index}"
            used.add(item.issue_id)
        if state.active_issue_id is None and state.reported_issues:
            active = next(
                (
                    item
                    for item in state.reported_issues
                    if item.status is IssueStatus.ACTIVE
                ),
                state.reported_issues[0],
            )
            active.status = IssueStatus.ACTIVE
            state.active_issue_id = active.issue_id
            for item in state.reported_issues:
                if item.issue_id != active.issue_id and item.status is IssueStatus.ACTIVE:
                    item.status = IssueStatus.DEFERRED

    @staticmethod
    def _set_active_issue(
        state: SharedProblemState,
        issue_id: str,
        turn_id: int,
    ) -> None:
        if state.active_issue_id == issue_id:
            return
        for item in state.reported_issues:
            if item.issue_id == issue_id:
                item.status = IssueStatus.ACTIVE
                state.active_issue_id = issue_id
            elif item.status is IssueStatus.ACTIVE:
                item.status = IssueStatus.DEFERRED
        state.last_focus_change_turn_id = turn_id

    def _merge_issue(
        self,
        state: SharedProblemState,
        candidate: IssueCandidate,
        message: str,
        turn_id: int,
    ) -> str | None:
        if not _quote_is_supported(candidate.evidence_quote, message):
            return None
        existing = next(
            (
                item
                for item in state.reported_issues
                if candidate.existing_issue_id
                and item.issue_id == candidate.existing_issue_id
            ),
            None,
        )
        if existing is None:
            existing = next(
                (
                    item
                    for item in state.reported_issues
                    if _normalized(item.summary) == _normalized(candidate.summary)
                ),
                None,
            )
        if existing is None:
            issue_id = self._next_issue_id(state)
            existing = ReportedIssue(
                issue_id=issue_id,
                summary=candidate.summary.strip(),
                evidence_quote=candidate.evidence_quote,
                first_seen_turn_id=turn_id,
                last_seen_turn_id=turn_id,
                status=(
                    IssueStatus.ACTIVE
                    if state.active_issue_id is None
                    else IssueStatus.DEFERRED
                ),
                relation=candidate.relation,
                related_issue_id=candidate.related_issue_id,
                priority_basis=candidate.priority_basis,
            )
            state.reported_issues.append(existing)
            if state.active_issue_id is None:
                state.active_issue_id = issue_id
        else:
            existing.last_seen_turn_id = turn_id
            if candidate.relation is not IssueRelation.UNKNOWN:
                existing.relation = candidate.relation
            if candidate.related_issue_id:
                existing.related_issue_id = candidate.related_issue_id
            if candidate.priority_basis:
                existing.priority_basis = candidate.priority_basis
        if candidate.make_active:
            self._set_active_issue(state, existing.issue_id, turn_id)
        return existing.issue_id

    @staticmethod
    def _issue_for_quote(state: SharedProblemState, quote: str) -> str | None:
        normalized = _normalized(quote)
        return next(
            (
                item.issue_id
                for item in reversed(state.reported_issues)
                if normalized
                and (
                    normalized in _normalized(item.evidence_quote)
                    or _normalized(item.evidence_quote) in normalized
                )
            ),
            state.active_issue_id,
        )

    @staticmethod
    def _record_goal_revision(
        state: SharedProblemState,
        new_outcome: str,
        evidence_quote: str,
        turn_id: int,
    ) -> None:
        previous = state.goal.current_outcome
        if previous and _normalized(previous) == _normalized(new_outcome):
            return
        if previous:
            state.goal.revision_history.append(
                GoalRevision(
                    previous_outcome=previous,
                    new_outcome=new_outcome,
                    evidence_quote=evidence_quote,
                    turn_id=turn_id,
                )
            )
        state.goal.current_outcome = new_outcome
        if state.goal.original_outcome is None:
            state.goal.original_outcome = new_outcome

    def merge_problem(
        self,
        state: SharedProblemState,
        interpretation: TurnInterpretation,
        message: str,
    ) -> SharedProblemState:
        updated = state.model_copy(deep=True)
        updated.turn_count += 1
        turn_id = updated.turn_count
        self._migrate_issue_identity(updated)
        legacy_outcome = updated.goal.explicit_goal or updated.goal.inferred_goal
        if updated.goal.current_outcome is None and legacy_outcome:
            updated.goal.current_outcome = legacy_outcome
        if updated.goal.original_outcome is None and legacy_outcome:
            updated.goal.original_outcome = legacy_outcome
        updated.unmapped_user_spans = list(interpretation.unmapped_spans)
        if updated.initial_problem_report is None:
            updated.initial_problem_report = message.strip()
        goal = interpretation.goal
        # An inferred goal is only a working proposal.  The user's next grounded
        # outcome must be allowed to confirm or correct it without relying on the
        # model to also set a special replacement flag.
        has_explicit_goal = bool(updated.goal.explicit_goal)
        same_explicit_goal = bool(
            goal.explicit_goal
            and updated.goal.current_outcome
            and _normalized(goal.explicit_goal) == _normalized(updated.goal.current_outcome)
        )
        may_replace_goal = (
            not has_explicit_goal or goal.replaces_current_goal or same_explicit_goal
        )
        grounded_explicit = bool(
            goal.operational
            and goal.goal_kind is GoalKind.OUTCOME
            and goal.explicit_goal
            and goal.explicit_goal_quote
            and _quote_is_supported(goal.explicit_goal_quote, message)
        )
        if grounded_explicit and may_replace_goal:
            explicit_outcome = goal.explicit_goal.strip()
            self._record_goal_revision(
                updated,
                explicit_outcome,
                goal.explicit_goal_quote or explicit_outcome,
                turn_id,
            )
            updated.goal.explicit_goal = explicit_outcome
            updated.goal.inferred_goal = goal.inferred_goal or updated.goal.inferred_goal
            updated.goal.common_ground = CommonGroundStatus.EXPLICITLY_GROUNDED
            updated.goal.reflected_turn_id = None
            updated.goal.acknowledged_turn_id = None
            updated.goal.last_grounded_turn_id = turn_id
        elif (
            goal.operational
            and goal.goal_kind is GoalKind.OUTCOME
            and goal.inferred_goal
            and may_replace_goal
        ):
            updated.goal.inferred_goal = goal.inferred_goal.strip()
            if updated.goal.current_outcome is None:
                updated.goal.current_outcome = updated.goal.inferred_goal
            if updated.goal.original_outcome is None:
                updated.goal.original_outcome = updated.goal.inferred_goal
        if goal.operational and goal.goal_kind is GoalKind.OUTCOME:
            updated.goal.scope_constraints = _dedupe_strings(
                [*updated.goal.scope_constraints, *goal.scope_constraints]
            )
            updated.goal.unresolved_ambiguity = _dedupe_strings(goal.ambiguities)
        existing_criteria = {
            _normalized(item.value) for item in updated.goal.success_criteria
        }
        for criterion in goal.success_criteria:
            if not _quote_is_supported(criterion.evidence_quote, message):
                continue
            key = _normalized(criterion.value)
            if not key or key in existing_criteria:
                continue
            updated.goal.success_criteria.append(
                GroundedValue(
                    value=criterion.value.strip(),
                    evidence_quote=criterion.evidence_quote,
                    turn_id=turn_id,
                )
            )
            existing_criteria.add(key)
        acknowledgement = interpretation.goal_acknowledgement
        if acknowledgement and _quote_is_supported(acknowledgement.evidence_quote, message):
            if acknowledgement.accepted and updated.goal.reflected_turn_id is not None:
                updated.goal.common_ground = CommonGroundStatus.MUTUALLY_ACKNOWLEDGED
                updated.goal.acknowledged_turn_id = turn_id
                updated.goal.last_grounded_turn_id = turn_id
                if not updated.goal.explicit_goal and updated.goal.inferred_goal:
                    updated.goal.explicit_goal = updated.goal.inferred_goal
                    updated.goal.status = GoalStatus.CONFIRMED
            elif not acknowledgement.accepted:
                updated.goal.common_ground = CommonGroundStatus.REPAIRED
                updated.goal.acknowledged_turn_id = turn_id
                updated.goal.last_grounded_turn_id = turn_id
                if acknowledgement.corrected_goal:
                    corrected = acknowledgement.corrected_goal.strip()
                    self._record_goal_revision(
                        updated,
                        corrected,
                        acknowledgement.evidence_quote,
                        turn_id,
                    )
                    updated.goal.explicit_goal = corrected
                    updated.goal.status = GoalStatus.CONFIRMED

        issue_candidates = list(interpretation.reported_issues)
        has_explicit_issue_candidates = bool(issue_candidates)
        for candidate in interpretation.situation_updates:
            field = candidate.field.casefold()
            if field not in _SITUATION_FIELDS:
                continue
            if not _quote_is_supported(candidate.evidence_quote, message):
                continue
            value = candidate.value.strip()
            if field == "what":
                # ``reported_issues`` is the authoritative issue extraction.
                # A ``what`` update is only a backward-compatible fallback;
                # promoting both creates duplicate issues from the same span.
                if not has_explicit_issue_candidates:
                    issue_candidates.append(
                        IssueCandidate(
                            summary=value,
                            evidence_quote=candidate.evidence_quote,
                        )
                    )
                continue
            setattr(updated.situation, field, value)

        for candidate in issue_candidates:
            self._merge_issue(updated, candidate, message, turn_id)
        active_issue = next(
            (
                item
                for item in updated.reported_issues
                if item.issue_id == updated.active_issue_id
            ),
            None,
        )
        if active_issue is not None:
            updated.situation.what = active_issue.summary

        existing_claims = {(_normalized(item.content), item.type.value) for item in updated.claims}
        for candidate in interpretation.claims:
            if not _quote_is_supported(candidate.evidence_quote, message):
                continue
            key = (_normalized(candidate.content), candidate.type.value)
            if key in existing_claims:
                continue
            claim = EpistemicClaim(
                content=candidate.content.strip(),
                type=candidate.type,
                source=ClaimSource.USER,
                verification_status=VerificationStatus.REPORTED,
                turn_id=turn_id,
                evidence_quote=candidate.evidence_quote,
                issue_id=(
                    candidate.issue_id
                    if candidate.issue_id
                    and any(
                        item.issue_id == candidate.issue_id
                        for item in updated.reported_issues
                    )
                    else self._issue_for_quote(updated, candidate.evidence_quote)
                ),
            )
            updated.claims.append(claim)
            existing_claims.add(key)
            if claim.type is ClaimType.ACTION:
                updated.action_result_ledger.append(
                    ActionResult(
                        action=claim.content,
                        target=candidate.target,
                        conditions=candidate.conditions,
                        expected_result=candidate.expected_result,
                        reported_result=candidate.reported_result,
                        outcome=candidate.outcome,
                        evidence_quote=candidate.evidence_quote,
                        issue_id=claim.issue_id,
                        repetitions=candidate.repetitions,
                        retry_condition=candidate.retry_condition,
                        turn_id=turn_id,
                    )
                )

        for revision in interpretation.claim_revisions:
            if not _quote_is_supported(revision.evidence_quote, message):
                continue
            original = next(
                (
                    item
                    for item in updated.claims
                    if _normalized(item.content) == _normalized(revision.original_content)
                    and item.verification_status is not VerificationStatus.CONTESTED
                ),
                None,
            )
            if original is None:
                continue
            original.verification_status = VerificationStatus.CONTESTED
            original.revised_turn_id = turn_id
            if original.type is ClaimType.ACTION:
                for action in updated.action_result_ledger:
                    if (
                        _normalized(action.action) == _normalized(original.content)
                        and action.relevance is ActionRelevance.ACTIVE
                    ):
                        action.relevance = ActionRelevance.CORRECTED
            replacement_key = (_normalized(revision.replacement_content), original.type.value)
            if replacement_key not in existing_claims:
                updated.claims.append(
                    EpistemicClaim(
                        content=revision.replacement_content.strip(),
                        type=original.type,
                        source=ClaimSource.USER,
                        verification_status=VerificationStatus.REPORTED,
                        turn_id=turn_id,
                        evidence_quote=revision.evidence_quote,
                        issue_id=original.issue_id,
                    )
                )
                existing_claims.add(replacement_key)

        # A problem report normally carries an implicit outcome: make the observed
        # problem stop.  Keep that outcome explicitly *inferred* until the user
        # acknowledges it; this avoids both an empty goal loop and an invented,
        # over-specific business objective.
        if not updated.goal.explicit_goal and not updated.goal.inferred_goal:
            observed_problem = updated.situation.what or next(
                (
                    item.content
                    for item in updated.claims
                    if item.type is ClaimType.OBSERVATION
                    and item.verification_status is not VerificationStatus.CONTESTED
                ),
                None,
            )
            if observed_problem:
                updated.goal.inferred_goal = f"解决当前问题：{observed_problem}"
                updated.goal.current_outcome = updated.goal.inferred_goal
                if updated.goal.original_outcome is None:
                    updated.goal.original_outcome = updated.goal.inferred_goal

        # A source-grounded explicit outcome outranks model-proposed secondary
        # ambiguities.  The ambiguities remain visible, but cannot reopen goal
        # alignment unless the user did not actually state an operational goal.
        if updated.goal.explicit_goal:
            updated.goal.status = GoalStatus.CONFIRMED
        elif updated.goal.unresolved_ambiguity:
            updated.goal.status = GoalStatus.AMBIGUOUS
        elif updated.goal.inferred_goal:
            updated.goal.status = GoalStatus.INFERRED
        else:
            updated.goal.status = GoalStatus.UNKNOWN

        updated.missing_evidence = self._merge_evidence_needs(
            updated.missing_evidence,
            interpretation.missing_evidence,
            interpretation,
            message,
            turn_id,
            updated.evidence_catalog,
            updated.active_issue_id,
        )
        resolved_contradictions = {
            _normalized(item.key)
            for item in interpretation.resolved_contradictions
            if _quote_is_supported(item.evidence_quote, message)
        }
        updated.contradictions = _dedupe_strings(
            [
                *(
                    item
                    for item in updated.contradictions
                    if _normalized(item) not in resolved_contradictions
                ),
                *interpretation.contradictions,
            ]
        )
        return updated

    def merge_user_state(
        self,
        state: SharedProblemState,
        interpretation: TurnInterpretation,
        message: str,
    ) -> SharedProblemState:
        updated = state.model_copy(deep=True)

        def record(field: str, value: str, evidence_quote: str) -> None:
            previous = next(
                (
                    item
                    for item in reversed(updated.user_state_evidence)
                    if item.field == field and item.is_current
                ),
                None,
            )
            if previous is not None:
                previous.is_current = False
                if previous.value != value:
                    updated.user_state_revisions.append(
                        UserStateRevision(
                            field=field,
                            previous_value=previous.value,
                            new_value=value,
                            evidence_quote=evidence_quote,
                            turn_id=updated.turn_count,
                        )
                    )
            updated.user_state_evidence.append(
                UserStateEvidence(
                    field=field,
                    value=value,
                    evidence_quote=evidence_quote,
                    turn_id=updated.turn_count,
                    supersedes_turn_id=(previous.turn_id if previous else None),
                )
            )

        for signal in interpretation.user_state_signals:
            field = signal.field.casefold()
            if field == "deadline":
                if _quote_is_supported(signal.evidence_quote, message):
                    value = signal.value.strip()
                    cleared = value.casefold() in {"none", "no_deadline", "cleared"}
                    updated.user_state.deadline = None if cleared else value
                    record(field, "none" if cleared else value, signal.evidence_quote)
                continue
            if field not in _USER_STATE_FIELDS:
                preference_type = _PREFERENCE_FIELDS.get(field)
                if preference_type is None:
                    continue
                if not _quote_is_supported(signal.evidence_quote, message):
                    continue
                try:
                    value = preference_type(signal.value.casefold())
                except ValueError:
                    continue
                setattr(updated.user_state, field, value)
                record(field, value.value, signal.evidence_quote)
                continue
            if not _quote_is_supported(signal.evidence_quote, message):
                continue
            try:
                value = CoarseLevel(signal.value.casefold())
                setattr(updated.user_state, field, value)
                record(field, value.value, signal.evidence_quote)
            except ValueError:
                continue
        return updated

    def merge(
        self,
        state: SharedProblemState,
        interpretation: TurnInterpretation,
        message: str,
    ) -> SharedProblemState:
        """Compatibility helper for callers that do not need separate graph nodes."""
        problem = self.merge_problem(state, interpretation, message)
        return self.merge_user_state(problem, interpretation, message)

    @staticmethod
    def _dedupe_needs(needs: list[EvidenceNeed]) -> list[EvidenceNeed]:
        seen = set()
        result = []
        for need in needs:
            normalized_key = need.key.strip().casefold()
            identity = (need.issue_id or "", normalized_key)
            if not normalized_key or identity in seen:
                continue
            seen.add(identity)
            result.append(need)
        priority_order = {
            EvidencePriority.CRITICAL: 0,
            EvidencePriority.HIGH: 1,
            EvidencePriority.MEDIUM: 2,
            EvidencePriority.LOW: 3,
        }
        return sorted(result, key=lambda item: priority_order[item.priority])

    def _merge_evidence_needs(
        self,
        existing: list[EvidenceNeed],
        proposed: list[EvidenceNeed],
        interpretation: TurnInterpretation,
        message: str,
        turn_id: int,
        evidence_catalog: list[str],
        active_issue_id: str | None,
    ) -> list[EvidenceNeed]:
        """Keep open needs until the user supplies grounded resolving evidence."""
        resolved = {
            (item.issue_id or active_issue_id, item.key.strip().casefold()): item.evidence_quote
            for item in interpretation.resolved_evidence
            if _quote_is_supported(item.evidence_quote, message)
        }
        unavailable = {
            (item.issue_id or active_issue_id, item.key.strip().casefold()): (
                item.evidence_quote,
                item.reason,
            )
            for item in interpretation.unavailable_evidence
            if _quote_is_supported(item.evidence_quote, message)
        }
        merged: dict[tuple[str | None, str], EvidenceNeed] = {}
        for need in existing:
            item = need.model_copy(deep=True)
            if item.issue_id is None and active_issue_id is not None:
                item.issue_id = active_issue_id
            key = item.key.strip().casefold()
            identity = (item.issue_id, key)
            # An explicit inability to obtain the requested evidence is itself
            # a grounded answer about acquisition. It must not be mistaken for
            # the requested evidence merely because both mention the same cue.
            if identity in unavailable and item.status is EvidenceNeedStatus.OPEN:
                item.status = EvidenceNeedStatus.UNAVAILABLE
                item.resolved_turn_id = turn_id
                item.resolution_quote, item.unavailable_reason = unavailable[identity]
            elif identity in resolved and item.status is EvidenceNeedStatus.OPEN:
                item.status = EvidenceNeedStatus.RESOLVED
                item.resolved_turn_id = turn_id
                item.resolution_quote = resolved[identity]
            merged[identity] = item
        catalog = {key.strip().casefold(): key.strip() for key in evidence_catalog}
        for need in proposed:
            key = need.key.strip().casefold()
            if not key:
                continue
            if catalog and key not in catalog:
                continue
            if key in catalog:
                need = need.model_copy(update={"key": catalog[key]})
            issue_id = need.issue_id or active_issue_id
            identity = (issue_id, key)
            if identity in merged:
                current = merged[identity]
                if current.status is EvidenceNeedStatus.OPEN:
                    current.description = need.description
                    current.question = need.question
                    current.priority = need.priority
                    current.user_burden = need.user_burden
                    current.answer_cues = need.answer_cues
                continue
            item = need.model_copy(deep=True)
            if item.issue_id is None:
                item.issue_id = active_issue_id
            item.introduced_turn_id = turn_id
            if item.acquisition_actor is AcquisitionActor.LATER_STAGE:
                item.status = EvidenceNeedStatus.DEFERRED
            merged[identity] = item
        return self._dedupe_needs(list(merged.values()))


class EvidenceAssessor:
    """Decide whether M2 can start, not whether M1's internal record is complete."""

    def assess(self, state: SharedProblemState) -> SharedProblemState:
        updated = state.model_copy(deep=True)
        active_issue_id = updated.active_issue_id

        def applies_to_active(issue_id: str | None) -> bool:
            return issue_id is None or issue_id == active_issue_id

        open_user_evidence = [
            need.key
            for need in updated.missing_evidence
            if need.status is EvidenceNeedStatus.OPEN
            and need.blocking
            and need.acquisition_actor is not AcquisitionActor.LATER_STAGE
            and applies_to_active(need.issue_id)
        ]
        unavailable = [
            need.key
            for need in updated.missing_evidence
            if need.status is EvidenceNeedStatus.UNAVAILABLE
            and applies_to_active(need.issue_id)
        ]
        deferred = [
            need.key
            for need in updated.missing_evidence
            if need.status is EvidenceNeedStatus.DEFERRED
            and applies_to_active(need.issue_id)
        ]
        residual = [
            need.description
            for need in updated.missing_evidence
            if need.status in {EvidenceNeedStatus.OPEN, EvidenceNeedStatus.UNAVAILABLE}
            and applies_to_active(need.issue_id)
        ]
        residual.extend(f"未完全映射的用户原话：{item}" for item in updated.unmapped_user_spans)
        goal_anchor = bool(updated.goal.explicit_goal or updated.goal.inferred_goal)
        grounded_claim_observation = any(
            claim.type is ClaimType.OBSERVATION
            and claim.verification_status is not VerificationStatus.CONTESTED
            and applies_to_active(claim.issue_id)
            for claim in updated.claims
        )
        grounded_contract_observation = any(
            need.status is EvidenceNeedStatus.RESOLVED and bool(need.resolution_quote)
            and applies_to_active(need.issue_id)
            for need in updated.missing_evidence
        )
        # A resolved Evidence Contract is already a source-grounded observation.
        # Readiness must not depend on the model redundantly copying that same
        # fact into SituationFrame.what or an EpistemicClaim.
        has_observation = grounded_claim_observation or grounded_contract_observation
        problem_anchor = bool(updated.active_issue_id) or has_observation
        reasons: list[str] = []
        if not goal_anchor:
            updated.status = CaseStatus.ALIGNING_GOAL
            updated.evidence_sufficiency = EvidenceSufficiency.INSUFFICIENT
            reasons.append("no_actionable_goal_anchor")
        elif not problem_anchor:
            updated.status = CaseStatus.RECONSTRUCTING_SITUATION
            updated.evidence_sufficiency = EvidenceSufficiency.INSUFFICIENT
            reasons.append("no_actionable_problem_anchor")
        else:
            updated.status = CaseStatus.CASE_READY
            carries_uncertainty = bool(
                open_user_evidence
                or unavailable
                or deferred
                or updated.contradictions
                or updated.unmapped_user_spans
                or updated.goal.unresolved_ambiguity
            )
            updated.evidence_sufficiency = (
                EvidenceSufficiency.PARTIALLY_SUFFICIENT
                if carries_uncertainty
                else EvidenceSufficiency.SUFFICIENT
            )
            reasons.extend(
                [
                    "minimum_actionable_goal_available",
                    "minimum_actionable_problem_available",
                    "uncertainty_carried_forward"
                    if carries_uncertainty
                    else "no_known_material_uncertainty",
                ]
            )
        updated.readiness = ReadinessAssessment(
            ready=updated.status is CaseStatus.CASE_READY,
            reason_codes=reasons,
            blocking_evidence=(
                [] if updated.status is CaseStatus.CASE_READY else open_user_evidence
            ),
            carried_forward_evidence=(
                open_user_evidence if updated.status is CaseStatus.CASE_READY else []
            ),
            unavailable_evidence=unavailable,
            deferred_evidence=deferred,
            residual_uncertainty=residual,
        )
        return updated
