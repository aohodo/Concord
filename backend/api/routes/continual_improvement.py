"""Research/admin API for the gated M7 continual-improvement lifecycle."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from api.schemas import ImprovementDecisionInput, ImprovementEvaluationInput
from core.continual_improvement import ProposalStatus


def build_continual_improvement_router(
    service_provider: Callable[[], Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/continual-improvement",
        tags=["M7 Continual Improvement"],
    )

    def service() -> Any:
        if os.getenv("CONCORD_ENABLE_IMPROVEMENT_ADMIN", "false").lower() != "true":
            raise HTTPException(
                403,
                "M7 改进管理接口默认关闭；仅在受控评测环境显式开启",
            )
        current = service_provider()
        if current is None:
            raise HTTPException(503, "M7 运行时未就绪")
        return current

    @router.get("/proposals")
    async def list_proposals(
        tenant_id: str = "local", status: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            selected = ProposalStatus(status) if status else None
        except ValueError as exc:
            raise HTTPException(422, "未知 proposal status") from exc
        return [
            item.model_dump(mode="json")
            for item in await service().list_proposals(
                tenant_id=tenant_id, status=selected
            )
        ]

    @router.post("/proposals/{proposal_id}/evaluations")
    async def record_evaluation(
        proposal_id: str, req: ImprovementEvaluationInput
    ) -> dict[str, Any]:
        try:
            proposal = await service().record_isolated_evaluation(
                tenant_id=req.tenant_id,
                proposal_id=proposal_id,
                evaluator=req.evaluator,
                result=req.result,
                expected_version=req.expected_version,
            )
        except KeyError as exc:
            raise HTTPException(404, "改进候选不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return proposal.model_dump(mode="json")

    @router.post("/proposals/{proposal_id}/decision")
    async def decide_proposal(
        proposal_id: str, req: ImprovementDecisionInput
    ) -> dict[str, Any]:
        try:
            proposal = await service().decide_proposal(
                tenant_id=req.tenant_id,
                proposal_id=proposal_id,
                actor_id=req.actor_id,
                decision=req.decision,
                reason=req.reason,
                expected_version=req.expected_version,
            )
        except KeyError as exc:
            raise HTTPException(404, "改进候选不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return proposal.model_dump(mode="json")

    return router


__all__ = ["build_continual_improvement_router"]
