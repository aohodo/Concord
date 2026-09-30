"""Reusable M2 scenario catalog and boundary evaluator."""

from .catalog import (
    M2ExpectedResult,
    M2FaultSpec,
    M2ScenarioDefinition,
    build_resolution_context,
    load_scenario_catalog,
    seed_scenario,
)
from .humanity import (
    DimensionResult,
    HumanityDimensions,
    HumanityJudge,
    HumanityJudgment,
)
from .longitudinal import (
    LongitudinalEvaluationResult,
    LongitudinalScenarioDefinition,
    UserEvidenceBurst,
    evaluate_longitudinal_scenario,
    evaluate_longitudinal_scenarios,
    load_longitudinal_catalog,
)
from .runner import M2EvaluationResult, evaluate_scenarios

__all__ = [
    "DimensionResult",
    "HumanityDimensions",
    "HumanityJudge",
    "HumanityJudgment",
    "LongitudinalEvaluationResult",
    "LongitudinalScenarioDefinition",
    "M2EvaluationResult",
    "M2ExpectedResult",
    "M2FaultSpec",
    "M2ScenarioDefinition",
    "UserEvidenceBurst",
    "build_resolution_context",
    "evaluate_longitudinal_scenario",
    "evaluate_longitudinal_scenarios",
    "evaluate_scenarios",
    "load_longitudinal_catalog",
    "load_scenario_catalog",
    "seed_scenario",
]
