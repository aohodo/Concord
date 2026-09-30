import asyncio
import json
from types import SimpleNamespace

from core.adaptive_resolution import LangChainResolutionPlanner, ResolutionDecision


def run(awaitable):
    return asyncio.run(awaitable)


class SequencedMessages:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            payload = {
                "decision": "use_tool",
                "rationale": "broken structure",
                "provisional_explanations": ["not-an-object"],
            }
        else:
            payload = {
                "decision": "ask_user",
                "rationale": "repaired without inventing evidence",
                "response": "请补充实际错误信息。",
            }
        return SimpleNamespace(
            content=[{"type": "text", "text": json.dumps(payload)}]
        )


class FakeClient:
    supports_json_object = True

    def __init__(self):
        self.messages = SequencedMessages()


def test_planner_repairs_invalid_structured_output_once():
    client = FakeClient()
    planner = LangChainResolutionPlanner(client, "test-model")

    result = run(
        planner.plan(
            resolution_context={"case_id": "case-1"},
            tools=[],
            tool_history=[],
            belief_history=[],
            guardrail_feedback="",
            decision_event="initial",
        )
    )

    assert result.decision is ResolutionDecision.ASK_USER
    assert len(client.messages.calls) == 2
    assert "只修复" in client.messages.calls[1]["system"]
