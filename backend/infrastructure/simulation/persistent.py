"""SQLite durability adapters for M5 simulated environments and idempotency."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from infrastructure.sqlite_runtime import (
    configure_sqlite_connection,
    sqlite_access_lock,
)

from .runtime import (
    IdempotencyRecord,
    IdempotencyStore,
    ScenarioActionRule,
    ScenarioCase,
    ScenarioObservationRule,
    ScenarioRuntime,
    SimulatedFault,
)


class SqliteFaultPlan:
    """Ordered fault injection queue that is not lost during restart tests."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).resolve()
        self._db: aiosqlite.Connection | None = None
        self._lock = sqlite_access_lock(self._path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await configure_sqlite_connection(self._db)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_faults (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                fault_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLite fault plan is not initialized")
        return self._db

    async def add(self, case_id: str, tool_id: str, fault: SimulatedFault) -> None:
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """INSERT INTO simulation_faults(
                       case_id, tool_id, phase, fault_json, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    case_id,
                    tool_id,
                    fault.phase,
                    fault.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await db.commit()

    async def take(
        self, case_id: str, tool_id: str, phase: str
    ) -> SimulatedFault | None:
        async with self._lock:
            db = self._require_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """SELECT sequence, fault_json FROM simulation_faults
                       WHERE case_id=? AND tool_id=? AND phase=?
                       ORDER BY sequence LIMIT 1""",
                    (case_id, tool_id, phase),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return None
                await db.execute(
                    "DELETE FROM simulation_faults WHERE sequence=?", (row["sequence"],)
                )
                await db.commit()
                return SimulatedFault.model_validate_json(row["fault_json"])
            except Exception:
                await db.rollback()
                raise


class SqliteIdempotencyStore(IdempotencyStore):
    """Keeps committed tool results replayable after a process restart."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path).resolve()
        self._db: aiosqlite.Connection | None = None
        self._lock = sqlite_access_lock(self._path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await configure_sqlite_connection(self._db)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_idempotency (
                case_id TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                key TEXT NOT NULL,
                canonical_arguments TEXT NOT NULL,
                result_json TEXT NOT NULL,
                PRIMARY KEY(case_id, tool_id, key)
            )
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLite idempotency store is not initialized")
        return self._db

    async def get(self, case_id: str, tool_id: str, key: str) -> IdempotencyRecord | None:
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                """SELECT canonical_arguments, result_json FROM tool_idempotency
                   WHERE case_id=? AND tool_id=? AND key=?""",
                (case_id, tool_id, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return IdempotencyRecord(
                tool_id=tool_id,
                key=key,
                canonical_arguments=row["canonical_arguments"],
                result=__import__("json").loads(row["result_json"]),
            )

    async def put(
        self,
        case_id: str,
        tool_id: str,
        key: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        import json

        async with self._lock:
            db = self._require_db()
            canonical = self.canonicalize(arguments)
            await db.execute(
                """
                INSERT INTO tool_idempotency(
                    case_id, tool_id, key, canonical_arguments, result_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(case_id, tool_id, key) DO NOTHING
                """,
                (
                    case_id,
                    tool_id,
                    key,
                    canonical,
                    json.dumps(result, ensure_ascii=False, default=str),
                ),
            )
            await db.commit()


class DurableScenarioRuntime(ScenarioRuntime):
    """Resettable simulation whose declared environment survives restarts."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path).resolve()
        self._db: aiosqlite.Connection | None = None
        self._persistence_lock = sqlite_access_lock(self._path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await configure_sqlite_connection(self._db)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_cases (
                case_id TEXT PRIMARY KEY,
                case_json TEXT NOT NULL
            )
            """
        )
        await self._db.commit()
        cursor = await self._db.execute("SELECT case_json FROM simulation_cases")
        for row in await cursor.fetchall():
            case = ScenarioCase.model_validate_json(row["case_json"])
            self._cases[case.case_id] = case

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _persist(self, case_id: str) -> None:
        if self._db is None:
            raise RuntimeError("durable simulation runtime is not initialized")
        async with self._persistence_lock:
            case = self._cases[case_id]
            await self._db.execute(
                """INSERT INTO simulation_cases(case_id, case_json) VALUES (?, ?)
                   ON CONFLICT(case_id) DO UPDATE SET case_json=excluded.case_json""",
                (case_id, case.model_dump_json()),
            )
            await self._db.commit()

    async def create_case(
        self,
        case_id: str,
        *,
        visible_state: dict[str, Any] | None = None,
        hidden_state: dict[str, Any] | None = None,
        action_rules: list[ScenarioActionRule] | None = None,
        observation_rules: list[ScenarioObservationRule] | None = None,
        replace: bool = False,
    ) -> ScenarioCase:
        case = await super().create_case(
            case_id,
            visible_state=visible_state,
            hidden_state=hidden_state,
            action_rules=action_rules,
            observation_rules=observation_rules,
            replace=replace,
        )
        await self._persist(case_id)
        return case

    async def observe_with_metadata(self, case_id: str, path: str = "") -> dict[str, Any]:
        result = await super().observe_with_metadata(case_id, path)
        await self._persist(case_id)
        return copy.deepcopy(result)

    async def execute_action(
        self,
        case_id: str,
        action_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await super().execute_action(case_id, action_id, arguments)
        await self._persist(case_id)
        return copy.deepcopy(result)

    async def advance_time(self, seconds: float) -> None:
        await super().advance_time(seconds)
        for case_id in self._cases:
            await self._persist(case_id)


__all__ = [
    "DurableScenarioRuntime",
    "SqliteFaultPlan",
    "SqliteIdempotencyStore",
]
