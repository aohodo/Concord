"""Normalize text blocks returned by supported model clients."""

from collections.abc import Iterable
from typing import Any


def provider_thinking_options(client: Any, *, enabled: bool) -> dict[str, bool]:
    """Control provider thinking without leaking provider syntax to callers.

    Concord normally supplies the control scaffold for extraction and bounded
    planning.  The caller can still enable deeper integration when runtime
    state shows that the fast path did not produce an actionable Case.
    """

    if getattr(client, "supports_thinking_control", False):
        return {"enable_thinking": enabled}
    return {}


def extract_text_content(content: Iterable[Any]) -> str:
    """Return text from either object-based or dictionary response blocks."""
    texts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue

        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type", block_type)
            text = block.get("text", text)

        if isinstance(text, str) and (block_type in (None, "text")):
            texts.append(text)

    return "\n".join(t for t in texts if t)
