"""Persistent outcome feedback for M3 transactive-memory routing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from core.multi_agent_collaboration.models import AgentOutcomeFeedback, AgentPerformance


class AgentPerformanceStore:
    """Store verified downstream outcomes, never an agent's self-confidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ready = False
        self._setup_lock = asyncio.Lock()

    async def setup(self) -> None:
        if self._ready:
            return
        async with self._setup_lock:
            if self._ready:
                return
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_performance (
                        agent_id TEXT PRIMARY KEY,
                        successes INTEGER NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        samples INTEGER NOT NULL DEFAULT 0,
                        total_latency_ms REAL NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                columns = {
                    str(row[1])
                    for row in await (await db.execute("PRAGMA table_info(agent_performance)")).fetchall()
                }
                if "verified_progress_total" not in columns:
                    await db.execute(
                        "ALTER TABLE agent_performance ADD COLUMN "
                        "verified_progress_total REAL NOT NULL DEFAULT 0"
                    )
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agent_context_performance (
                        agent_id TEXT NOT NULL,
                        capability TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        successes INTEGER NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        samples INTEGER NOT NULL DEFAULT 0,
                        total_latency_ms REAL NOT NULL DEFAULT 0,
                        verified_progress_total REAL NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(agent_id, capability, task_type)
                    );
                    CREATE TABLE IF NOT EXISTS agent_outcome_events (
                        collaboration_id TEXT NOT NULL,
                        assignment_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(collaboration_id, assignment_id)
                    );
                    """
                )
                await db.commit()
            self._ready = True

    async def snapshot(self) -> dict[str, AgentPerformance]:
        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT agent_id, successes, failures, samples,
                              total_latency_ms, verified_progress_total
                       FROM agent_performance"""
                )
            ).fetchall()
        return {
            str(row[0]): AgentPerformance(
                agent_id=str(row[0]),
                successes=int(row[1]),
                failures=int(row[2]),
                samples=int(row[3]),
                average_latency_ms=(float(row[4]) / int(row[3]) if row[3] else 0.0),
                verified_progress_total=float(row[5]),
            )
            for row in rows
        }

    async def record(self, feedback: AgentOutcomeFeedback) -> AgentPerformance:
        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            if feedback.collaboration_id and feedback.assignment_id:
                inserted = await db.execute(
                    """INSERT OR IGNORE INTO agent_outcome_events
                       (collaboration_id, assignment_id, agent_id, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        feedback.collaboration_id,
                        feedback.assignment_id,
                        feedback.agent_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                if inserted.rowcount == 0:
                    await db.rollback()
                    return (await self.snapshot())[feedback.agent_id]
            await db.execute(
                """
                INSERT INTO agent_performance
                    (agent_id, successes, failures, samples, total_latency_ms,
                     updated_at, verified_progress_total)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    successes = successes + excluded.successes,
                    failures = failures + excluded.failures,
                    samples = samples + 1,
                    total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                    verified_progress_total = verified_progress_total
                        + excluded.verified_progress_total,
                    updated_at = excluded.updated_at
                """,
                (
                    feedback.agent_id,
                    1 if feedback.successful else 0,
                    0 if feedback.successful else 1,
                    feedback.latency_ms,
                    datetime.now(UTC).isoformat(),
                    feedback.progress_delta,
                ),
            )
            for capability in feedback.capabilities or {"general"}:
                await db.execute(
                    """
                    INSERT INTO agent_context_performance
                        (agent_id, capability, task_type, successes, failures,
                         samples, total_latency_ms, verified_progress_total, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(agent_id, capability, task_type) DO UPDATE SET
                        successes = successes + excluded.successes,
                        failures = failures + excluded.failures,
                        samples = samples + 1,
                        total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                        verified_progress_total = verified_progress_total
                            + excluded.verified_progress_total,
                        updated_at = excluded.updated_at
                    """,
                    (
                        feedback.agent_id,
                        capability,
                        feedback.task_type,
                        int(feedback.successful),
                        int(not feedback.successful),
                        feedback.latency_ms,
                        feedback.progress_delta,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            await db.commit()
        return (await self.snapshot())[feedback.agent_id]

    async def context_snapshot(self) -> dict[str, AgentPerformance]:
        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT agent_id, capability, task_type, successes, failures,
                              samples, total_latency_ms, verified_progress_total
                       FROM agent_context_performance"""
                )
            ).fetchall()
        return {
            f"{row[0]}|{row[1]}|{row[2]}": AgentPerformance(
                agent_id=str(row[0]),
                successes=int(row[3]),
                failures=int(row[4]),
                samples=int(row[5]),
                average_latency_ms=float(row[6]) / int(row[5]) if row[5] else 0.0,
                verified_progress_total=float(row[7]),
            )
            for row in rows
        }


class InMemoryAgentPerformanceStore:
    def __init__(self) -> None:
        self._items: dict[str, AgentPerformance] = {}
        self._lock = asyncio.Lock()
        self._context_items: dict[str, AgentPerformance] = {}
        self._outcomes: set[tuple[str, str]] = set()

    async def setup(self) -> None:
        return None

    async def snapshot(self) -> dict[str, AgentPerformance]:
        async with self._lock:
            return {key: item.model_copy(deep=True) for key, item in self._items.items()}

    async def record(self, feedback: AgentOutcomeFeedback) -> AgentPerformance:
        async with self._lock:
            outcome_key = (feedback.collaboration_id, feedback.assignment_id)
            if all(outcome_key) and outcome_key in self._outcomes:
                return self._items[feedback.agent_id].model_copy(deep=True)
            if all(outcome_key):
                self._outcomes.add(outcome_key)
            current = self._items.get(
                feedback.agent_id, AgentPerformance(agent_id=feedback.agent_id)
            )
            total_latency = current.average_latency_ms * current.samples + feedback.latency_ms
            updated = AgentPerformance(
                agent_id=feedback.agent_id,
                successes=current.successes + int(feedback.successful),
                failures=current.failures + int(not feedback.successful),
                samples=current.samples + 1,
                average_latency_ms=total_latency / (current.samples + 1),
                verified_progress_total=(
                    current.verified_progress_total + feedback.progress_delta
                ),
            )
            self._items[feedback.agent_id] = updated
            for capability in feedback.capabilities or {"general"}:
                key = f"{feedback.agent_id}|{capability}|{feedback.task_type}"
                observed = self._context_items.get(
                    key, AgentPerformance(agent_id=feedback.agent_id)
                )
                context_total_latency = (
                    observed.average_latency_ms * observed.samples + feedback.latency_ms
                )
                self._context_items[key] = AgentPerformance(
                    agent_id=feedback.agent_id,
                    successes=observed.successes + int(feedback.successful),
                    failures=observed.failures + int(not feedback.successful),
                    samples=observed.samples + 1,
                    average_latency_ms=context_total_latency / (observed.samples + 1),
                    verified_progress_total=(
                        observed.verified_progress_total + feedback.progress_delta
                    ),
                )
            return updated.model_copy(deep=True)

    async def context_snapshot(self) -> dict[str, AgentPerformance]:
        async with self._lock:
            return {
                key: item.model_copy(deep=True)
                for key, item in self._context_items.items()
            }


__all__ = ["AgentPerformanceStore", "InMemoryAgentPerformanceStore"]
