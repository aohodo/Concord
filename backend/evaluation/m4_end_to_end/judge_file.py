"""Independent interaction-quality pass over saved M4 runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from core.llm_client import LLMConfig, create_llm_client
from evaluation.m2_resolution.humanity import HumanityJudge


def _write_judge_report(target: Path, rows: list[dict]) -> None:
    completed = [item for item in rows if "interaction_judge" in item]
    dimensions: dict[str, Counter[str]] = {}
    variant_dimensions: dict[str, Counter[str]] = {}
    variant_completed: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for item in completed:
        verdict = item["interaction_judge"]
        variant = str(item.get("variant", "unknown"))
        variant_completed[variant] += 1
        for name, value in verdict.get("dimensions", {}).items():
            dimensions.setdefault(name, Counter())[str(value)] += 1
            variant_dimensions.setdefault(variant, Counter())[str(value)] += 1
        labels.update(str(value) for value in verdict.get("failure_labels", []))
    summary = {
        "episodes": len(rows),
        "completed": len(completed),
        "judge_errors": len(rows) - len(completed),
        "dimensions": {name: dict(counts) for name, counts in dimensions.items()},
        "variants": {
            variant: {
                "completed": variant_completed[variant],
                "dimension_verdicts": dict(counts),
            }
            for variant, counts in sorted(variant_dimensions.items())
        },
        "failure_labels": dict(labels),
    }
    (target.parent / "interaction_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# M4 Interaction-quality Audit",
        "",
        (
            "This is an independent LLM-as-Judge view of visible interaction behavior. "
            "It is auxiliary evidence and cannot override environment-grounded outcomes."
        ),
        "",
        f"- Episodes: {len(rows)}",
        f"- Completed judgments: {len(completed)}",
        f"- Judge errors: {len(rows) - len(completed)}",
        "",
        "## Dimensions",
        "",
        "| dimension | PASS | PARTIAL | FAIL | NOT_APPLICABLE |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, counts in sorted(dimensions.items()):
        lines.append(
            f"| {name} | {counts['PASS']} | {counts['PARTIAL']} | "
            f"{counts['FAIL']} | {counts['NOT_APPLICABLE']} |"
        )
    lines.extend(
        [
            "",
            "## Variant totals across dimensions",
            "",
            "| variant | cases | PASS | PARTIAL | FAIL | NOT_APPLICABLE |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for variant, counts in sorted(variant_dimensions.items()):
        lines.append(
            f"| {variant} | {variant_completed[variant]} | {counts['PASS']} | "
            f"{counts['PARTIAL']} | {counts['FAIL']} | "
            f"{counts['NOT_APPLICABLE']} |"
        )
    lines.extend(["", "## Failure labels", ""])
    if labels:
        lines.extend(f"- `{name}`: {count}" for name, count in labels.most_common())
    else:
        lines.append("- None")
    lines.append("")
    (target.parent / "M4_INTERACTION_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


async def judge_rows(
    rows: list[dict], *, judge: HumanityJudge, concurrency: int = 4
) -> list[dict]:
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 12)))

    async def guarded(row: dict) -> dict:
        async with semaphore:
            try:
                trajectory = {
                    "user_condition": row.get("user_condition"),
                    "turns": [
                        {
                            "raw_user_input": item.get("raw_user_input"),
                            "response": item.get("response", {}).get("response"),
                            "case_status": item.get("response", {}).get("case_status"),
                            "interaction_action": item.get("response", {}).get(
                                "interaction_action"
                            ),
                            "resolution_status": item.get("response", {}).get(
                                "resolution_status"
                            ),
                        }
                        for item in row.get("turns", [])
                    ],
                }
                runtime = {
                    "passed": row.get("passed"),
                    "final_phase": row.get("final_phase"),
                    "final_status": row.get("final_status"),
                    "failures": row.get("failures", []),
                    "events": row.get("case_snapshot", {}).get("events", []),
                    "tool_events": row.get("tool_trace", {})
                    .get("trace", {})
                    .get("events", []),
                }
                verdict = await judge.judge(
                    case_id=str(row.get("case_id", "")),
                    user_trajectory=trajectory,
                    runtime_result=runtime,
                )
                return {**row, "interaction_judge": verdict.model_dump(mode="json")}
            except Exception as exc:  # noqa: BLE001 - preserve raw run on judge failure
                return {
                    **row,
                    "interaction_judge_error": f"{type(exc).__name__}:{exc}",
                }

    return await asyncio.gather(*(guarded(row) for row in rows))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Judge saved M4 interaction traces")
    parser.add_argument("--input", required=True, nargs="+")
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--episode-id", action="append", default=[])
    args = parser.parse_args()
    load_dotenv()
    rows = [
        json.loads(line)
        for input_path in args.input
        for line in Path(input_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.episode_id:
        selected = set(args.episode_id)
        rows = [item for item in rows if item.get("episode_id") in selected]
    cfg = LLMConfig.from_env()
    client = create_llm_client(cfg)
    try:
        judged = await judge_rows(
            rows,
            judge=HumanityJudge(client, cfg.model),
            concurrency=args.concurrency,
        )
    finally:
        await client.close()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in judged),
        encoding="utf-8",
    )
    _write_judge_report(target, judged)
    completed = sum("interaction_judge" in item for item in judged)
    print(f"M4 interaction judge: {completed}/{len(judged)}")


if __name__ == "__main__":
    asyncio.run(main())
