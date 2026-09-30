"""Real-model M5 longitudinal episodes through the product HTTP boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from evaluation.m4_end_to_end.catalog import load_episodes
from evaluation.m4_end_to_end.runner import M4EpisodeRun, evaluate_episodes

from .call_chain_audit import write_call_chain_report

M5_EPISODE_IDS = {
    "vpn_stale_credential__self_correction",
    "saas_webhook_secret__fragmented_novice",
    "education_sso_clock_drift__deadline_result_first",
    "logistics_spooler_backlog__self_correction",
    "write_committed_response_lost__self_correction",
    "asynchronous_recovery__deadline_result_first",
    "permission_boundary__fragmented_novice",
    "specialist_recovery_after_tool_failures__self_correction",
}


class M5LongEpisodeRun(BaseModel):
    episode: M4EpisodeRun
    lifecycle_events: list[dict[str, Any]] = Field(default_factory=list)
    durable_jobs: list[dict[str, Any]] = Field(default_factory=list)
    effective_event_count: int = 0
    recovery_evidence: list[str] = Field(default_factory=list)
    governance_failures: list[str] = Field(default_factory=list)


async def _governance_state(
    client: httpx.AsyncClient, run: M4EpisodeRun, user_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_response, job_response = await asyncio.gather(
        client.get(
            f"/cases/{run.case_id}/events",
            params={"user_id": user_id, "tenant_id": "local"},
        ),
        client.get(
            f"/cases/{run.case_id}/jobs",
            params={"user_id": user_id, "tenant_id": "local"},
        ),
    )
    events = event_response.json().get("events", []) if event_response.status_code == 200 else []
    jobs = job_response.json().get("jobs", []) if job_response.status_code == 200 else []
    return events, jobs


async def run_m5_episodes(
    *,
    base_url: str,
    concurrency: int = 4,
    variant: str = "v2_full",
) -> list[M5LongEpisodeRun]:
    episodes = [
        item for item in load_episodes() if item.episode_id in M5_EPISODE_IDS
    ]
    run_id = f"m5-long-{variant}-{uuid4().hex[:8]}"
    runs = await evaluate_episodes(
        episodes,
        base_url=base_url,
        variant=variant,
        concurrency=concurrency,
        run_id=run_id,
    )
    user_by_episode = {
        item.episode_id: f"{item.user_id}-{run_id}" for item in episodes
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        governance = await asyncio.gather(
            *[
                _governance_state(client, run, user_by_episode[run.episode_id])
                for run in runs
            ]
        )
    output: list[M5LongEpisodeRun] = []
    for run, (events, jobs) in zip(runs, governance, strict=True):
        recovery = []
        if any(item.get("event_type") == "durable_job_enqueued" for item in events):
            recovery.append("durable_job_persisted")
        if any(item.get("event_type") == "durable_job_completed" for item in events):
            recovery.append("durable_job_completed")
        if run.case_snapshot.get("context_summary"):
            recovery.append("compact_context_available")
        failures = []
        if any(item.get("status") == "running" for item in jobs):
            failures.append("UNSETTLED_RUNNING_JOB")
        if any(
            item.get("event_type") == "m3_result_discarded"
            and item.get("status") != "stale"
            for item in events
        ):
            failures.append("INVALID_STALE_RESULT_STATUS")
        output.append(
            M5LongEpisodeRun(
                episode=run,
                lifecycle_events=events,
                durable_jobs=jobs,
                effective_event_count=len(events),
                recovery_evidence=recovery,
                governance_failures=failures,
            )
        )
    return output


def save_m5_report(output_dir: str | Path, runs: list[M5LongEpisodeRun]) -> None:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "raw_long_episodes.jsonl").open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(run.model_dump_json() + "\n")
    snapshots = [
        {**run.episode.case_snapshot, "events": run.lifecycle_events} for run in runs
    ]
    write_call_chain_report(snapshots, output)
    total = len(runs)
    passed = sum(run.episode.passed and not run.governance_failures for run in runs)
    resolved = sum(run.episode.final_status == "resolved" for run in runs)
    human_required = sum(run.episode.final_status == "human_required" for run in runs)
    events = sum(run.effective_event_count for run in runs)
    jobs = sum(len(run.durable_jobs) for run in runs)
    lines = [
        "# M5 Long-Horizon Case Governance Report",
        "",
        f"- Episodes: {total}",
        f"- Outcome + governance pass: {passed}/{total}",
        f"- Resolved: {resolved}",
        f"- Human-required boundary: {human_required}",
        f"- Persisted lifecycle events: {events}",
        f"- Durable asynchronous jobs: {jobs}",
        "",
        "| episode | outcome | events | jobs | governance failures |",
        "|---|---|---:|---:|---|",
    ]
    for run in runs:
        lines.append(
            f"| {run.episode.episode_id} | {run.episode.final_status} | "
            f"{run.effective_event_count} | {len(run.durable_jobs)} | "
            f"{', '.join(run.governance_failures) or '-'} |"
        )
    lines.extend(
        [
            "",
            (
                "Restart recovery and exactly-once replay are additionally exercised by "
                "deterministic integration tests that close and reopen the SQLite runtime. "
                "Environment outcome remains the success authority."
            ),
            "",
        ]
    )
    (output / "M5_LONG_HORIZON_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "episodes": total,
                "passed": passed,
                "resolved": resolved,
                "human_required": human_required,
                "lifecycle_events": events,
                "durable_jobs": jobs,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = ["M5LongEpisodeRun", "run_m5_episodes", "save_m5_report"]
