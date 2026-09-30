"""Grounded image-to-evidence extraction for otherwise text-based M1."""

from __future__ import annotations

import json
from typing import Any

from core.llm_utils import extract_text_content


async def extract_image_evidence(
    *,
    client: Any,
    model: str,
    user_message: str,
    image_urls: list[str],
) -> dict[str, Any]:
    """Extract observable content while explicitly withholding diagnosis."""

    if not image_urls:
        return {"observations": [], "visible_text": [], "uncertainties": []}
    if not getattr(client, "supports_vision", False):
        raise ValueError("configured model client does not support image input")
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "只提取图片中直接可见的事实，不诊断原因，不补全看不清的文字。"
                "结合用户文字指出哪些内容可见、哪些不确定。输出 JSON object："
                '{"observations":[string],"visible_text":[string],'
                '"uncertainties":[string]}。\n用户文字：'
                + user_message
            ),
        }
    ]
    content.extend(
        {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}
        for url in image_urls
    )
    response = await client.messages.create(
        model=model,
        max_tokens=700,
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": content}],
    )
    raw = extract_text_content(response.content).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("vision evidence extractor returned invalid JSON") from exc
    return {
        "observations": [str(item) for item in parsed.get("observations", [])],
        "visible_text": [str(item) for item in parsed.get("visible_text", [])],
        "uncertainties": [str(item) for item in parsed.get("uncertainties", [])],
        "source": "model_vision_extraction",
        "epistemic_status": "unverified_visual_observation",
    }


def render_image_evidence(evidence: dict[str, Any]) -> str:
    return (
        "[用户提供的图片；以下为视觉模型提取的未验证观察，不是原因诊断]\n"
        f"直接可见：{evidence.get('observations', [])}\n"
        f"可见文字：{evidence.get('visible_text', [])}\n"
        f"不确定部分：{evidence.get('uncertainties', [])}"
    )


__all__ = ["extract_image_evidence", "render_image_evidence"]
