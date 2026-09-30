"""Small real-model, cross-Case M7.5 reuse chain with simulated world truth."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from core.adaptive_resolution import AdaptiveResolutionService, ResolutionStatus
from core.case_orchestration import CaseContextSummary, CasePhase, CaseRunSnapshot
from core.continual_improvement import (
    ContinualImprovementRepository,
    ContinualImprovementService,
)
from core.llm_client import LLMConfig, create_llm_client
from evaluation.m2_resolution import (
    build_resolution_context,
    load_scenario_catalog,
    seed_scenario,
)
from infrastructure.simulation import ScenarioRuntime, build_simulation_tools
from infrastructure.tool_runtime import AdaptiveToolRuntime

DEFAULT_IDS = (
    "vpn_stale_credential",
    "education_sso_clock_drift",
    "logistics_spooler_backlog",
)


def _snapshot(
    *,
    case_id: str,
    context: dict[str, Any],
    result: Any,
) -> CaseRunSnapshot:
    resolved = result.status is ResolutionStatus.RESOLVED
    return CaseRunSnapshot(
        case_id=case_id,
        conv_id=f"conv:{case_id}",
        user_id="m7-real-evaluator",
        tenant_id="m7-real",
        phase=CasePhase.RESOLVED if resolved else CasePhase.ERROR,
        status=result.status.value,
        response=result.response,
        m1_thread_id=f"m1:{case_id}",
        m2_thread_id=result.thread_id,
        case_revision=1,
        m1={"resolution_context": context},
        m2=result.model_dump(mode="json"),
        context_summary=CaseContextSummary(goal=context.get("goal", {})),
    )


async def run_real_chain(ids: tuple[str, ...], output: Path) -> list[dict[str, Any]]:
    backend_root = Path(__file__).resolve().parents[2]
    load_dotenv(backend_root / ".env")
    cfg = LLMConfig.from_env()
    client = create_llm_client(cfg)
    scenarios = {item.scenario_id: item for item in load_scenario_catalog()}
    scenario_runtime = ScenarioRuntime()
    tool_runtime = AdaptiveToolRuntime(scenarios=scenario_runtime)
    for spec in build_simulation_tools(scenario_runtime):
        tool_runtime.registry.register(spec)
    resolution = AdaptiveResolutionService(
        checkpointer=InMemorySaver(),
        tool_runtime=tool_runtime,
        client=client,
        model=cfg.model,
    )
    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="concord-m7-5-") as temporary:
            repository = ContinualImprovementRepository(Path(temporary) / "m7.sqlite3")
            await repository.setup()
            continual = ContinualImprovementService(repository)
            try:
                for scenario_id in ids:
                    scenario = scenarios[scenario_id]
                    source_case = f"m7-real:{scenario_id}:source"
                    target_case = f"m7-real:{scenario_id}:target"
                    source_context = build_resolution_context(
                        scenario, case_id=source_case
                    )
                    source_context["domain_prior"] = scenario.domain
                    await seed_scenario(
                        scenario, case_id=source_case, tool_runtime=tool_runtime
                    )
                    started = time.perf_counter()
                    source_result = await resolution.resolve(
                        resolution_context=source_context,
                        permissions=scenario.permissions,
                    )
                    source_ms = round((time.perf_counter() - started) * 1000, 3)
                    outcome, _, _ = await continual.observe_case(
                        _snapshot(
                            case_id=source_case,
                            context=source_context,
                            result=source_result,
                        )
                    )

                    target_context = build_resolution_context(
                        scenario, case_id=target_case
                    )
                    target_context["domain_prior"] = scenario.domain
                    target_profile = resolution.runtime_tool_profile(
                        case_id=target_case,
                        permissions=scenario.permissions,
                        tenant_id="m7-real",
                    )
                    target_context, matches = await continual.enrich_resolution_context(
                        target_context,
                        tenant_id="m7-real",
                        runtime_tool_profile=target_profile,
                    )
                    await seed_scenario(
                        scenario, case_id=target_case, tool_runtime=tool_runtime
                    )
                    started = time.perf_counter()
                    target_result = await resolution.resolve(
                        resolution_context=target_context,
                        permissions=scenario.permissions,
                    )
                    target_ms = round((time.perf_counter() - started) * 1000, 3)
                    target_outcome, _, _ = await continual.observe_case(
                        _snapshot(
                            case_id=target_case,
                            context=target_context,
                            result=target_result,
                        )
                    )
                    rows.append(
                        {
                            "scenario_id": scenario_id,
                            "domain": scenario.domain,
                            "model": cfg.model,
                            "source": {
                                "status": source_result.status.value,
                                "outcome": outcome.verdict.value,
                                "model_calls": source_result.episode_decisions,
                                "tool_calls": len(source_result.tool_history),
                                "latency_ms": source_ms,
                            },
                            "target": {
                                "status": target_result.status.value,
                                "outcome": target_outcome.verdict.value,
                                "model_calls": target_result.episode_decisions,
                                "tool_calls": len(target_result.tool_history),
                                "latency_ms": target_ms,
                                "retrieved": target_result.retrieved_experience_ids,
                                "considered": target_result.considered_experience_ids,
                                "adopted": target_result.adopted_experience_ids,
                                "verified": target_result.verified_experience_ids,
                            },
                            "match_scores": [item.score for item in matches],
                        }
                    )
            finally:
                await repository.close()
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw_runs.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )
    source_mean = sum(item["source"]["latency_ms"] for item in rows) / max(
        len(rows), 1
    )
    target_mean = sum(item["target"]["latency_ms"] for item in rows) / max(
        len(rows), 1
    )
    (output / "REAL_CHAIN_REPORT.md").write_text(
        "\n".join(
            [
                "# M7.5 Real-Model Reuse Chain",
                "",
                f"- Model: `{cfg.model}`",
                f"- Domains: {len(rows)}",
                f"- Verified source outcomes: {sum(item['source']['outcome'] == 'verified_success' for item in rows)}/{len(rows)}",
                f"- Verified target outcomes: {sum(item['target']['outcome'] == 'verified_success' for item in rows)}/{len(rows)}",
                f"- Retrieved → considered → adopted → verified: {sum(bool(item['target']['verified']) for item in rows)}/{len(rows)} complete chains",
                f"- Mean source latency: {source_mean:.3f} ms",
                f"- Mean target latency: {target_mean:.3f} ms",
                "",
                (
                    "The target Case always re-executed and independently verified the current "
                    "world. This smoke test proves attribution and reverification wiring across "
                    "three domains; it does not prove a success-rate, latency, or tool-call uplift. "
                    "In this sample, experience reuse did not reduce model calls or tool calls and "
                    "target latency was higher."
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="*", default=list(DEFAULT_IDS))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("results") / "m7_5_real",
    )
    args = parser.parse_args()
    rows = asyncio.run(run_real_chain(tuple(args.ids), args.output.resolve()))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(item["target"]["outcome"] == "verified_success" for item in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
