"""Provider-neutral model client construction for Concord.

Both provider adapters expose the small ``client.messages.create`` surface used
by the control modules, keeping provider-specific HTTP details at this boundary.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI


def _first_value(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float = 300.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        values = os.environ if env is None else env
        provider = _first_value(values, "LLM_PROVIDER").lower() or "anthropic"
        if provider in {"openai-compatible", "qwen", "dashscope"}:
            provider = "openai"

        try:
            timeout_seconds = float(_first_value(values, "LLM_TIMEOUT_SECONDS") or "300")
        except ValueError as exc:
            raise RuntimeError("LLM_TIMEOUT_SECONDS 必须是数字") from exc
        if timeout_seconds <= 0:
            raise RuntimeError("LLM_TIMEOUT_SECONDS 必须大于 0")

        if provider == "anthropic":
            api_key = _first_value(values, "ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("LLM_PROVIDER=anthropic 时必须设置 ANTHROPIC_API_KEY")
            return cls(
                provider=provider,
                api_key=api_key,
                base_url=_first_value(values, "ANTHROPIC_BASE_URL") or None,
                model=_first_value(values, "ANTHROPIC_MODEL") or "claude-3-5-sonnet-20241022",
                timeout_seconds=timeout_seconds,
            )

        if provider == "openai":
            api_key = _first_value(
                values,
                "OPENAI_API_KEY",
                "QWEN_API_KEY",
            )
            base_url = _first_value(
                values,
                "OPENAI_BASE_URL",
                "QWEN_BASE_URL",
            )
            if not api_key:
                raise RuntimeError("LLM_PROVIDER=openai 时必须设置 OPENAI_API_KEY")
            if not base_url:
                raise RuntimeError("LLM_PROVIDER=openai 时必须设置 OPENAI_BASE_URL")
            return cls(
                provider=provider,
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                model=_first_value(
                    values,
                    "OPENAI_MODEL",
                    "QWEN_MODEL",
                )
                or "qwen3.8-flash",
                reasoning_effort=_first_value(values, "OPENAI_REASONING_EFFORT") or "low",
                timeout_seconds=timeout_seconds,
            )

        raise RuntimeError("LLM_PROVIDER 仅支持 anthropic 或 openai")


def create_llm_client(config: LLMConfig) -> Any:
    if config.provider == "anthropic":
        kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "timeout": config.timeout_seconds,
            # Model traffic must not inherit HTTP(S)_PROXY / ALL_PROXY from
            # the shell or virtual-network environment.
            "http_client": httpx.AsyncClient(
                timeout=config.timeout_seconds,
                trust_env=False,
            ),
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return AsyncAnthropic(**kwargs)

    if config.provider == "openai":
        return OpenAICompatibleMessagesClient(
            api_key=config.api_key,
            base_url=config.base_url or "",
            reasoning_effort=config.reasoning_effort,
            timeout_seconds=config.timeout_seconds,
        )

    raise ValueError(f"不支持的 LLM provider: {config.provider}")


def create_configured_client(
    *,
    provider: str,
    api_key: str,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: float = 300.0,
) -> Any:
    """Build a client for constructors that still expose explicit arguments."""
    return create_llm_client(
        LLMConfig(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model="",
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
        )
    )


class OpenAICompatibleMessagesClient:
    """Adapt OpenAI Chat Completions to the Anthropic Messages subset we use."""

    supports_json_object = True
    supports_vision = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        reasoning_effort: str | None = "low",
        timeout_seconds: float = 300.0,
        openai_client: Any = None,
    ) -> None:
        if not base_url:
            raise ValueError("OpenAI-compatible client requires base_url")
        self._client = openai_client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            # OpenAI 3.x uses httpx2 internally.  Supplying our own client is
            # the reliable way to prohibit environment-proxy inheritance.
            http_client=httpx2.AsyncClient(
                timeout=timeout_seconds,
                trust_env=False,
            ),
        )
        self._reasoning_effort = reasoning_effort
        self.messages = _OpenAIMessagesResource(self)

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _value(block: Any, name: str, default: Any = None) -> Any:
        if isinstance(block, dict):
            return block.get(name, default)
        return getattr(block, name, default)

    @classmethod
    def _text_from_blocks(cls, blocks: Iterable[Any]) -> str:
        texts = []
        for block in blocks:
            if isinstance(block, str):
                texts.append(block)
                continue
            if cls._value(block, "type") == "text":
                text = cls._value(block, "text")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts)

    @classmethod
    def _assistant_message(cls, content: Any) -> dict[str, Any]:
        if isinstance(content, str):
            return {"role": "assistant", "content": content}

        blocks = list(content or [])
        message: dict[str, Any] = {
            "role": "assistant",
            "content": cls._text_from_blocks(blocks) or None,
        }
        tool_calls = []
        for block in blocks:
            if cls._value(block, "type") != "tool_use":
                continue
            arguments = cls._value(block, "input", {})
            tool_calls.append(
                {
                    "id": str(cls._value(block, "id", "")),
                    "type": "function",
                    "function": {
                        "name": str(cls._value(block, "name", "")),
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    @classmethod
    def _multimodal_content(cls, blocks: Iterable[Any]) -> list[dict[str, Any]]:
        """Preserve grounded user media instead of flattening it into text.

        Callers may use native OpenAI ``image_url`` blocks or Anthropic-style
        base64 ``image`` blocks.  The adapter normalizes both to the OpenAI
        compatible shape accepted by Qwen multimodal models.
        """
        converted: list[dict[str, Any]] = []
        for block in blocks:
            if isinstance(block, str):
                converted.append({"type": "text", "text": block})
                continue
            block_type = cls._value(block, "type")
            if block_type == "text":
                text = cls._value(block, "text")
                if isinstance(text, str) and text:
                    converted.append({"type": "text", "text": text})
                continue
            if block_type == "image_url":
                image_url = cls._value(block, "image_url")
                if isinstance(image_url, str):
                    image_url = {"url": image_url}
                if isinstance(image_url, Mapping) and image_url.get("url"):
                    converted.append({"type": "image_url", "image_url": dict(image_url)})
                continue
            if block_type != "image":
                continue
            source = cls._value(block, "source", {})
            source_type = cls._value(source, "type")
            if source_type == "base64":
                media_type = cls._value(source, "media_type", "image/png")
                data = cls._value(source, "data", "")
                if data:
                    converted.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        }
                    )
            elif source_type == "url":
                url = cls._value(source, "url", "")
                if url:
                    converted.append({"type": "image_url", "image_url": {"url": url}})
        return converted

    @classmethod
    def _convert_messages(cls, messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "assistant":
                converted.append(cls._assistant_message(content))
                continue

            if isinstance(content, list):
                tool_results = [
                    block for block in content if cls._value(block, "type") == "tool_result"
                ]
                if tool_results:
                    for block in tool_results:
                        result_content = cls._value(block, "content", "")
                        if not isinstance(result_content, str):
                            result_content = json.dumps(result_content, ensure_ascii=False)
                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": str(cls._value(block, "tool_use_id", "")),
                                "content": result_content,
                            }
                        )
                    continue
                content = cls._multimodal_content(content)

            converted.append(
                {
                    "role": role,
                    "content": content if isinstance(content, list) else str(content),
                }
            )
        return converted

    @staticmethod
    def _convert_tools(tools: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"}),
                },
            }
            for tool in tools
        ]

    async def _create_message(self, **kwargs: Any) -> Any:
        messages = self._convert_messages(kwargs.pop("messages", []))
        system = kwargs.pop("system", None)
        if system:
            messages.insert(0, {"role": "system", "content": str(system)})

        request: dict[str, Any] = {
            "model": kwargs.pop("model"),
            "messages": messages,
        }
        max_tokens = kwargs.pop("max_tokens", None)
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        temperature = kwargs.pop("temperature", None)
        if temperature is not None:
            request["temperature"] = temperature
        tools = kwargs.pop("tools", None)
        if tools:
            request["tools"] = self._convert_tools(tools)
        reasoning_effort = kwargs.pop("reasoning_effort", self._reasoning_effort)
        if reasoning_effort:
            request["reasoning_effort"] = reasoning_effort
        request.update(kwargs)

        response = await self._client.chat.completions.create(**request)
        choice = response.choices[0]
        message = choice.message
        blocks = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        for tool_call in message.tool_calls or []:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"模型工具参数不是合法 JSON: {tool_call.function.arguments!r}"
                ) from exc
            if not isinstance(arguments, dict):
                raise TypeError("模型工具参数必须是 JSON object")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": arguments,
                }
            )
        return SimpleNamespace(
            content=blocks,
            stop_reason=choice.finish_reason,
            usage=getattr(response, "usage", None),
        )


class _OpenAIMessagesResource:
    def __init__(self, owner: OpenAICompatibleMessagesClient) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> Any:
        return await self._owner._create_message(**kwargs)
