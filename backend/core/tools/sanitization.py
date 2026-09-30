"""Bounded, privacy-aware projections for durable agent memory."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|cookie|credential)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|authorization|password|passwd|secret|token|cookie|credential)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


def _safe_text(value: str, *, max_chars: int) -> str:
    value = _SECRET_ASSIGNMENT.sub("[REDACTED_SECRET]", value)
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _PHONE.sub("[REDACTED_PHONE]", value)
    return value if len(value) <= max_chars else value[:max_chars] + "…[TRUNCATED]"


def sanitize_value(
    value: Any,
    *,
    max_depth: int = 4,
    max_items: int = 24,
    max_chars: int = 512,
    _depth: int = 0,
) -> Any:
    if _depth >= max_depth:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                output["__truncated__"] = True
                break
            key_text = str(key)
            output[key_text] = (
                "[REDACTED_SECRET]"
                if _SENSITIVE_KEY.search(key_text)
                else sanitize_value(
                    item,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_chars=max_chars,
                    _depth=_depth + 1,
                )
            )
        return output
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        output = [
            sanitize_value(
                item,
                max_depth=max_depth,
                max_items=max_items,
                max_chars=max_chars,
                _depth=_depth + 1,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            output.append("[TRUNCATED_ITEMS]")
        return output
    if isinstance(value, str):
        return _safe_text(value, max_chars=max_chars)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(str(value), max_chars=max_chars)


def allowlisted_projection(value: Any, fields: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = set(fields)
    return sanitize_value({key: value[key] for key in allowed if key in value})


__all__ = ["allowlisted_projection", "sanitize_value"]
