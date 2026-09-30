"""Synthetic, non-sensitive M3 boundary catalog."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from core.adaptive_resolution import CollaborationHandoff, CollaborationMode
from core.multi_agent_collaboration import CollaborationStatus

DEFAULT_CATALOG = Path(__file__).with_name("data") / "m3_collaboration_cases.json"


class M3EvaluationCase(BaseModel):
    case_id: str
    description: str
    handoff: CollaborationHandoff
    allowed_statuses: set[CollaborationStatus]
    expected_modes: set[CollaborationMode]
    maximum_agents: int = Field(ge=0, le=6)
    fixed_baseline_agents: int = Field(default=3, ge=1)


def load_cases(path: str | Path | None = None) -> list[M3EvaluationCase]:
    source = Path(path) if path else DEFAULT_CATALOG
    return [M3EvaluationCase.model_validate(item) for item in json.loads(source.read_text("utf-8"))]


__all__ = ["DEFAULT_CATALOG", "M3EvaluationCase", "load_cases"]
