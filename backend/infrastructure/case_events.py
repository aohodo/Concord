"""Persistent ordered inbox for user and environment events during M2."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiosqlite

from core.adaptive_resolution.models import CaseEvent, CaseEventType


class CaseEventStore:
    """SQLite event inbox with ownership binding and idempotent event IDs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup_lock = asyncio.Lock()
        self._ready = False

    async def setup(self) -> None:
        if self._ready:
            return
        async with self._setup_lock:
            if self._ready:
                return
            async with aiosqlite.connect(self.path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS case_threads (
                        thread_id TEXT PRIMARY KEY,
                        case_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        actor_id TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS case_events (
                        event_id TEXT PRIMARY KEY,
                        thread_id TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        actor_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        consumed_at TEXT,
                        UNIQUE(thread_id, sequence)
                    );
                    CREATE INDEX IF NOT EXISTS idx_case_events_pending
                    ON case_events(thread_id, consumed_at, sequence);
                    """
                )
                await db.commit()
            self._ready = True

    async def bind_thread(
        self,
        *,
        thread_id: str,
        case_id: str,
        tenant_id: str,
        actor_id: str,
        created_at: str,
    ) -> None:
        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT case_id, tenant_id, actor_id FROM case_threads WHERE thread_id = ?",
                    (thread_id,),
                )
            ).fetchone()
            expected = (case_id, tenant_id, actor_id)
            if row is not None and tuple(row) != expected:
                raise PermissionError("thread ownership or case binding does not match")
            await db.execute(
                """
                INSERT OR IGNORE INTO case_threads
                    (thread_id, case_id, tenant_id, actor_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, case_id, tenant_id, actor_id, created_at),
            )
            await db.commit()

    async def assert_owner(
        self, thread_id: str, *, tenant_id: str, actor_id: str
    ) -> tuple[str, str, str]:
        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT case_id, tenant_id, actor_id FROM case_threads WHERE thread_id = ?",
                    (thread_id,),
                )
            ).fetchone()
        if row is None:
            raise KeyError(thread_id)
        if row[1] != tenant_id or row[2] != actor_id:
            raise PermissionError("thread belongs to a different actor or tenant")
        return str(row[0]), str(row[1]), str(row[2])

    async def enqueue(self, event: CaseEvent) -> CaseEvent:
        owner = await self.assert_owner(
            event.thread_id,
            tenant_id=event.tenant_id,
            actor_id=event.actor_id,
        )
        if owner[0] != event.case_id:
            raise ValueError("event case_id does not match the bound thread")
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT sequence FROM case_events WHERE event_id = ?",
                    (event.event_id,),
                )
            ).fetchone()
            if existing is not None:
                await db.rollback()
                return event.model_copy(update={"sequence": int(existing[0])})
            row = await (
                await db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM case_events WHERE thread_id = ?",
                    (event.thread_id,),
                )
            ).fetchone()
            sequence = int(row[0])
            stored = event.model_copy(update={"sequence": sequence})
            await db.execute(
                """
                INSERT INTO case_events
                    (event_id, thread_id, case_id, tenant_id, actor_id, sequence,
                     event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.event_id,
                    stored.thread_id,
                    stored.case_id,
                    stored.tenant_id,
                    stored.actor_id,
                    stored.sequence,
                    stored.type.value,
                    json.dumps(stored.payload, ensure_ascii=False, default=str),
                    stored.created_at,
                ),
            )
            await db.commit()
        return stored

    async def drain(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[CaseEvent]:
        """Read ordered events after the graph's checkpointed sequence.

        The store deliberately does not acknowledge here.  LangGraph checkpoints
        ``last_event_sequence`` together with the merged Case state, so a crash
        before that checkpoint safely replays an idempotent event instead of
        losing it between two independent SQLite transactions.
        """

        await self.setup()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """
                    SELECT event_id, case_id, tenant_id, actor_id, sequence,
                           event_type, payload_json, created_at
                    FROM case_events
                    WHERE thread_id = ? AND sequence > ?
                    ORDER BY sequence ASC
                    LIMIT ?
                    """,
                    (thread_id, after_sequence, max(1, min(limit, 500))),
                )
            ).fetchall()
        return [
            CaseEvent(
                event_id=row[0],
                thread_id=thread_id,
                case_id=row[1],
                tenant_id=row[2],
                actor_id=row[3],
                sequence=int(row[4]),
                type=CaseEventType(row[5]),
                payload=json.loads(row[6]),
                created_at=row[7],
            )
            for row in rows
        ]

    async def list_events(
        self, thread_id: str, *, include_consumed: bool = True
    ) -> list[CaseEvent]:
        await self.setup()
        clause = "" if include_consumed else " AND consumed_at IS NULL"
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """
                    SELECT event_id, case_id, tenant_id, actor_id, sequence,
                           event_type, payload_json, created_at
                    FROM case_events WHERE thread_id = ?
                    """
                    + clause
                    + " ORDER BY sequence ASC",
                    (thread_id,),
                )
            ).fetchall()
        return [
            CaseEvent(
                event_id=row[0],
                thread_id=thread_id,
                case_id=row[1],
                tenant_id=row[2],
                actor_id=row[3],
                sequence=int(row[4]),
                type=CaseEventType(row[5]),
                payload=json.loads(row[6]),
                created_at=row[7],
            )
            for row in rows
        ]


class InMemoryCaseEventStore:
    """Deterministic test implementation with the same concurrency semantics."""

    def __init__(self) -> None:
        self._owners: dict[str, tuple[str, str, str]] = {}
        self._events: dict[str, list[CaseEvent]] = {}
        self._consumed: set[str] = set()
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def bind_thread(
        self,
        *,
        thread_id: str,
        case_id: str,
        tenant_id: str,
        actor_id: str,
        created_at: str,
    ) -> None:
        del created_at
        async with self._lock:
            owner = (case_id, tenant_id, actor_id)
            existing = self._owners.get(thread_id)
            if existing is not None and existing != owner:
                raise PermissionError("thread ownership or case binding does not match")
            self._owners[thread_id] = owner

    async def assert_owner(
        self, thread_id: str, *, tenant_id: str, actor_id: str
    ) -> tuple[str, str, str]:
        owner = self._owners.get(thread_id)
        if owner is None:
            raise KeyError(thread_id)
        if owner[1:] != (tenant_id, actor_id):
            raise PermissionError("thread belongs to a different actor or tenant")
        return owner

    async def enqueue(self, event: CaseEvent) -> CaseEvent:
        owner = await self.assert_owner(
            event.thread_id, tenant_id=event.tenant_id, actor_id=event.actor_id
        )
        if owner[0] != event.case_id:
            raise ValueError("event case_id does not match the bound thread")
        async with self._lock:
            items = self._events.setdefault(event.thread_id, [])
            duplicate = next((item for item in items if item.event_id == event.event_id), None)
            if duplicate is not None:
                return duplicate.model_copy(deep=True)
            stored = event.model_copy(update={"sequence": len(items) + 1})
            items.append(stored)
            return stored.model_copy(deep=True)

    async def drain(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[CaseEvent]:
        async with self._lock:
            pending = [
                item
                for item in self._events.get(thread_id, [])
                if item.sequence > after_sequence
            ][:limit]
            return [item.model_copy(deep=True) for item in pending]

    async def list_events(
        self, thread_id: str, *, include_consumed: bool = True
    ) -> list[CaseEvent]:
        async with self._lock:
            items = self._events.get(thread_id, [])
            if not include_consumed:
                items = [item for item in items if item.event_id not in self._consumed]
            return [item.model_copy(deep=True) for item in items]


__all__ = ["CaseEventStore", "InMemoryCaseEventStore"]
