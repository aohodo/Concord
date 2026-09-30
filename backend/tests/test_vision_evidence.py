import asyncio
from types import SimpleNamespace

from core.problem_formulation.vision import extract_image_evidence, render_image_evidence


def run(awaitable):
    return asyncio.run(awaitable)


class FakeMessages:
    def __init__(self):
        self.request = None

    async def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            content=[
                {
                    "type": "text",
                    "text": (
                        '{"observations":["登录窗口显示错误"],'
                        '"visible_text":["691"],"uncertainties":["账号已脱敏"]}'
                    ),
                }
            ]
        )


class FakeVisionClient:
    supports_vision = True

    def __init__(self):
        self.messages = FakeMessages()


def test_image_input_becomes_unverified_evidence_not_diagnosis():
    client = FakeVisionClient()
    evidence = run(
        extract_image_evidence(
            client=client,
            model="vision-model",
            user_message="连不上了",
            image_urls=["data:image/png;base64,aW1hZ2U="],
        )
    )

    assert evidence["visible_text"] == ["691"]
    assert evidence["epistemic_status"] == "unverified_visual_observation"
    assert client.messages.request["messages"][0]["content"][1]["type"] == (
        "image_url"
    )
    rendered = render_image_evidence(evidence)
    assert "未验证观察" in rendered
    assert "不是原因诊断" in rendered
