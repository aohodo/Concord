"""M4 multi-turn Episode contracts loaded by the API-level evaluator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from evaluation.m2_resolution.catalog import M2FaultSpec
from infrastructure.simulation import ScenarioActionRule, ScenarioObservationRule

DEFAULT_CATALOG = Path(__file__).with_name("data") / "m4_end_to_end_episodes.json"


class M4Turn(BaseModel):
    message: str
    purpose: str
    wait_for_async_completion: bool = False


class M4Expected(BaseModel):
    allowed_case_statuses: set[str] = Field(default_factory=lambda: {"resolved"})
    require_m2: bool = True
    require_m3: bool = False
    success_criteria: list[str] = Field(default_factory=list)


class M4Episode(BaseModel):
    episode_id: str
    case_id: str
    conv_id: str
    user_id: str
    domain: str
    title: str
    user_condition: str
    turns: list[M4Turn]
    visible_state: dict[str, Any]
    hidden_state: dict[str, Any] = Field(default_factory=dict)
    action_rules: list[ScenarioActionRule] = Field(default_factory=list)
    observation_rules: list[ScenarioObservationRule] = Field(default_factory=list)
    faults: list[M2FaultSpec] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=lambda: ["simulation:act"])
    goal: str
    tags: set[str] = Field(default_factory=set)
    expected: M4Expected

    def evaluation_context(self) -> dict[str, Any]:
        """Expose runtime affordances, never evaluator answers or hidden truth."""

        return {
            "domain": self.domain,
            "permissions": self.permissions,
            "available_actions": [
                {
                    "action_id": item.action_id,
                    "description": item.description,
                }
                for item in self.action_rules
            ],
            "synthetic": True,
        }


def load_episodes(path: str | Path | None = None) -> list[M4Episode]:
    source = Path(path).resolve() if path else DEFAULT_CATALOG
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("M4 Episode catalog must be a JSON array")
    episodes = [M4Episode.model_validate(item) for item in raw]
    ids = [item.episode_id for item in episodes]
    case_ids = [item.case_id for item in episodes]
    if len(ids) != len(set(ids)):
        raise ValueError("M4 Episode catalog contains duplicate episode_id values")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("M4 Episode catalog contains duplicate case_id values")
    if any(len(item.turns) < 2 for item in episodes):
        raise ValueError("every M4 Episode must exercise at least two user turns")
    return episodes


__all__ = ["DEFAULT_CATALOG", "M4Episode", "M4Expected", "M4Turn", "load_episodes"]
