"""Cross-domain M7 controls, including deliberately unsafe memories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from core.continual_improvement import ExperienceKind, ExperienceStatus

DOMAINS = (
    "enterprise_it",
    "saas_access",
    "logistics",
    "membership",
    "merchant_reconciliation",
    "education_portal",
    "device_maintenance",
    "cross_border",
)


class M7Scenario(BaseModel):
    scenario_id: str
    domain: str
    scenario_type: str
    source_domain: str
    source_state_keys: set[str] = Field(default_factory=set)
    query_state_keys: set[str] = Field(default_factory=set)
    source_state_constraints: dict[str, Any] = Field(default_factory=dict)
    query_state_constraints: dict[str, Any] = Field(default_factory=dict)
    source_required_capabilities: set[str] = Field(default_factory=set)
    query_available_capabilities: set[str] = Field(default_factory=set)
    source_required_permissions: set[str] = Field(default_factory=set)
    query_available_permissions: set[str] = Field(default_factory=set)
    source_tool_versions: dict[str, str] = Field(default_factory=dict)
    query_tool_versions: dict[str, str] = Field(default_factory=dict)
    source_valid_until: str | None = None
    source_kind: ExperienceKind = ExperienceKind.SUCCESSFUL_PROCEDURE
    source_status: ExperienceStatus = ExperienceStatus.ACTIVE
    source_verified: bool = True
    should_retrieve: bool
    reason: str


def build_scenarios() -> list[M7Scenario]:
    scenarios: list[M7Scenario] = []
    for domain in DOMAINS:
        common = {"observed_state", "affected_resource", f"{domain}_identity"}
        scenarios.extend(
            [
                M7Scenario(
                    scenario_id=f"{domain}:exact_success",
                    domain=domain,
                    scenario_type="exact_success",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    should_retrieve=True,
                    reason="verified experience matches the current structured state",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:partial_success",
                    domain=domain,
                    scenario_type="partial_success",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys={"observed_state", f"{domain}_identity"},
                    should_retrieve=True,
                    reason="partial state overlap is sufficient but still requires reverification",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:failure_avoidance",
                    domain=domain,
                    scenario_type="failure_avoidance",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_kind=ExperienceKind.FAILURE_AVOIDANCE,
                    should_retrieve=True,
                    reason="verified failure should prevent repeating the same action",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:boundary_handoff",
                    domain=domain,
                    scenario_type="boundary_handoff",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_kind=ExperienceKind.BOUNDARY_HANDOFF,
                    should_retrieve=True,
                    reason="verified capability boundary is reusable as a handoff candidate",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:state_mismatch",
                    domain=domain,
                    scenario_type="state_mismatch",
                    source_domain=domain,
                    source_state_keys={f"{domain}_billing_state"},
                    query_state_keys={f"{domain}_access_state"},
                    should_retrieve=False,
                    reason="same domain does not imply the same problem state",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:cross_domain_false_friend",
                    domain=domain,
                    scenario_type="cross_domain_false_friend",
                    source_domain=(
                        DOMAINS[(DOMAINS.index(domain) + 1) % len(DOMAINS)]
                    ),
                    source_state_keys=common,
                    query_state_keys=common,
                    should_retrieve=False,
                    reason="surface similarity cannot override a domain mismatch",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:unverified_source",
                    domain=domain,
                    scenario_type="unverified_source",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_verified=False,
                    should_retrieve=False,
                    reason="plausible but unverified outcomes cannot become active memory",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:deprecated_source",
                    domain=domain,
                    scenario_type="deprecated_source",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_status=ExperienceStatus.DEPRECATED,
                    should_retrieve=False,
                    reason="deprecated experience must not influence current control",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:state_value_conflict",
                    domain=domain,
                    scenario_type="state_value_conflict",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_state_constraints={"deployment_ring": "blue"},
                    query_state_constraints={"deployment_ring": "green"},
                    should_retrieve=False,
                    reason="the same field names can describe incompatible environment states",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:capability_mismatch",
                    domain=domain,
                    scenario_type="capability_mismatch",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_required_capabilities={"provider_admin_read"},
                    query_available_capabilities={"local_status_read"},
                    should_retrieve=False,
                    reason="a procedure is inapplicable when its required capability is absent",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:permission_mismatch",
                    domain=domain,
                    scenario_type="permission_mismatch",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_required_permissions={"tenant:admin"},
                    query_available_permissions={"tenant:viewer"},
                    should_retrieve=False,
                    reason="past authority cannot be assumed in the current Case",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:tool_version_mismatch",
                    domain=domain,
                    scenario_type="tool_version_mismatch",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_tool_versions={"provider_tool": "1"},
                    query_tool_versions={"provider_tool": "2"},
                    should_retrieve=False,
                    reason="tool-contract drift invalidates old procedure arguments",
                ),
                M7Scenario(
                    scenario_id=f"{domain}:expired_source",
                    domain=domain,
                    scenario_type="expired_source",
                    source_domain=domain,
                    source_state_keys=common,
                    query_state_keys=common,
                    source_valid_until=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                    should_retrieve=False,
                    reason="freshness is part of applicability, not retrieval metadata",
                ),
            ]
        )
    return scenarios


__all__ = ["DOMAINS", "M7Scenario", "build_scenarios"]
