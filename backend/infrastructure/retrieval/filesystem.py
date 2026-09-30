"""Auditable live-file retrieval using ripgrep and bounded text reads."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
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


class FilesystemRetrievalAdapter:
    def __init__(self, root: str | Path, *, respect_ignore: bool = True) -> None:
        self.root = Path(root).resolve()
        self.respect_ignore = respect_ignore

    async def search_text(
        self,
        query: str,
        *,
        regex: bool = False,
        globs: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        command = ["rg", "--json", "--color", "never", "--hidden"]
        if not self.respect_ignore:
            command.append("--no-ignore")
        if not regex:
            command.append("--fixed-strings")
        for pattern in globs or []:
            command.extend(["--glob", pattern])
        command.extend(
            [
                "--glob",
                "!.git/**",
                "--glob",
                "!**/.venv/**",
                "--glob",
                "!**/data/**",
                query,
                str(self.root),
            ]
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode not in {0, 1}:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "rg failed")
        matches: list[dict[str, Any]] = []
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            absolute = Path(data["path"]["text"]).resolve()
            relative = absolute.relative_to(self.root).as_posix()
            matches.append(
                {
                    "path": relative,
                    "line": int(data.get("line_number", 0)),
                    "text": data.get("lines", {}).get("text", "").rstrip("\r\n"),
                    "submatches": data.get("submatches", []),
                }
            )
            if len(matches) >= max(1, min(max_results, 200)):
                break
        return matches

    async def read_text(
        self,
        relative_path: str,
        *,
        line_start: int = 1,
        line_end: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        target = (self.root / relative_path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("path escapes the configured workspace root") from exc
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        start = max(1, line_start)
        end = max(start, line_end or start + 199)

        def _read() -> dict[str, Any]:
            selected: list[str] = []
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    if number < start:
                        continue
                    if number > end:
                        break
                    selected.append(line)
                    if sum(len(item) for item in selected) >= max_chars:
                        break
            return {
                "path": target.relative_to(self.root).as_posix(),
                "line_start": start,
                "line_end": start + max(len(selected) - 1, 0),
                "content": "".join(selected)[:max_chars],
                "truncated": sum(len(item) for item in selected) > max_chars,
            }

        return await asyncio.to_thread(_read)


def build_filesystem_tools(adapter: FilesystemRetrievalAdapter) -> list[ToolSpec]:
    async def search(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        matches = await adapter.search_text(
            str(args["query"]),
            regex=bool(args.get("regex", False)),
            globs=list(args.get("globs", [])),
            max_results=int(args.get("max_results", 20)),
        )
        now = datetime.now(UTC).isoformat()
        evidence = [
            Evidence(
                key=f"{item['path']}:{item['line']}",
                value=item["text"],
                source="workspace",
                source_type="exact_text_match",
                path=item["path"],
                line_start=item["line"],
                line_end=item["line"],
                retrieved_at=now,
                reliability=1.0,
                metadata={"retrieval_method": "ripgrep"},
            )
            for item in matches
        ]
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data=matches,
            observation=f"found {len(matches)} exact text matches",
            evidence=evidence,
        )

    async def read(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        result = await adapter.read_text(
            str(args["path"]),
            line_start=int(args.get("line_start", 1)),
            line_end=int(args["line_end"]) if args.get("line_end") is not None else None,
            max_chars=int(args.get("max_chars", 20_000)),
        )
        evidence = Evidence(
            key=f"{result['path']}:{result['line_start']}-{result['line_end']}",
            value=result["content"],
            source="workspace",
            source_type="file_read",
            path=result["path"],
            line_start=result["line_start"],
            line_end=result["line_end"],
            retrieved_at=datetime.now(UTC).isoformat(),
            reliability=1.0,
            metadata={"retrieval_method": "direct_read", "truncated": result["truncated"]},
        )
        return ToolResult(
            tool_id=invocation.tool_id,
            invocation_id=invocation.invocation_id,
            status=ToolStatus.SUCCEEDED,
            data=result,
            observation="read the requested workspace text range",
            evidence=[evidence],
        )

    return [
        ToolSpec(
            tool_id="workspace_search_text",
            name="Search workspace text",
            description=(
                "Search live workspace files for exact text or an explicit regular expression. "
                "Use for identifiers, error codes, paths, configuration keys, logs and source symbols."
            ),
            category=ToolCategory.OBSERVE,
            handler=search,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "regex": {"type": "boolean"},
                    "globs": {"type": "array"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"workspace_search", "exact_retrieval"}),
            data_source=DataSource.REAL_READ_ONLY,
            operation_mode=OperationMode.READ_ONLY,
            side_effect_scope=SideEffectScope.NONE,
            risk_level=RiskLevel.LOW,
            expected_latency_ms=40,
            expected_information_gain=0.85,
            expected_progress=0.6,
            provenance="ripgrep/live-workspace",
        ),
        ToolSpec(
            tool_id="workspace_read_text",
            name="Read workspace text",
            description="Read a bounded line range from a known workspace text file.",
            category=ToolCategory.OBSERVE,
            handler=read,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            capabilities=frozenset({"workspace_read", "exact_retrieval"}),
            data_source=DataSource.REAL_READ_ONLY,
            operation_mode=OperationMode.READ_ONLY,
            side_effect_scope=SideEffectScope.NONE,
            risk_level=RiskLevel.LOW,
            expected_latency_ms=15,
            expected_information_gain=0.9,
            expected_progress=0.7,
            provenance="filesystem/live-workspace",
        ),
    ]
