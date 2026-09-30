"""Pure transformations from ordered Case events to an M2 ResolutionContext."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from typing import Any

from .models import CaseEvent, CaseEventType, DecisionEvent


def _deduplicate(items: list[Any]) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    for item in items:
        canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if canonical not in seen:
            output.append(copy.deepcopy(item))
            seen.add(canonical)
    return output


def _evidence_identity(item: Any) -> str:
    if isinstance(item, dict) and item.get("key"):
        return f"{item.get('issue_id', '')}:{item['key']}"
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)


def _merge_evidence_lifecycle(
    previous: dict[str, Any],
    current: dict[str, Any],
    additions: list[dict[str, Any]],
) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Build one current disposition per evidence identity and retain transitions."""

    buckets = ("open", "deferred", "unavailable", "resolved")
    latest: dict[str, tuple[str, Any]] = {}
    history = list(previous.get("status_history", []))

    def consume(source: dict[str, Any]) -> None:
        for bucket in buckets:
            for item in source.get(bucket, []):
                identity = _evidence_identity(item)
                prior = latest.get(identity)
                if prior is not None and prior[0] != bucket:
                    history.append(
                        {
                            "identity": identity,
                            "from": prior[0],
                            "to": bucket,
                        }
                    )
                latest[identity] = (bucket, copy.deepcopy(item))

    consume(previous)
    consume(current)
    if additions:
        consume({"resolved": additions})

    result = {bucket: [] for bucket in buckets}
    for bucket, item in latest.values():
        result[bucket].append(item)
    return result, _deduplicate(history)


def merge_resolution_context(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    additional_evidence: list[dict[str, Any]] | None = None,
    user_state_update: dict[str, Any] | None = None,
    events: Iterable[CaseEvent] = (),
) -> tuple[dict[str, Any], DecisionEvent | None, bool, list[str]]:
    """Merge new facts without converting reports or hypotheses into verified facts."""

    merged = copy.deepcopy(previous or current)
    for key, value in current.items():
        if key not in {"evidence_handoff", "user_state", "user_state_snapshot"}:
            merged[key] = copy.deepcopy(value)

    evidence = merged.setdefault("evidence_handoff", {})
    current_evidence = current.get("evidence_handoff", {})

    additions = list(additional_evidence or [])
    user_updates = dict(user_state_update or {})
    goal_updates: list[dict[str, Any]] = []
    event_ids: list[str] = []
    cancelled = False
    last_decision_event: DecisionEvent | None = None
    raw_messages: list[str] = []

    for event in sorted(events, key=lambda item: item.sequence):
        event_ids.append(event.event_id)
        if event.type is CaseEventType.CANCEL:
            cancelled = True
            last_decision_event = DecisionEvent.USER_CANCELLED
        elif event.type is CaseEventType.GOAL_UPDATE:
            goal_updates.append(copy.deepcopy(event.payload))
            last_decision_event = DecisionEvent.USER_GOAL_UPDATED
        elif event.type is CaseEventType.USER_STATE_UPDATE:
            user_updates.update(copy.deepcopy(event.payload.get("state", event.payload)))
            last_decision_event = DecisionEvent.USER_EVIDENCE_UPDATED
        elif event.type is CaseEventType.ENVIRONMENT_UPDATE:
            additions.extend(copy.deepcopy(event.payload.get("evidence", [])))
            last_decision_event = DecisionEvent.ENVIRONMENT_UPDATED
        elif event.type is CaseEventType.USER_EVIDENCE:
            additions.extend(copy.deepcopy(event.payload.get("evidence", [])))
            raw_messages.extend(str(item) for item in event.payload.get("raw_messages", []))
            last_decision_event = DecisionEvent.USER_EVIDENCE_UPDATED

    if raw_messages:
        additions.append(
            {
                "key": "user_message_burst",
                "value": {"messages": raw_messages},
                "epistemic_status": "reported_observation",
                "source": "user",
            }
        )
    canonical_evidence, evidence_history = _merge_evidence_lifecycle(
        evidence,
        current_evidence,
        additions,
    )
    evidence.clear()
    evidence.update(canonical_evidence)
    evidence["status_history"] = evidence_history

    # M1's canonical key is user_state. Keep the historical alias synchronized
    # because older evaluation contexts used user_state_snapshot.
    user_state = dict(
        merged.get("user_state")
        or merged.get("user_state_snapshot")
        or merged.get("human_interaction_state", {}).get("current_snapshot", {})
    )
    current_user_state = (
        current.get("user_state")
        or current.get("user_state_snapshot")
        or current.get("human_interaction_state", {}).get("current_snapshot", {})
    )
    user_state.update(copy.deepcopy(current_user_state))
    user_state.update(user_updates)
    merged["user_state"] = user_state
    merged["user_state_snapshot"] = copy.deepcopy(user_state)
    if "human_interaction_state" in merged:
        merged["human_interaction_state"]["current_snapshot"] = copy.deepcopy(user_state)

    for update in goal_updates:
        goal = dict(merged.get("goal", {}))
        if update.get("explicit_goal"):
            goal["explicit_goal"] = update["explicit_goal"]
        if "success_criteria" in update:
            goal["success_criteria"] = [
                item if isinstance(item, dict) else {"value": item}
                for item in update["success_criteria"]
            ]
        merged["goal"] = goal

    explanation_preference = str(user_state.get("explanation_preference", ""))
    selected_mode = (
        explanation_preference
        if explanation_preference in {"minimal", "on_demand", "detailed"}
        else None
    )
    if user_state.get("delivery_preference") == "result_first":
        selected_mode = "minimal"
    if selected_mode:
        policy = dict(merged.get("provisional_resolution_policy", {}))
        policy["explanation_mode"] = selected_mode
        merged["provisional_resolution_policy"] = policy
    return merged, last_decision_event, cancelled, event_ids


__all__ = ["merge_resolution_context"]
