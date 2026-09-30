"""Curated natural-language knowledge adapter for local full-text retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.tools import (
    DataSource,
    Evidence,
    OperationMode,
    RiskLevel,
    SideEffectScope,
    ToolCategory,
    ToolInvocation,
    ToolResult,
    ToolSpec,
    ToolStatus,
)


def build_knowledge_tool(knowledge_base: Any) -> ToolSpec:
    async def search(invocation: ToolInvocation) -> ToolResult:
        query = str(invocation.arguments["query"]).strip()
        top_k = max(1, min(int(invocation.arguments.get("top_k", 5)), 20))
        results = await knowledge_base.search_async(query, top_k=top_k)
        now = datetime.now(UTC).isoformat()
        evidence = [
            Evidence(
                key=f"{item.get('title', 'document')}:{item.get('chunk', 0)}",
                value=item.get("content"),
                source="internal_knowledge",
                source_type="lexical_retrieval",
                retrieved_at=now,
                reliability=max(0.0, min(float(item.get("score", 0.0)), 1.0)),
                metadata={
                    "title": item.get("title", ""),
                    "chunk": item.get("chunk", 0),
                    "retrieval_method": "sqlite_fts",
                },
            )
            for item in results
        ]
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data=results,
            observation=f"retrieved {len(results)} curated knowledge chunks",
            evidence=evidence,
            metadata={"retrieval_method": "sqlite_fts", "query": query},
        )

    return ToolSpec(
        tool_id="knowledge_search",
        name="Search curated internal knowledge",
        description=(
            "Search curated natural-language internal knowledge by local full-text relevance. "
            "Do not use for live source files, exact identifiers, logs or structured runtime state."
        ),
        category=ToolCategory.OBSERVE,
        handler=search,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        capabilities=frozenset({"internal_knowledge", "lexical_retrieval"}),
        data_source=DataSource.REAL_READ_ONLY,
        operation_mode=OperationMode.READ_ONLY,
        side_effect_scope=SideEffectScope.NONE,
        risk_level=RiskLevel.LOW,
        timeout_s=30,
        expected_latency_ms=250,
        expected_information_gain=0.75,
        expected_progress=0.55,
        provenance="sqlite-fts/curated-knowledge",
        cache_ttl_s=300,
    )


__all__ = ["build_knowledge_tool"]
