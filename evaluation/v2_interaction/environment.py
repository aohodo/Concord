"""Deterministic closed-world environments for objective agent evaluation."""

from __future__ import annotations

import re
from typing import Any

from .schemas import EnvironmentAction, EnvironmentSpec, HiddenFact


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


class ControlledEnvironment:
    def __init__(self, spec: EnvironmentSpec | None):
        self.spec = spec
        self.state = dict(spec.initial_state) if spec else {}
        self.observation_log: list[dict[str, Any]] = []
        self.action_log: list[str] = []

    def observe_for_target(
        self,
        target: str,
        response: str,
        hidden_facts: list[HiddenFact],
    ) -> tuple[HiddenFact | None, dict[str, Any]]:
        if self.spec is None:
            return None, {}
        target_text = _normalized(target)
        for fact in hidden_facts:
            if fact.revealed or fact.key not in self.spec.observable_keys:
                continue
            # target_evidence is a structured protocol key supplied by M1.
            # Never reinterpret user-facing prose with keyword aliases here.
            if target_text != _normalized(fact.key):
                continue
            value = self.state.get(fact.key, fact.value)
            observation = {fact.key: value}
            self.observation_log.append(observation)
            return fact, observation
        return None, {}

    def execute_from_response(self, response: str) -> str | None:
        if self.spec is None:
            return None
        selected = {
            item.casefold()
            for item in re.findall(r"<ACTION:([A-Za-z0-9_.-]+)>", response)
        }
        matches = [
            action
            for action in self.spec.actions
            if action.safe
            and action.action_id.casefold() in selected
            and self._requirements_met(action)
        ]
        if not matches:
            return None
        action = matches[0]
        self.state.update(action.effects)
        self.action_log.append(action.action_id)
        return action.action_id

    def unsafe_actions_mentioned(self, response: str) -> list[str]:
        if self.spec is None:
            return []
        selected = {
            item.casefold()
            for item in re.findall(r"<ACTION:([A-Za-z0-9_.-]+)>", response)
        }
        return [
            action.action_id
            for action in self.spec.actions
            if not action.safe and action.action_id.casefold() in selected
        ]

    def final_state_matches(self) -> bool:
        if self.spec is None:
            return False
        return all(
            self.state.get(key) == value
            for key, value in self.spec.expected_final_state.items()
        )

    def _requirements_met(self, action: EnvironmentAction) -> bool:
        return all(
            self.state.get(key) == value for key, value in action.requires.items()
        )
