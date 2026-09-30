"""Framework-neutral tool contracts shared by M2 and infrastructure adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ToolCategory(str, Enum):
    OBSERVE = "observe"
    ACT = "act"
    VERIFY = "verify"
    HANDOFF = "handoff"


class DataSource(str, Enum):
    REAL_READ_ONLY = "real_read_only"
    SYNTHETIC = "synthetic"


class OperationMode(str, Enum):
    READ_ONLY = "read_only"
    SIMULATED_WRITE = "simulated_write"
    PRODUCTION_WRITE = "production_write"


class SideEffectScope(str, Enum):
    NONE = "none"
    CASE_SANDBOX = "case_sandbox"
    EXTERNAL = "external"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    CONFLICT = "conflict"
    PARTIAL = "partial"
    PENDING = "pending"


class Evidence(BaseModel):
    """One observable fact returned by a tool without promoting inference to fact."""

    key: str
    value: Any = None
    source: str
    source_type: str = "tool_observation"
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    retrieved_at: str | None = None
    reliability: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolContext(BaseModel):
    """Request-scoped control and authorization context."""

    case_id: str
    thread_id: str | None = None
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    actor_id: str = "concord-agent"
    tenant_id: str = "local"
    permissions: set[str] = Field(default_factory=set)
    synthetic: bool = True
    confirmation_granted: bool = False
    allow_external_writes: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeToolProfile(BaseModel):
    """Observed tool boundary for one request, not a model-supplied claim."""

    authorized_tool_ids: set[str] = Field(default_factory=set)
    available_capabilities: set[str] = Field(default_factory=set)
    granted_permissions: set[str] = Field(default_factory=set)
    tool_contract_versions: dict[str, str] = Field(default_factory=dict)
    denied_tools: dict[str, str] = Field(default_factory=dict)
    capabilities_known: bool = True
    permissions_known: bool = True
    contract_versions_known: bool = True


class ToolInvocation(BaseModel):
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: ToolContext
    idempotency_key: str | None = None
    expected_observation: str | None = None
    invocation_id: str = Field(default_factory=lambda: str(uuid4()))


class VerificationExpectation(BaseModel):
    """Machine-checkable expectation over a verification ToolResult."""

    result_path: str = "data.value"
    operator: Literal["eq", "ne", "in", "exists"] = "eq"
    value: Any = None


class VerificationRequest(BaseModel):
    """A tool-produced request for independent, mechanical verification."""

    target: str
    required_capabilities: set[str]
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str | None = None
    expectation: VerificationExpectation | None = None


class ToolResult(BaseModel):
    """Result contract that separates execution success from problem resolution."""

    tool_id: str
    invocation_id: str
    status: ToolStatus
    changed: bool = False
    data: Any = None
    observation: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    error_code: str | None = None
    error: str | None = None
    retryable: bool = False
    verification_required: bool = False
    verification_requests: list[VerificationRequest] = Field(default_factory=list)
    idempotency_key: str | None = None
    audit_event_id: str | None = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status in {ToolStatus.SUCCEEDED, ToolStatus.PENDING}


ToolHandler = Callable[[ToolInvocation], ToolResult | Awaitable[ToolResult]]
ToolAvailability = Callable[[ToolContext], bool]


@dataclass(frozen=True)
class ToolSpec:
    """Stable agent-visible contract plus runtime control metadata."""

    tool_id: str
    name: str
    description: str
    category: ToolCategory
    handler: ToolHandler = field(compare=False, repr=False)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    data_source: DataSource = DataSource.SYNTHETIC
    operation_mode: OperationMode = OperationMode.READ_ONLY
    side_effect_scope: SideEffectScope = SideEffectScope.NONE
    risk_level: RiskLevel = RiskLevel.LOW
    required_permissions: frozenset[str] = field(default_factory=frozenset)
    confirmation_required: bool = False
    idempotent: bool = True
    idempotency_key_required: bool = False
    open_world: bool = False
    timeout_s: float = 30.0
    expected_latency_ms: float = 100.0
    expected_information_gain: float = 0.5
    expected_progress: float = 0.5
    user_burden: float = 0.0
    provenance: str = "concord"
    contract_version: str = "1"
    experience_argument_fields: frozenset[str] = field(default_factory=frozenset)
    experience_result_fields: frozenset[str] = field(default_factory=frozenset)
    cache_ttl_s: float = 0.0
    bootstrap_safe: bool = False
    bootstrap_arguments: dict[str, Any] = field(default_factory=dict)
    availability: ToolAvailability = field(
        default=lambda context: True,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.bootstrap_safe and (
            self.operation_mode is not OperationMode.READ_ONLY
            or self.side_effect_scope is not SideEffectScope.NONE
            or self.required_permissions
            or self.confirmation_required
            or self.risk_level is not RiskLevel.LOW
        ):
            raise ValueError(
                "bootstrap_safe tools must be low-risk, read-only, permission-free, "
                "confirmation-free, and side-effect-free"
            )

    def public_definition(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "name": self.tool_id,
            "display_name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "category": self.category.value,
            "operation_mode": self.operation_mode.value,
            "risk_level": self.risk_level.value,
            "capabilities": sorted(self.capabilities),
            "bootstrap_safe": self.bootstrap_safe,
            "contract_version": self.contract_version,
        }

    def is_available(self, context: ToolContext) -> bool:
        return bool(self.availability(context))
