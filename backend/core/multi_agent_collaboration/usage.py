"""Per-collaboration model usage accounting that remains safe under asyncio."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelUsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_available: bool = False

    def record(self, response: Any) -> None:
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = getattr(usage, "input_tokens", None)
        if prompt is None:
            prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "output_tokens", None)
        if completion is None:
            completion = getattr(usage, "completion_tokens", None)
        if prompt is not None or completion is not None:
            self.tokens_available = True
            self.input_tokens += int(prompt or 0)
            self.output_tokens += int(completion or 0)


_CURRENT_USAGE: ContextVar[ModelUsageMeter | None] = ContextVar(
    "concord_m3_model_usage", default=None
)


def begin_usage_meter(meter: ModelUsageMeter) -> Token:
    return _CURRENT_USAGE.set(meter)


def end_usage_meter(token: Token) -> None:
    _CURRENT_USAGE.reset(token)


def record_model_response(response: Any) -> None:
    meter = _CURRENT_USAGE.get()
    if meter is not None:
        meter.record(response)


__all__ = [
    "ModelUsageMeter",
    "begin_usage_meter",
    "end_usage_meter",
    "record_model_response",
]
