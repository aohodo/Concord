"""Run a small real-model M6.5 interaction-control batch through ``POST /chat``.

This is a wiring and control-contract experiment, not an LLM-as-judge quality score.
The two separately recorded text/image smoke runs plus this ten-case batch form the
12-case M6.5 targeted evidence set.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class TargetedCase:
    case_id: str
    user_condition: str
    message: str


CASES = (
    TargetedCase(
        "m65-batch-001",
        "fragmented",
        "昨晚还能用。今天先是一直转圈，后来提示认证失败。我重启过两次，网页正常，"
        "中间好像也改过公司密码，现在就是进不了内网。",
    ),
    TargetedCase(
        "m65-batch-002",
        "half_expert",
        "肯定是 DNS 污染，VPN 报 691，但我能 ping 通百度。别让我再改 DNS，先判断我的"
        "这个结论有没有证据。",
    ),
    TargetedCase(
        "m65-batch-003",
        "frustrated",
        "我已经重启八遍、卸载重装一遍了，还是提示登录失败。别再让我重复这些，告诉我"
        "现在最值得查哪一项。",
    ),
    TargetedCase(
        "m65-batch-004",
        "low_control",
        "公司那个连内网的东西坏了，我怕点错把电脑弄坏。你一次只告诉我一个安全步骤，"
        "我做完再回复你。",
    ),
    TargetedCase(
        "m65-batch-005",
        "expert",
        "Windows 11 IKEv2 VPN 返回 691，公网正常，域密码今早轮换，凭据管理器仍有旧条目；"
        "请给出最小区分性检查，不要先下根因结论。",
    ),
    TargetedCase(
        "m65-batch-006",
        "procedural_only",
        "教程让我点连接、输入账号密码，我都照做了但还是 691。我不知道日志在哪里，"
        "请直接告诉我下一步点哪里。",
    ),
    TargetedCase(
        "m65-batch-007",
        "cross_domain_transfer",
        "我是做前端的，网页能打开说明网络层肯定没问题，所以 VPN 只能是服务端挂了。"
        "请先验证这个推断，不要当成事实。",
    ),
    TargetedCase(
        "m65-batch-008",
        "low_patience",
        "又连不上了，别讲原理，也别给我十步清单。先给一个最可能恢复工作的动作。",
    ),
    TargetedCase(
        "m65-batch-009",
        "multi_issue",
        "VPN 登录失败，邮箱也一直弹密码，内网页面打不开；这些可能是一件事也可能不是。"
        "先帮我抓主线，别三个问题各说一遍。",
    ),
    TargetedCase(
        "m65-batch-010",
        "uncertain_goal",
        "我也说不清要修什么，反正等会儿开会要展示公司系统。现在电脑上那个连接按钮"
        "一直失败，我只想先把演示做完。",
    ),
)


def _contract_failures(response: dict[str, Any], console: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not response.get("case_id"):
        failures.append("MISSING_CASE_ID")
    if int(response.get("request_generation", 0)) < 1:
        failures.append("MISSING_REQUEST_GENERATION")
    if response.get("case_phase") in {"error", "cancelled", "timed_out"}:
        failures.append("UNEXPECTED_TERMINAL_PHASE")
    stages = response.get("stage_latency_ms", {})
    if not stages.get("m1_formulation"):
        failures.append("MISSING_M1_STAGE_LATENCY")
    if response.get("case_ready") and not stages.get("m2_resolution"):
        failures.append("MISSING_M2_STAGE_LATENCY")
    human = console.get("human_collaboration", {})
    budget = human.get("budget", {})
    usage = human.get("usage", {})
    if int(usage.get("questions_asked", 0)) > int(budget.get("max_questions_per_turn", 99)):
        failures.append("QUESTION_BUDGET_EXCEEDED")
    if int(usage.get("user_actions_requested", 0)) > int(
        budget.get("max_user_actions_per_turn", 99)
    ):
        failures.append("ACTION_BUDGET_EXCEEDED")
    progress = console.get("progress", [])
    if float(response.get("latency_ms", 0)) > 10_000 and not any(
        item.get("type") == "case_progress_heartbeat" for item in progress
    ):
        failures.append("MISSING_LONG_WAIT_PROGRESS")
    return failures


async def _run_one(
    client: httpx.AsyncClient,
    case: TargetedCase,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    runtime_case_id = f"case-m65-{uuid.uuid4().hex[:12]}"
    user_id = f"m65-{uuid.uuid4().hex[:10]}"
    payload = {
        "message": case.message,
        "user_id": user_id,
        "tenant_id": "default",
        "conv_id": f"conv-{uuid.uuid4().hex}",
        "case_id": runtime_case_id,
    }
    queued_at = time.monotonic()
    try:
        async with semaphore:
            started = time.monotonic()
            queue_wait_ms = round((started - queued_at) * 1000, 1)
            reply = await client.post("/chat", json=payload)
            reply.raise_for_status()
            response = reply.json()
            console_reply = await client.get(
                f"/cases/{runtime_case_id}/console",
                params={"user_id": user_id, "tenant_id": "default"},
            )
            console_reply.raise_for_status()
            console = console_reply.json()
        failures = _contract_failures(response, console)
        return {
            "episode_id": case.case_id,
            "runtime_case_id": runtime_case_id,
            "user_condition": case.user_condition,
            "raw_user_input": case.message,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "queue_wait_ms": queue_wait_ms,
            "request_latency_ms": response.get("latency_ms", 0),
            "case_phase": response.get("case_phase", ""),
            "case_ready": bool(response.get("case_ready")),
            "request_generation": response.get("request_generation", 0),
            "response": response.get("response", ""),
            "response_details": response.get("response_details", ""),
            "stage_latency_ms": response.get("stage_latency_ms", {}),
            "human_collaboration": console.get("human_collaboration", {}),
            "verification_status": console.get("verification_status", ""),
            "next_request": console.get("next_request", ""),
            "progress": console.get("progress", []),
            "passed": not failures,
            "failures": failures,
            "exception": None,
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        # Preserve expected infrastructure/protocol failures in raw results.
        return {
            "episode_id": case.case_id,
            "runtime_case_id": runtime_case_id,
            "user_condition": case.user_condition,
            "raw_user_input": case.message,
            "elapsed_ms": round((time.monotonic() - queued_at) * 1000, 1),
            "passed": False,
            "failures": ["RUNTIME_FAILURE"],
            "exception": f"{type(exc).__name__}: {exc}",
        }


def _save(output: Path, rows: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "runtime_batch_raw.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    fields = [
        "episode_id",
        "user_condition",
        "passed",
        "case_phase",
        "case_ready",
        "elapsed_ms",
        "queue_wait_ms",
        "request_latency_ms",
        "m1_ms",
        "m2_ms",
        "questions_asked",
        "max_questions",
        "failure_labels",
    ]
    with (output / "runtime_batch_matrix.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            human = row.get("human_collaboration", {})
            writer.writerow(
                {
                    "episode_id": row["episode_id"],
                    "user_condition": row["user_condition"],
                    "passed": row["passed"],
                    "case_phase": row.get("case_phase", ""),
                    "case_ready": row.get("case_ready", ""),
                    "elapsed_ms": row["elapsed_ms"],
                    "queue_wait_ms": row.get("queue_wait_ms", ""),
                    "request_latency_ms": row.get("request_latency_ms", ""),
                    "m1_ms": row.get("stage_latency_ms", {}).get("m1_formulation", ""),
                    "m2_ms": row.get("stage_latency_ms", {}).get("m2_resolution", ""),
                    "questions_asked": human.get("usage", {}).get("questions_asked", ""),
                    "max_questions": human.get("budget", {}).get(
                        "max_questions_per_turn", ""
                    ),
                    "failure_labels": ";".join(row["failures"]),
                }
            )
    passed = sum(bool(row["passed"]) for row in rows)
    (output / "RUNTIME_BATCH_REPORT.md").write_text(
        "\n".join(
            [
                "# M6.5 Real-model Runtime Batch",
                "",
                f"- Episodes: {len(rows)}",
                f"- Control-contract pass: {passed}/{len(rows)}",
                "- Repetitions: 1",
                "- Judge: none; pass/fail only checks runtime and explicit control contracts",
                "",
                (
                    "Semantic response quality remains in the raw records for later human or "
                    "independent-judge review. It is not folded into this contract pass rate."
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )


async def run(base_url: str, output: Path, concurrency: int) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(360.0, connect=15.0)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 4)))
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        rows = await asyncio.gather(
            *(_run_one(client, case, semaphore) for case in CASES)
        )
    _save(output, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M6.5 targeted real-model batch")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output-dir",
        default="evaluation/m6_human_collaboration/results/m6_5_targeted",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    rows = asyncio.run(run(args.base_url, Path(args.output_dir).resolve(), args.concurrency))
    print(f"M6.5 targeted runtime: {sum(row['passed'] for row in rows)}/{len(rows)} passed")


if __name__ == "__main__":
    main()
