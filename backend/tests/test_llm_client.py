import asyncio
from types import SimpleNamespace

from core.llm_client import LLMConfig, OpenAICompatibleMessagesClient, create_llm_client


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeOpenAI:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)

    async def close(self):
        return None


def _response(*, content=None, tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def test_openai_config_defaults_qwen_to_low_reasoning():
    config = LLMConfig.from_env(
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://workspace.invalid/compatible-mode/v1/",
        }
    )

    assert config.provider == "openai"
    assert config.model == "qwen3.8-flash"
    assert config.base_url == "https://workspace.invalid/compatible-mode/v1"
    assert config.reasoning_effort == "low"


def test_openai_adapter_declares_json_object_capability():
    assert OpenAICompatibleMessagesClient.supports_json_object is True
    assert OpenAICompatibleMessagesClient.supports_vision is True


def test_openai_adapter_preserves_image_input_blocks():
    fake = FakeOpenAI([_response(content="截图显示错误码 691。")])
    client = OpenAICompatibleMessagesClient(
        api_key="test-key",
        base_url="https://workspace.invalid/compatible-mode/v1",
        reasoning_effort="low",
        openai_client=fake,
    )

    asyncio.run(
        client.messages.create(
            model="qwen3.8-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请读取截图中的错误信息。"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "aW1hZ2UtYnl0ZXM=",
                            },
                        },
                    ],
                }
            ],
        )
    )

    assert fake.completions.calls[0]["messages"][0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "请读取截图中的错误信息。"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aW1hZ2UtYnl0ZXM="},
            },
        ],
    }


def test_provider_clients_do_not_inherit_environment_proxies():
    openai_adapter = create_llm_client(
        LLMConfig(
            provider="openai",
            api_key="test-key",
            base_url="https://workspace.invalid/compatible-mode/v1",
            model="qwen3.8-flash",
        )
    )
    anthropic_client = create_llm_client(
        LLMConfig(provider="anthropic", api_key="test-key", model="test-model")
    )

    assert openai_adapter._client._client._trust_env is False
    assert anthropic_client._client._trust_env is False

    asyncio.run(openai_adapter.close())
    asyncio.run(anthropic_client.close())


def test_openai_adapter_maps_tools_and_tool_results():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="lookup", arguments='{"query":"401"}'),
    )
    fake = FakeOpenAI(
        [
            _response(tool_calls=[tool_call], finish_reason="tool_calls"),
            _response(content="已完成查询。"),
        ]
    )
    client = OpenAICompatibleMessagesClient(
        api_key="test-key",
        base_url="https://workspace.invalid/compatible-mode/v1",
        reasoning_effort="low",
        openai_client=fake,
    )

    first = asyncio.run(
        client.messages.create(
            model="qwen3.8-flash",
            system="你是测试助手。",
            max_tokens=256,
            messages=[{"role": "user", "content": "查询 401"}],
            tools=[
                {
                    "name": "lookup",
                    "description": "查询错误码",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
        )
    )

    assert first.content == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "lookup",
            "input": {"query": "401"},
        }
    ]
    request = fake.completions.calls[0]
    assert request["reasoning_effort"] == "low"
    assert request["messages"][0] == {"role": "system", "content": "你是测试助手。"}
    assert request["tools"][0]["function"]["name"] == "lookup"

    second = asyncio.run(
        client.messages.create(
            model="qwen3.8-flash",
            messages=[
                {"role": "assistant", "content": first.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": '{"status":"ok"}',
                        }
                    ],
                },
            ],
        )
    )

    assert second.content == [{"type": "text", "text": "已完成查询。"}]
    assert fake.completions.calls[1]["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"status":"ok"}',
    }
