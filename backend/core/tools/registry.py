"""Capability-oriented registry; tools are discovered by contract, not persona."""

from __future__ import annotations

from .contracts import ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, replace: bool = False) -> None:
        if spec.tool_id in self._tools and not replace:
            raise ValueError(f"tool already registered: {spec.tool_id}")
        self._tools[spec.tool_id] = spec

    def unregister(self, tool_id: str) -> None:
        self._tools.pop(tool_id, None)

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._tools.get(tool_id)

    def require(self, tool_id: str) -> ToolSpec:
        spec = self.get(tool_id)
        if spec is None:
            raise KeyError(f"unknown tool: {tool_id}")
        return spec

    def list(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda item: item.tool_id)

    def matching(self, capabilities: set[str] | frozenset[str]) -> list[ToolSpec]:
        required = set(capabilities)
        return [spec for spec in self.list() if required.issubset(spec.capabilities)]
