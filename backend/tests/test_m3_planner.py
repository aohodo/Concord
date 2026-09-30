import asyncio
import json
from types import SimpleNamespace

from core.multi_agent_collaboration import (
    AgentPerformance,
    AgentProfile,
    AvailableAgent,
    CollaborationDecision,
    LangChainCollaborationCoordinator,
)


def run(awaitable):
    return asyncio.run(awaitable)


class RecordingMessages:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "decision": "return_to_m2",
                            "mode": "specialist_handoff",
                            "rationale": "single safe observation remains",
                            "assignments": [],
                            "expected_information_gain": 0.1,
                            "estimated_coordination_cost": 0.2,
                            "stop_conditions": ["M2 can observe directly"],
                        }
                    ),
                }
            ]
        )


class FakeClient:
    supports_json_object = True

    def __init__(self):
        self.messages = RecordingMessages()


def test_m3_structured_request_explicitly_mentions_json_for_qwen_compatibility():
    client = FakeClient()
    coordinator = LangChainCollaborationCoordinator(client, "test-model")
    available = AvailableAgent(
        profile=AgentProfile(
            agent_id="specialist",
            role="specialist",
            mission="diagnose",
            capabilities={"diagnose"},
            perspective="independent",
        ),
        performance=AgentPerformance(agent_id="specialist"),
    )

    plan = run(
        coordinator.plan(
            handoff={"case_id": "case"},
            available_agents=[available],
            prior_contributions=[],
            round_index=0,
            recruited_agents=[],
        )
    )

    assert plan.decision is CollaborationDecision.RETURN_TO_M2
    assert "JSON object" in client.messages.calls[0]["messages"][0]["content"]
