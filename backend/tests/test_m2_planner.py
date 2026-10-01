import asyncio
import json
from types import SimpleNamespace

from core.adaptive_resolution import LangChainResolutionPlanner, ResolutionDecision
from core.adaptive_resolution.models import CriterionVerification, ProvisionalExplanation


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
    supports_thinking_control = True

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
    assert all(call["enable_thinking"] is False for call in client.messages.calls)


def test_structured_evidence_objects_become_runtime_verifiable_refs():
    explanation = ProvisionalExplanation.model_validate(
        {
            "statement": "版本不一致",
            "supporting_evidence": [{"key": "secret.version", "value": 8}],
        }
    )
    verification = CriterionVerification.model_validate(
        {
            "criterion": "delivery.status=accepted",
            "evidence_refs": [
                {"key": "delivery.status", "value": "accepted"},
                {"invocation_id": "call-1"},
            ],
        }
    )

    assert explanation.supporting_evidence == ["fact:secret.version=8"]
    assert verification.evidence_refs == [
        "fact:delivery.status=accepted",
        "invocation:call-1",
    ]
