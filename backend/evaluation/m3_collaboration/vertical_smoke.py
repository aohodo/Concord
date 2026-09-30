"""One real-model M2 -> M3 -> M2 product-chain smoke test."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from core.adaptive_resolution import (
    AdaptiveResolutionService,
    CollaborationHandoff,
    ProvisionalExplanation,
    ResolutionStatus,
)
from core.llm_client import LLMConfig, create_llm_client
from core.multi_agent_collaboration import AdaptiveCollaborationService
from evaluation.m2_resolution.catalog import (
    build_resolution_context,
    load_scenario_catalog,
    seed_scenario,
)
from infrastructure.agent_performance import InMemoryAgentPerformanceStore
from infrastructure.simulation import ScenarioRuntime, build_simulation_tools
from infrastructure.tool_runtime import AdaptiveToolRuntime


async def run_vertical(case_name: str) -> dict:
    backend_root = Path(__file__).resolve().parents[2]
    load_dotenv(backend_root / ".env")
    cfg = LLMConfig.from_env()
    client = create_llm_client(cfg)
    runtime_state = ScenarioRuntime()
    tool_runtime = AdaptiveToolRuntime(scenarios=runtime_state)
    for spec in build_simulation_tools(runtime_state):
        tool_runtime.registry.register(spec)
    checkpointer = InMemorySaver()
    m2 = AdaptiveResolutionService(
        checkpointer=checkpointer,
        tool_runtime=tool_runtime,
        client=client,
        model=cfg.model,
    )
    m3 = AdaptiveCollaborationService(
        checkpointer=checkpointer,
        performance_store=InMemoryAgentPerformanceStore(),
        client=client,
        model=cfg.model,
    )
    scenario = next(
        item for item in load_scenario_catalog() if item.scenario_id == case_name
    )
    case_id = f"case-{uuid4().hex[:12]}"
    context = build_resolution_context(scenario, case_id=case_id)
    await seed_scenario(scenario, case_id=case_id, tool_runtime=tool_runtime)
    try:
        first = await m2.resolve(
            resolution_context=context,
            thread_id=f"m2:{case_id}",
            permissions=scenario.permissions,
        )
        if (
            first.status is not ResolutionStatus.COLLABORATION_REQUIRED
            or first.collaboration_handoff is None
        ):
            return {
                "case_id": case_id,
                "passed": False,
                "failure": "M2_DID_NOT_REACH_COLLABORATION_BOUNDARY",
                "m2_initial": first.model_dump(mode="json"),
            }
        collaboration = await m3.collaborate(handoff=first.collaboration_handoff)
        resumed = None
        feedback = []
        if collaboration.resume_packet is not None:
            resumed = await m2.resolve(
                resolution_context=context,
                thread_id=first.thread_id,
                permissions=scenario.permissions,
                collaboration_advice=collaboration.resume_packet.model_dump(mode="json"),
                episode_decision_budget=4,
            )
            feedback = await m3.record_resolution_outcome(
                collaboration=collaboration,
                resolution_result=resumed,
                previous_tool_count=len(first.tool_history),
            )
        safe_terminal = (
            resumed.status
            if resumed is not None
            else first.status
        ) in {
            ResolutionStatus.COLLABORATION_REQUIRED,
            ResolutionStatus.WAITING_FOR_USER,
            ResolutionStatus.PAUSED,
            ResolutionStatus.RESOLVED,
        }
        return {
            "case_id": case_id,
            "passed": collaboration.error is None and safe_terminal,
            "m2_initial_status": first.status.value,
            "m3_status": collaboration.status.value,
            "m3_mode": collaboration.mode.value,
            "m3_agents": collaboration.agents_recruited,
            "m2_resumed_status": resumed.status.value if resumed else None,
            "verified_feedback_events": len(feedback),
            "unsafe_resolution": bool(
                resumed
                and resumed.status is ResolutionStatus.RESOLVED
                and not resumed.tool_history
            ),
            "m3_metrics": collaboration.metrics.model_dump(mode="json"),
            "m2_initial": first.model_dump(mode="json"),
            "m3_result": collaboration.model_dump(mode="json"),
            "m2_resumed": resumed.model_dump(mode="json") if resumed else None,
        }
    finally:
        await client.close()


async def run_verified_feedback_vertical() -> dict:
    """Exercise real M3 advice, real M2 tools, and evidence-gated reliability credit."""

    case_name = "vpn_stale_credential"
    backend_root = Path(__file__).resolve().parents[2]
    load_dotenv(backend_root / ".env")
    cfg = LLMConfig.from_env()
    client = create_llm_client(cfg)
    runtime_state = ScenarioRuntime()
    tool_runtime = AdaptiveToolRuntime(scenarios=runtime_state)
    for spec in build_simulation_tools(runtime_state):
        tool_runtime.registry.register(spec)
    checkpointer = InMemorySaver()
    performance = InMemoryAgentPerformanceStore()
    m2 = AdaptiveResolutionService(
        checkpointer=checkpointer,
        tool_runtime=tool_runtime,
        client=client,
        model=cfg.model,
    )
    m3 = AdaptiveCollaborationService(
        checkpointer=checkpointer,
        performance_store=performance,
        client=client,
        model=cfg.model,
    )
    scenario = next(
        item for item in load_scenario_catalog() if item.scenario_id == case_name
    )
    case_id = f"case-{uuid4().hex[:12]}"
    context = build_resolution_context(scenario, case_id=case_id)
    await seed_scenario(scenario, case_id=case_id, tool_runtime=tool_runtime)
    handoff = CollaborationHandoff(
        case_id=case_id,
        thread_id=f"m2:{case_id}",
        reason="a specialized diagnosis is needed after a failed low-value retry",
        goal=dict(context.get("goal", {})),
        action_constraints=dict(context.get("action_constraints", {})),
        reported_evidence=list(context.get("evidence_handoff", {}).get("resolved", [])),
        observed_evidence=[
            {
                "tool_id": "user_report",
                "observation": scenario.user_report,
                "evidence": [],
            }
        ],
        competing_explanations=[
            ProvisionalExplanation(
                statement="the cached credential may be stale after the password change"
            ),
            ProvisionalExplanation(
                statement="the user's DNS explanation remains unverified"
            ),
        ],
        failed_directions=[
            {"action": "restart client", "status": "failed", "changed": False}
        ],
        prohibited_retries=["restart client without new evidence"],
        suggested_collaboration_mode="specialist_handoff",
    )
    try:
        collaboration = await m3.collaborate(handoff=handoff)
        if collaboration.resume_packet is None:
            return {
                "case_id": case_id,
                "passed": False,
                "failure": "M3_DID_NOT_PRODUCE_ADVICE",
                "m3_result": collaboration.model_dump(mode="json"),
            }
        resolved = await m2.resolve(
            resolution_context=context,
            thread_id=handoff.thread_id,
            permissions=scenario.permissions,
            collaboration_advice=collaboration.resume_packet.model_dump(mode="json"),
            episode_decision_budget=4,
        )
        feedback = await m3.record_resolution_outcome(
            collaboration=collaboration,
            resolution_result=resolved,
            previous_tool_count=0,
        )
        cited = {
            source_id
            for item in resolved.tool_history
            for source_id in item.get("collaboration_source_ids", [])
        }
        credited = {item.assignment_id for item in feedback}
        passed = bool(
            resolved.status is ResolutionStatus.RESOLVED
            and cited
            and credited == cited
            and all(item.successful for item in feedback)
        )
        return {
            "case_id": case_id,
            "passed": passed,
            "m3_status": collaboration.status.value,
            "m3_agents": collaboration.agents_recruited,
            "m2_status": resolved.status.value,
            "cited_assignment_ids": sorted(cited),
            "credited_assignment_ids": sorted(credited),
            "verified_feedback_events": len(feedback),
            "m3_metrics": collaboration.metrics.model_dump(mode="json"),
            "m3_result": collaboration.model_dump(mode="json"),
            "m2_result": resolved.model_dump(mode="json"),
            "feedback": [item.model_dump(mode="json") for item in feedback],
        }
    finally:
        await client.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="permission_boundary")
    parser.add_argument("--flow", choices=["boundary", "verified_feedback"], default="boundary")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = (
        await run_verified_feedback_vertical()
        if args.flow == "verified_feedback"
        else await run_vertical(args.case)
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"M2-M3-M2 vertical: passed={result.get('passed')} "
        f"m3={result.get('m3_status')} "
        f"m2={result.get('m2_resumed_status', result.get('m2_status'))}"
    )


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["run_verified_feedback_vertical", "run_vertical"]
