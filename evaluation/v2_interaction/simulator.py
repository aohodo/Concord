"""Stateful, deterministic user policy for reproducible interaction tests."""

from __future__ import annotations

import re

from .environment import ControlledEnvironment
from .schemas import EpisodeSpec, HiddenFact


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


class ScriptedUserSimulator:
    """Reveal hidden facts only when the agent asks a relevant, affordable question."""

    def __init__(
        self,
        spec: EpisodeSpec,
        environment: ControlledEnvironment | None = None,
    ):
        self.spec = spec.model_copy(deep=True)
        self.state = self.spec.user_state.model_copy(deep=True)
        self.initial_state = self.state.model_copy(deep=True)
        self.environment = environment or ControlledEnvironment(spec.environment)
        self.no_progress_turns = 0
        self.disengaged = False

    def respond(
        self,
        *,
        turn_number: int,
        system_response: str,
        action: str,
        target_evidence: str,
        contract_violations: list[str],
    ) -> tuple[str, list[str], str, dict]:
        if self.state.deadline_turns is not None:
            self.state.deadline_turns = max(0, self.state.deadline_turns - 1)
            if self.state.deadline_turns == 0:
                self.state.patience = min(self.state.patience, 1)
        if contract_violations:
            self._increase_burden(2)

        correction = self.spec.correction_event
        if correction and correction.after_system_turn == turn_number:
            return correction.message, [], "issued a controlled correction", {}

        pressure = self.spec.negative_pressure
        if pressure and pressure.message and pressure.after_system_turn == turn_number:
            return pressure.message, [], "injected subjective negative pressure", {}

        environmental_fact, observation = self.environment.observe_for_target(
            target_evidence,
            system_response,
            self.spec.hidden_facts,
        )
        if environmental_fact is not None and self.state.effort_budget > 0:
            environmental_fact.revealed = True
            self.state.effort_budget -= 1
            if self.state.frustration > 0:
                self.state.frustration -= 1
            self.no_progress_turns = 0
            return (
                environmental_fact.value,
                [environmental_fact.key],
                "revealed an observation from the controlled environment",
                observation,
            )

        relevant = self._select_fact(
            turn_number=turn_number,
            target_evidence=target_evidence,
            system_response=system_response,
        )
        if relevant is not None and self.state.effort_budget > 0:
            relevant.revealed = True
            self.state.effort_budget -= 1
            if self.state.frustration > 0:
                self.state.frustration -= 1
            self.no_progress_turns = 0
            return relevant.value, [relevant.key], "revealed relevant hidden fact", {}

        if action in {"reflect", "structure"}:
            self.no_progress_turns += 1
            return (
                f"对，我现在想先{self.spec.goal}。",
                [],
                "explicitly grounded the episode goal",
                {},
            )
        if action in {"calibrate", "repair"}:
            self.no_progress_turns += 1
            return "对，你先按这个理解继续问。", [], "acknowledged formulation", {}

        self.no_progress_turns += 1
        self._increase_burden(1)
        if self.state.patience == 0 and self.no_progress_turns >= 5:
            self.disengaged = True
            return (
                "这个流程一直没有进展，请转人工继续处理。",
                [],
                "sustained no-progress caused handoff",
                {},
            )
        if self.state.patience == 0:
            return (
                "我已经没耐心了，请直接告诉我下一步。",
                [],
                "low patience changed cooperation style",
                {},
            )
        return (
            "我不知道你具体想让我补充什么。",
            [],
            "question did not match a hidden fact",
            {},
        )

    def _select_fact(
        self,
        *,
        turn_number: int,
        target_evidence: str,
        system_response: str,
    ) -> HiddenFact | None:
        target = _normalized(target_evidence)
        candidates = [
            fact
            for fact in self.spec.hidden_facts
            if not fact.revealed and turn_number >= fact.reveal_after_turn
        ]
        for fact in candidates:
            if target and target == _normalized(fact.key):
                return fact
        return None

    def _increase_burden(self, amount: int) -> None:
        self.state.frustration = min(4, self.state.frustration + amount)
        self.state.patience = max(0, self.state.patience - amount)

    @property
    def revealed_keys(self) -> set[str]:
        return {fact.key for fact in self.spec.hidden_facts if fact.revealed}

    def mark_revealed(self, keys: set[str]) -> None:
        """Synchronize facts already present in the user's submitted message."""
        for fact in self.spec.hidden_facts:
            if fact.key in keys:
                fact.revealed = True
