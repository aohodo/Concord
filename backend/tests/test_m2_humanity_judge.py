import asyncio
from types import SimpleNamespace

from evaluation.m2_resolution import HumanityJudge


def run(awaitable):
    return asyncio.run(awaitable)


class FakeMessages:
    async def create(self, **kwargs):
        return SimpleNamespace(
            content=[
                {
                    "type": "text",
                    "text": """{
                      "case_id":"ignored",
                      "dimensions":{
                        "outcome_orientation":"PASS",
                        "cognitive_load":"FAIL",
                        "emotional_attunement":"PARTIAL",
                        "progress_truthfulness":"FAIL",
                        "failure_memory":"PASS",
                        "epistemic_humility":"PARTIAL"
                      },
                      "failure_labels":["OVERLONG_RESPONSE","FALSE_PROGRESS"],
                      "evidence":["在工具验证前声称已经解决"],
                      "critique":"礼貌不能抵消虚假进度。"
                    }""",
                }
            ]
        )


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


def test_humanity_judge_keeps_interaction_dimensions_separate_from_success():
    judgment = run(
        HumanityJudge(FakeClient(), "judge-model").judge(
            case_id="case-1",
            user_trajectory={"urgency": "deadline", "reading_budget": "minimal"},
            runtime_result={"response": "很长的回答", "status": "resolved"},
        )
    )

    assert judgment.case_id == "case-1"
    assert judgment.dimensions.outcome_orientation.value == "PASS"
    assert judgment.dimensions.cognitive_load.value == "FAIL"
    assert "FALSE_PROGRESS" in judgment.failure_labels


class RepairingMessages(FakeMessages):
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=[
                    {
                        "type": "text",
                        "text": '{"outcome_orientation":"PASS","evidence":{}}',
                    }
                ]
            )
        return await super().create(**kwargs)


def test_humanity_judge_repairs_invalid_schema_once():
    client = FakeClient()
    client.messages = RepairingMessages()

    judgment = run(
        HumanityJudge(client, "judge-model").judge(
            case_id="case-repair",
            user_trajectory={},
            runtime_result={},
        )
    )

    assert judgment.case_id == "case-repair"
    assert client.messages.calls == 2
