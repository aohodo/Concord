"""Vendor-neutral tracing ports for graph and tool execution."""

from __future__ import annotations

from typing import Any, Protocol


class TraceSpan(Protocol):
    trace_id: str

    async def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class TraceSink(Protocol):
    enabled: bool

    async def start_span(
        self,
        name: str,
        *,
        run_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any],
        tags: list[str] | None = None,
        trace_id: str | None = None,
    ) -> TraceSpan: ...


class _NullSpan:
    def __init__(self, trace_id: str = "") -> None:
        self.trace_id = trace_id

    async def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None


class NullTraceSink:
    enabled = False

    async def start_span(
        self,
        name: str,
        *,
        run_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any],
        tags: list[str] | None = None,
        trace_id: str | None = None,
    ) -> TraceSpan:
        return _NullSpan(trace_id or "")
