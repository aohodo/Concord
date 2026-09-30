"""Run reproducible multi-turn episodes through the public /chat API."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .environment import ControlledEnvironment
from .evidence_contracts import catalog_entry
from .judge import judge_results
from .metrics import compute_metrics
from .rules import evaluate_rules
from .schemas import EpisodeResult, EpisodeSpec, EpisodeTurn
from .simulator import ScriptedUserSimulator

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parents[1] / "backend" / ".env")


def load_specs(path: Path) -> list[EpisodeSpec]:
    return [
        EpisodeSpec.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def run_episode(
    client: httpx.AsyncClient,
    spec: EpisodeSpec,
    *,
    variant: str,
    run_number: int,
    run_id: str = "default",
) -> EpisodeResult:
    environment = ControlledEnvironment(spec.environment)
    simulator = ScriptedUserSimulator(spec, environment)
    initial_user_state = simulator.initial_state.model_copy(deep=True)
    turns: list[EpisodeTurn] = []
    message = spec.initial_message
    revealed_for_turn: list[str] = []
    note_for_turn = "initial user report"
    observations_for_turn: dict[str, Any] = {}
    stop_reason = "max_turns"
    unsafe_actions: list[str] = []
    try:
        for turn_number in range(1, spec.max_turns + 1):
            response = await client.post(
                "/chat",
                json={
                    "message": message,
                    "user_id": (
                        f"eval-{run_id}-{variant}-{run_number}-{spec.episode_id}"
                    ),
                    "conv_id": (
                        f"episode-{run_id}-{variant}-{run_number}-{spec.episode_id}"
                    ),
                    "evaluation_variant": variant,
                    "evaluation_context": {
                        "domain": spec.domain,
                        "evidence_catalog": [
                            catalog_entry(fact) for fact in spec.hidden_facts
                        ],
                        "available_actions": [
                            {
                                "action_id": action.action_id,
                                "aliases": action.aliases,
                            }
                            for action in (
                                spec.environment.actions if spec.environment else []
                            )
                            if action.safe
                        ],
                    },
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            problem_state = data.get("problem_state") or {}
            history = problem_state.get("formulation_history") or []
            resolved_in_message = {
                key
                for entry in history[-1:]
                for key in entry.get("evidence_resolved", [])
            }
            known_fact_keys = {fact.key for fact in spec.hidden_facts}
            resolved_in_message &= known_fact_keys
            simulator.mark_revealed(resolved_in_message)
            observed_fact_keys = list(
                dict.fromkeys([*revealed_for_turn, *sorted(resolved_in_message)])
            )
            turn = EpisodeTurn(
                turn_number=turn_number,
                user_message=message,
                system_response=str(data.get("response", "")),
                action=str(data.get("interaction_action", "")),
                addressed_need=str(data.get("addressed_need", "")),
                target_evidence=str(data.get("target_evidence", "")),
                case_ready=bool(data.get("case_ready")),
                case_status=str(data.get("case_status", "")),
                problem_state=problem_state,
                latency_ms=float(data.get("latency_ms") or 0.0),
                contract_compliant=bool(data.get("contract_compliant", True)),
                contract_violations=list(data.get("contract_violations") or []),
                revealed_fact_keys=observed_fact_keys,
                simulator_note=note_for_turn,
                environment_observations=observations_for_turn,
                m1_model_calls=int(data.get("m1_model_calls") or 0),
                control_priors=dict(data.get("control_priors") or {}),
            )
            unsafe_actions.extend(
                environment.unsafe_actions_mentioned(turn.system_response)
            )
            turn.executed_action = environment.execute_from_response(
                turn.system_response
            )
            turns.append(turn)
            if turn.case_ready:
                stop_reason = "case_ready"
                break
            if simulator.disengaged:
                stop_reason = "user_abandoned"
                break
            (
                message,
                revealed_for_turn,
                note_for_turn,
                observations_for_turn,
            ) = simulator.respond(
                turn_number=turn_number,
                system_response=turn.system_response,
                action=turn.action,
                target_evidence=turn.target_evidence,
                contract_violations=turn.contract_violations,
            )
        final_state_match = environment.final_state_matches()
        checks = evaluate_rules(spec, turns, final_state_match, unsafe_actions)
        metrics = compute_metrics(
            spec,
            turns,
            initial_user_state,
            simulator.state,
            final_state_match=final_state_match,
            rule_checks=checks,
            unsafe_actions=unsafe_actions,
            user_disengaged=simulator.disengaged,
        )
        return EpisodeResult(
            spec=spec,
            turns=turns,
            metrics=metrics,
            final_user_state=simulator.state,
            stop_reason=stop_reason,
            variant=variant,
            run_number=run_number,
            rule_checks=checks,
        )
    except Exception as exc:  # noqa: BLE001 - preserve a complete failed episode
        metrics = compute_metrics(
            spec,
            turns,
            initial_user_state,
            simulator.state,
            user_disengaged=simulator.disengaged,
        )
        return EpisodeResult(
            spec=spec,
            turns=turns,
            metrics=metrics,
            final_user_state=simulator.state,
            stop_reason="exception",
            exception=f"{type(exc).__name__}: {exc}",
            variant=variant,
            run_number=run_number,
        )


def write_outputs(results: list[EpisodeResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw_episodes.jsonl"
    raw_path.write_text(
        "".join(item.model_dump_json() + "\n" for item in results),
        encoding="utf-8",
    )
    metric_fields = list(type(results[0].metrics).model_fields) if results else []
    with (output_dir / "episode_metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "episode_id",
                "domain",
                "variant",
                "run_number",
                "stop_reason",
                "exception",
                *metric_fields,
            ],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "episode_id": item.spec.episode_id,
                    "domain": item.spec.domain,
                    "variant": item.variant,
                    "run_number": item.run_number,
                    "stop_reason": item.stop_reason,
                    "exception": item.exception or "",
                    **item.metrics.model_dump(),
                }
            )
    successful = [item for item in results if item.exception is None]
    ready = sum(item.metrics.reached_case_ready for item in successful)
    premature = sum(item.metrics.premature_ready for item in successful)
    pass_k = calculate_pass_k(results)
    (output_dir / "pass_k.json").write_text(
        json.dumps(pass_k, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    by_variant: dict[str, list[EpisodeResult]] = defaultdict(list)
    for item in results:
        by_variant[item.variant].append(item)
    comparison_rows = []
    for variant, items in by_variant.items():
        comparison_rows.append(
            {
                "variant": variant,
                "runs": len(items),
                "objective_pass": sum(item.metrics.objective_pass for item in items),
                "premature_ready": sum(item.metrics.premature_ready for item in items),
                "fact_distortions": sum(
                    item.metrics.fact_distortion_count for item in items
                ),
                "user_abandoned": sum(item.metrics.user_abandoned for item in items),
                "avg_burden": round(
                    sum(item.metrics.user_burden_score for item in items) / len(items),
                    3,
                ),
                "judge_disagreement": sum(item.judge_disagreement for item in items),
                "judged_runs": sum(bool(item.subjective_judges) for item in items),
                "professional_but_distorted": sum(
                    item.style_truth_conflict for item in items
                ),
            }
        )
    with (output_dir / "variant_comparison.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison_rows[0]))
        writer.writeheader()
        writer.writerows(comparison_rows)
    report = [
        "# Concord V2 交互式 Episode 评测报告",
        "",
        f"- 生成时间：{datetime.now(UTC).isoformat()}",
        f"- Episode：{len(results)}；无异常：{len(successful)}。",
        f"- 到达 CASE_READY：{ready}；过早 CASE_READY：{premature}。",
        "- pass^k：" + json.dumps(pass_k, ensure_ascii=False),
        "- 四变体汇总：" + json.dumps(comparison_rows, ensure_ascii=False),
        "",
        "本报告评价多轮 Case 收敛过程，不把语言流畅度当作问题解决能力。",
        "完整轨迹见 `raw_episodes.jsonl`，逐 Episode 指标见 `episode_metrics.csv`。",
        "",
    ]
    (output_dir / "EPISODE_REPORT.md").write_text("\n".join(report), encoding="utf-8")


def calculate_pass_k(results: list[EpisodeResult]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for item in results:
        grouped[item.variant][item.spec.episode_id].append(item.metrics.objective_pass)
    output: dict[str, dict[str, float]] = {}
    for variant, episodes in grouped.items():
        max_k = max((len(values) for values in episodes.values()), default=0)
        output[variant] = {}
        for k in range(1, max_k + 1):
            eligible = [values for values in episodes.values() if len(values) >= k]
            output[variant][f"pass^{k}"] = (
                round(sum(all(values[:k]) for values in eligible) / len(eligible), 4)
                if eligible
                else 0.0
            )
    return output


async def main_async(args: argparse.Namespace) -> None:
    specs = load_specs(Path(args.specs))
    if args.episode_ids:
        requested = {
            item.strip() for item in args.episode_ids.split(",") if item.strip()
        }
        known = {item.episode_id for item in specs}
        if missing := requested - known:
            raise ValueError(f"unknown episode ids: {sorted(missing)}")
        specs = [item for item in specs if item.episode_id in requested]
    if args.episode_limit:
        specs = specs[: args.episode_limit]
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    allowed_variants = {
        "v2_full",
        "v2_no_user_state",
        "v2_no_epistemic_separation",
    }
    unknown = set(variants) - allowed_variants
    if unknown:
        raise ValueError(f"unknown variants: {sorted(unknown)}")
    if args.plan:
        print(
            json.dumps(
                {
                    "episodes": len(specs),
                    "variants": variants,
                    "repetitions": args.repetitions,
                    "episode_runs": len(specs) * len(variants) * args.repetitions,
                    "maximum_chat_turns": sum(item.max_turns for item in specs)
                    * len(variants)
                    * args.repetitions,
                    "llm_judge": not args.skip_judge,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw_episodes.jsonl"
    existing: list[EpisodeResult] = []
    if raw_path.exists() and not args.resume and not args.overwrite:
        raise RuntimeError("results already exist; use --resume or --overwrite")
    if args.resume and args.overwrite:
        raise RuntimeError("--resume and --overwrite are mutually exclusive")
    if args.overwrite and raw_path.exists():
        raw_path.unlink()
    if args.resume and raw_path.exists():
        existing = [
            EpisodeResult.model_validate_json(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    completed = {
        (item.spec.episode_id, item.variant, item.run_number) for item in existing
    }
    append_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=httpx.Timeout(args.timeout),
        trust_env=False,
    ) as client:
        health = await client.get("/health")
        health.raise_for_status()

        async def bounded(
            spec: EpisodeSpec, variant: str, run_number: int
        ) -> EpisodeResult:
            async with semaphore:
                result = await run_episode(
                    client,
                    spec,
                    variant=variant,
                    run_number=run_number,
                    run_id=args.run_id,
                )
                print(
                    f"episode={spec.episode_id} variant={variant} run={run_number} "
                    f"stop={result.stop_reason} "
                    f"turns={len(result.turns)}",
                    flush=True,
                )
                async with append_lock:
                    with raw_path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(result.model_dump_json() + "\n")
                return result

        pending = [
            (spec, variant, run_number)
            for spec in specs
            for variant in variants
            for run_number in range(1, args.repetitions + 1)
            if (spec.episode_id, variant, run_number) not in completed
        ]
        new_results = await asyncio.gather(
            *(
                bounded(spec, variant, run_number)
                for spec, variant, run_number in pending
            )
        )
        results = [*existing, *new_results]
        if not args.skip_judge:
            for result in results:
                result.subjective_judges = []
            await judge_results(results)
    write_outputs(results, output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--specs", default=str(HERE / "benchmark_30.jsonl"))
    parser.add_argument("--output-dir", default=str(HERE / "results"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--episode-limit", type=int, default=0)
    parser.add_argument(
        "--episode-ids",
        default="",
        help="comma-separated episode ids; useful for focused regression checks",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--run-id",
        default="default",
        help="isolates LangGraph/memory state between evaluation executions",
    )
    parser.add_argument(
        "--variants",
        default=("v2_full,v2_no_user_state,v2_no_epistemic_separation"),
    )
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.concurrency < 1 or args.repetitions < 1 or args.episode_limit < 0:
        parser.error(
            "concurrency/repetitions must be positive; episode-limit cannot be negative"
        )
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
