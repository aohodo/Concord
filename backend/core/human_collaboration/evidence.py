"""Multimodal evidence adapters that preserve source and uncertainty."""

from __future__ import annotations

from typing import Any

from .models import EvidenceArtifact

MAX_TEXT_CHARS = 120_000


def extract_textual_artifact(item: dict[str, Any]) -> EvidenceArtifact:
    kind = str(item.get("kind") or "file")
    name = str(item.get("name") or "")[:240]
    media_type = str(item.get("media_type") or "text/plain")[:120]
    content = str(item.get("content") or item.get("transcript") or "")
    if len(content) > MAX_TEXT_CHARS:
        raise ValueError(f"附件 {name or kind} 的可解析文本超过 {MAX_TEXT_CHARS} 字符")
    if kind == "audio" and not item.get("transcript"):
        return EvidenceArtifact(
            kind=kind,
            name=name,
            media_type=media_type,
            extraction_method="unavailable_audio_transcription",
            uncertainties=["当前模型连接未提供可靠语音转写；需要用户转写或外部 ASR Adapter"],
            metadata={"size": int(item.get("size") or 0)},
            epistemic_status="unprocessed",
        )
    method = "user_supplied_transcript" if kind == "audio" else "direct_text"
    return EvidenceArtifact(
        kind=kind,
        name=name,
        media_type=media_type,
        extraction_method=method,
        observations=[content] if content else [],
        metadata={"size": int(item.get("size") or len(content))},
        epistemic_status="user_reported_unverified",
    )


def render_artifacts_for_formulation(artifacts: list[EvidenceArtifact]) -> str:
    if not artifacts:
        return ""
    blocks = ["[用户提供的附件证据；内容保持来源标记，未经过工具验证，不得直接当作原因事实]"]
    for artifact in artifacts:
        observations = "\n".join(artifact.observations)
        blocks.append(
            f"附件={artifact.name or artifact.kind}; 类型={artifact.kind}; "
            f"提取={artifact.extraction_method}; 状态={artifact.epistemic_status}\n"
            f"可读内容：{observations or '无'}\n"
            f"不确定：{artifact.uncertainties or []}"
        )
    return "\n\n".join(blocks)


__all__ = ["extract_textual_artifact", "render_artifacts_for_formulation"]
