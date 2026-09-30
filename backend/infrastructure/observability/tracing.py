"""Optional LangSmith adapter with local-first privacy defaults."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from uuid import UUID, uuid4

from core.tools.tracing import NullTraceSink, TraceSink, TraceSpan

logger = logging.getLogger(__name__)

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|cookie|credential)",
    re.IGNORECASE,
)
_CONTENT_KEY = re.compile(
    r"(?:raw_user_input|prompt|content|document|screenshot|image|response|messages?)",
    re.IGNORECASE,
)


def sanitize_trace_value(value: Any, *, include_content: bool = False) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            label = str(key)
            if _SECRET_KEY.search(label):
                sanitized[label] = "[REDACTED_SECRET]"
            elif not include_content and _CONTENT_KEY.search(label):
                sanitized[label] = "[REDACTED_CONTENT]"
            else:
                sanitized[label] = sanitize_trace_value(item, include_content=include_content)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [sanitize_trace_value(item, include_content=include_content) for item in value]
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    return value


class _LangSmithSpan:
    def __init__(self, run: Any, *, include_content: bool) -> None:
        self._run = run
        self._include_content = include_content
        self.trace_id = str(run.trace_id or run.id)

    async def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            safe_outputs = sanitize_trace_value(
                outputs or {}, include_content=self._include_content
            )
            safe_metadata = sanitize_trace_value(
                metadata or {}, include_content=self._include_content
            )
            self._run.end(outputs=safe_outputs, error=error, metadata=safe_metadata)
            await asyncio.to_thread(self._run.patch)
        except Exception as exc:  # noqa: BLE001 - tracing must never break product execution
            logger.warning("LangSmith span finalization failed: %s", exc)


class LangSmithTraceSink:
    enabled = True

    def __init__(
        self,
        *,
        api_key: str,
        project_name: str,
        endpoint: str | None = None,
        include_content: bool = False,
        trust_environment_proxy: bool = False,
    ) -> None:
        import requests
        from langsmith import Client

        self._include_content = include_content
        session = requests.Session()
        session.trust_env = trust_environment_proxy
        self._client = Client(
            api_key=api_key,
            api_url=endpoint,
            session=session,
            anonymizer=lambda item: sanitize_trace_value(
                item, include_content=include_content
            ),
        )
        self._project_name = project_name

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
        from langsmith.run_trees import RunTree

        trace_uuid = _as_uuid(trace_id)
        run_uuid = uuid4()
        safe_inputs = sanitize_trace_value(inputs, include_content=self._include_content)
        safe_metadata = sanitize_trace_value(metadata, include_content=self._include_content)
        run = RunTree(
            id=run_uuid,
            trace_id=trace_uuid,
            name=name,
            run_type=run_type,
            inputs=safe_inputs,
            extra={"metadata": safe_metadata},
            tags=tags or [],
            project_name=self._project_name,
            ls_client=self._client,
        )
        try:
            await asyncio.to_thread(run.post)
        except Exception as exc:  # noqa: BLE001 - observability is fail-open
            logger.warning("LangSmith span creation failed; continuing locally: %s", exc)
            return await NullTraceSink().start_span(
                name,
                run_type=run_type,
                inputs={},
                metadata={},
                trace_id=str(trace_uuid),
            )
        return _LangSmithSpan(run, include_content=self._include_content)


def build_trace_sink_from_env() -> TraceSink:
    enabled = os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return NullTraceSink()
    api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if not api_key:
        logger.warning("LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is missing")
        return NullTraceSink()

    include_content = os.getenv("CONCORD_LANGSMITH_INCLUDE_CONTENT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not include_content:
        # LangGraph/LangChain automatic tracing reads these settings independently.
        os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "true")
        os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "true")
    return LangSmithTraceSink(
        api_key=api_key,
        endpoint=os.getenv("LANGSMITH_ENDPOINT") or None,
        project_name=os.getenv("LANGSMITH_PROJECT", "concord-dev-synthetic"),
        include_content=include_content,
        trust_environment_proxy=os.getenv(
            "CONCORD_LANGSMITH_TRUST_ENV_PROXY", "false"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
    )


def _as_uuid(value: str | None) -> UUID:
    if value:
        try:
            return UUID(value)
        except ValueError:
            pass
    return uuid4()


__all__ = [
    "LangSmithTraceSink",
    "build_trace_sink_from_env",
    "sanitize_trace_value",
]
