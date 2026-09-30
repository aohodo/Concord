"""SQLite-backed M5 Case, event and recoverable-job store."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from .models import CaseJob, CaseJobKind, CaseJobStatus, CasePhase, CaseRunSnapshot
from .registry import InMemoryCaseRunRegistry, StaleCaseResultError


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DurableCaseRunRegistry(InMemoryCaseRunRegistry):
    """Drop-in registry that survives process restarts.

    Case snapshots make reads cheap, while the event table retains the complete
    append-only lifecycle beyond the bounded API event window.
    """

    def __init__(self, path: str | Path, *, max_events: int = 200) -> None:
        super().__init__(max_events=max_events)
        self._path = Path(path).resolve()
        self._db: aiosqlite.Connection | None = None
        self._db_lock = asyncio.Lock()

    @property
    def storage_backend(self) -> str:
        return "sqlite_m5"

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                conv_id TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_conversation
                ON cases(tenant_id, user_id, conv_id);
            CREATE TABLE IF NOT EXISTS case_lifecycle_events (
                event_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(case_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_case_events_sequence
                ON case_lifecycle_events(case_id, sequence);
            CREATE TABLE IF NOT EXISTS case_jobs (
                job_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                case_revision INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                job_json TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_expires_at TEXT,
                lease_generation INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_case_jobs_claim
                ON case_jobs(status, available_at, lease_expires_at);
            CREATE INDEX IF NOT EXISTS idx_case_jobs_case
                ON case_jobs(case_id, status, case_revision);
            """
        )
        columns = {
            row["name"]
            for row in await (await self._db.execute("PRAGMA table_info(case_jobs)")).fetchall()
        }
        if "lease_generation" not in columns:
            await self._db.execute(
                "ALTER TABLE case_jobs ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0"
            )
        await self._db.commit()
        cursor = await self._db.execute("SELECT snapshot_json FROM cases ORDER BY updated_at")
        rows = await cursor.fetchall()
        for row in rows:
            snapshot = CaseRunSnapshot.model_validate_json(row["snapshot_json"])
            self._cases[snapshot.case_id] = snapshot
            self._case_by_conversation[(snapshot.tenant_id, snapshot.user_id, snapshot.conv_id)] = (
                snapshot.case_id
            )
        await self.recover_expired_jobs(force=False)

    async def close(self) -> None:
        if self._db is not None:
            # A graceful owner shutdown may immediately release local work.
            # Crash recovery still honors the persisted lease and therefore
            # cannot steal a healthy job from another process.
            await self.recover_expired_jobs(force=True)
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("durable Case registry is not initialized")
        return self._db

    async def _after_mutation(self, snapshot: CaseRunSnapshot) -> None:
        async with self._db_lock:
            db = self._require_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._persist_snapshot_unlocked(snapshot)
                await db.commit()
            except Exception:
                await db.rollback()
                await self._restore_case_from_db_unlocked(snapshot.case_id)
                raise

    async def _restore_case_from_db_unlocked(self, case_id: str) -> None:
        """Restore the in-memory authority after a failed SQLite transaction."""

        current = self._cases.pop(case_id, None)
        if current is not None:
            self._case_by_conversation.pop(
                (current.tenant_id, current.user_id, current.conv_id),
                None,
            )
        db = self._require_db()
        cursor = await db.execute(
            "SELECT snapshot_json FROM cases WHERE case_id=?",
            (case_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        restored = CaseRunSnapshot.model_validate_json(row["snapshot_json"])
        self._cases[restored.case_id] = restored
        self._case_by_conversation[
            (restored.tenant_id, restored.user_id, restored.conv_id)
        ] = restored.case_id

    async def _persist_snapshot_unlocked(self, snapshot: CaseRunSnapshot) -> None:
        db = self._require_db()
        await db.execute(
            """
            INSERT INTO cases(case_id, tenant_id, user_id, conv_id, snapshot_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                tenant_id=excluded.tenant_id,
                user_id=excluded.user_id,
                conv_id=excluded.conv_id,
                snapshot_json=excluded.snapshot_json,
                updated_at=excluded.updated_at
            """,
            (
                snapshot.case_id,
                snapshot.tenant_id,
                snapshot.user_id,
                snapshot.conv_id,
                snapshot.model_dump_json(),
                snapshot.updated_at,
            ),
        )
        await db.executemany(
            """
            INSERT OR IGNORE INTO case_lifecycle_events(
                event_id, case_id, sequence, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    event.event_id,
                    snapshot.case_id,
                    event.sequence,
                    event.model_dump_json(),
                    event.created_at,
                )
                for event in snapshot.events
            ],
        )

    async def list_all_events(
        self,
        case_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._db_lock:
            db = self._require_db()
            order = "DESC" if latest else "ASC"
            cursor = await db.execute(
                f"""SELECT event_json FROM case_lifecycle_events
                   WHERE case_id=? AND sequence>? ORDER BY sequence {order} LIMIT ?""",
                (case_id, max(0, after_sequence), max(1, min(limit, 500))),
            )
            rows = [json.loads(row["event_json"]) for row in await cursor.fetchall()]
            return list(reversed(rows)) if latest else rows

    async def enqueue_job(self, job: CaseJob) -> CaseJob:
        created = False
        async with self._db_lock:
            db = self._require_db()
            cursor = await db.execute(
                """
            INSERT OR IGNORE INTO case_jobs(
                job_id, case_id, tenant_id, user_id, kind, status, case_revision,
                idempotency_key, job_json, available_at, lease_owner,
                lease_expires_at, lease_generation, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._job_row(job),
            )
            created = cursor.rowcount == 1
            await db.commit()
            if not created:
                existing = await db.execute(
                    "SELECT job_json FROM case_jobs WHERE idempotency_key=?",
                    (job.idempotency_key,),
                )
                row = await existing.fetchone()
                if row is None:
                    raise RuntimeError("idempotent job insert was not observable")
                return CaseJob.model_validate_json(row["job_json"])
        await self.record_runtime_event(
            job.case_id,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            event_type="durable_job_enqueued",
            status=job.status.value,
            summary=f"Durable job enqueued: {job.kind.value}",
            data={"job_id": job.job_id, "case_revision": job.case_revision},
        )
        return job.model_copy(deep=True)

    async def schedule_collaboration_job(
        self,
        job: CaseJob,
        *,
        collaboration_id: str,
        thread_id: str,
        expected_request_generation: int | None = None,
    ) -> CaseJob:
        """Atomically persist the collaboration state, events and durable Job."""

        async with self._lock:
            snapshot = self._required(job.case_id)
            self._assert_owner(snapshot, user_id=job.user_id, tenant_id=job.tenant_id)
            self._assert_current_request(snapshot, expected_request_generation)
            if snapshot.case_revision != job.case_revision:
                raise ValueError("collaboration source revision is stale")
            if snapshot.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
                raise StaleCaseResultError(
                    f"collaboration cannot start for a {snapshot.phase.value} Case"
                )
            async with self._db_lock:
                db = self._require_db()
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cursor = await db.execute(
                        "SELECT job_json FROM case_jobs WHERE idempotency_key=?",
                        (job.idempotency_key,),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        await db.commit()
                        return CaseJob.model_validate_json(row["job_json"])
                    snapshot.phase = CasePhase.COLLABORATION
                    snapshot.status = "running"
                    snapshot.collaboration_id = collaboration_id
                    snapshot.m3_thread_id = thread_id
                    snapshot.m3 = {
                        "collaboration_id": collaboration_id,
                        "thread_id": thread_id,
                        "status": "running",
                        "source_case_revision": job.case_revision,
                    }
                    self._append_event(
                        snapshot,
                        phase=CasePhase.COLLABORATION,
                        event_type="m3_started",
                        status="running",
                        summary="M3 collaboration started",
                        data={
                            "collaboration_id": collaboration_id,
                            "thread_id": thread_id,
                            "source_case_revision": job.case_revision,
                        },
                    )
                    self._append_event(
                        snapshot,
                        phase=CasePhase.COLLABORATION,
                        event_type="durable_job_enqueued",
                        status=job.status.value,
                        summary=f"Durable job enqueued: {job.kind.value}",
                        data={
                            "job_id": job.job_id,
                            "case_revision": job.case_revision,
                        },
                    )
                    self._touch(snapshot)
                    await db.execute(
                        """
                        INSERT INTO case_jobs(
                            job_id, case_id, tenant_id, user_id, kind, status,
                            case_revision, idempotency_key, job_json, available_at,
                            lease_owner, lease_expires_at, lease_generation, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._job_row(job),
                    )
                    await self._persist_snapshot_unlocked(snapshot)
                    await db.commit()
                except Exception:
                    await db.rollback()
                    await self._restore_case_from_db_unlocked(snapshot.case_id)
                    raise
            return job.model_copy(deep=True)

    @staticmethod
    def _job_row(job: CaseJob) -> tuple[Any, ...]:
        return (
            job.job_id,
            job.case_id,
            job.tenant_id,
            job.user_id,
            job.kind.value,
            job.status.value,
            job.case_revision,
            job.idempotency_key,
            job.model_dump_json(),
            job.available_at,
            job.lease_owner,
            job.lease_expires_at,
            job.lease_generation,
            job.updated_at,
        )

    async def _save_job(self, job: CaseJob) -> None:
        async with self._db_lock:
            await self._save_job_unlocked(job)

    async def _save_job_unlocked(self, job: CaseJob, *, commit: bool = True) -> None:
        db = self._require_db()
        await db.execute(
            """
            UPDATE case_jobs SET status=?, job_json=?, available_at=?, lease_owner=?,
                lease_expires_at=?, lease_generation=?, updated_at=?, case_revision=?
            WHERE job_id=?
            """,
            (
                job.status.value,
                job.model_dump_json(),
                job.available_at,
                job.lease_owner,
                job.lease_expires_at,
                job.lease_generation,
                job.updated_at,
                job.case_revision,
                job.job_id,
            ),
        )
        if commit:
            await db.commit()

    async def get_job(self, job_id: str) -> CaseJob | None:
        async with self._db_lock:
            return await self._get_job_unlocked(job_id)

    async def _get_job_unlocked(self, job_id: str) -> CaseJob | None:
        db = self._require_db()
        cursor = await db.execute("SELECT job_json FROM case_jobs WHERE job_id=?", (job_id,))
        row = await cursor.fetchone()
        return CaseJob.model_validate_json(row["job_json"]) if row else None

    async def list_jobs(self, case_id: str) -> list[CaseJob]:
        async with self._db_lock:
            return await self._list_jobs_unlocked(case_id)

    async def _list_jobs_unlocked(self, case_id: str) -> list[CaseJob]:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT job_json FROM case_jobs WHERE case_id=? ORDER BY updated_at",
            (case_id,),
        )
        return [CaseJob.model_validate_json(row["job_json"]) for row in await cursor.fetchall()]

    async def find_active_job(
        self,
        case_id: str,
        kind: CaseJobKind,
        case_revision: int,
    ) -> CaseJob | None:
        async with self._db_lock:
            db = self._require_db()
            cursor = await db.execute(
                """
            SELECT job_json FROM case_jobs
            WHERE case_id=? AND kind=? AND case_revision=?
              AND status IN ('pending', 'running', 'waiting')
            ORDER BY updated_at DESC LIMIT 1
            """,
                (case_id, kind.value, case_revision),
            )
            row = await cursor.fetchone()
            return CaseJob.model_validate_json(row["job_json"]) if row else None

    async def claim_next_job(self, owner: str, *, lease_seconds: float = 180.0) -> CaseJob | None:
        async with self._db_lock:
            db = self._require_db()
            now = _now()
            lease = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            await db.execute("BEGIN IMMEDIATE")
            try:
                # Reap expired leases inside every atomic claim cycle. This
                # handles a peer crashing after our worker has already started
                # without a separate recovery loop or lock-order race.
                expired_cursor = await db.execute(
                    """
                    SELECT job_json FROM case_jobs
                    WHERE status='running'
                      AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                    """,
                    (now,),
                )
                for expired_row in await expired_cursor.fetchall():
                    expired = CaseJob.model_validate_json(expired_row["job_json"])
                    expired.status = CaseJobStatus.PENDING
                    expired.lease_owner = ""
                    expired.lease_expires_at = None
                    expired.available_at = now
                    expired.updated_at = now
                    await db.execute(
                        """
                        UPDATE case_jobs SET status='pending', job_json=?, available_at=?,
                            lease_owner='', lease_expires_at=NULL, updated_at=?
                        WHERE job_id=? AND status='running' AND lease_generation=?
                        """,
                        (
                            expired.model_dump_json(),
                            now,
                            now,
                            expired.job_id,
                            expired.lease_generation,
                        ),
                    )
                cursor = await db.execute(
                    """
                    SELECT job_json FROM case_jobs
                    WHERE status='pending' AND available_at<=?
                    ORDER BY available_at, updated_at LIMIT 1
                    """,
                    (now,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return None
                job = CaseJob.model_validate_json(row["job_json"])
                job.status = CaseJobStatus.RUNNING
                job.attempts += 1
                job.lease_generation += 1
                job.lease_owner = owner
                job.lease_expires_at = lease
                job.updated_at = now
                await db.execute(
                    """
                    UPDATE case_jobs SET status=?, job_json=?, lease_owner=?,
                        lease_expires_at=?, lease_generation=?, updated_at=?
                    WHERE job_id=? AND status='pending'
                    """,
                    (
                        job.status.value,
                        job.model_dump_json(),
                        owner,
                        lease,
                        job.lease_generation,
                        now,
                        job.job_id,
                    ),
                )
                await db.commit()
                return job
            except Exception:
                await db.rollback()
                raise

    async def heartbeat_job(
        self,
        job_id: str,
        owner: str,
        *,
        lease_generation: int | None = None,
        lease_seconds: float = 180.0,
    ) -> bool:
        async with self._db_lock:
            db = self._require_db()
            job = await self._get_job_unlocked(job_id)
            if job is None or job.status is not CaseJobStatus.RUNNING:
                return False
            expected_generation = (
                job.lease_generation if lease_generation is None else lease_generation
            )
            if job.lease_owner != owner or job.lease_generation != expected_generation:
                return False
            job.lease_expires_at = (
                datetime.now(UTC) + timedelta(seconds=lease_seconds)
            ).isoformat()
            job.updated_at = _now()
            cursor = await db.execute(
                """
                UPDATE case_jobs SET job_json=?, lease_expires_at=?, updated_at=?
                WHERE job_id=? AND status='running' AND lease_owner=?
                  AND lease_generation=?
                """,
                (
                    job.model_dump_json(),
                    job.lease_expires_at,
                    job.updated_at,
                    job_id,
                    owner,
                    expected_generation,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def complete_job(
        self,
        job_id: str,
        owner: str,
        result: dict[str, Any],
        *,
        lease_generation: int | None = None,
    ) -> bool:
        async with self._lock, self._db_lock:
            db = self._require_db()
            job = await self._get_job_unlocked(job_id)
            expected_generation = (
                job.lease_generation
                if job is not None and lease_generation is None
                else lease_generation
            )
            if (
                job is None
                or job.status is not CaseJobStatus.RUNNING
                or job.lease_owner != owner
                or job.lease_generation != expected_generation
            ):
                return False
            snapshot = self._required(job.case_id)
            job.status = CaseJobStatus.COMPLETED
            job.result = result
            job.lease_owner = ""
            job.lease_expires_at = None
            job.updated_at = _now()
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type="durable_job_completed",
                status="completed",
                summary=f"Durable job completed: {job.kind.value}",
                data={"job_id": job.job_id, "attempts": job.attempts},
            )
            self._touch(snapshot)
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                        UPDATE case_jobs SET status=?, job_json=?, lease_owner='',
                            lease_expires_at=NULL, updated_at=?
                        WHERE job_id=? AND status='running' AND lease_owner=?
                          AND lease_generation=?
                        """,
                    (
                        job.status.value,
                        job.model_dump_json(),
                        job.updated_at,
                        job_id,
                        owner,
                        expected_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return False
                await self._persist_snapshot_unlocked(snapshot)
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                await self._restore_case_from_db_unlocked(snapshot.case_id)
                raise

    async def fail_job(
        self,
        job_id: str,
        owner: str,
        error: str,
        *,
        retry_delay_seconds: float | None = None,
        lease_generation: int | None = None,
    ) -> bool:
        async with self._lock, self._db_lock:
            db = self._require_db()
            job = await self._get_job_unlocked(job_id)
            expected_generation = (
                job.lease_generation
                if job is not None and lease_generation is None
                else lease_generation
            )
            if (
                job is None
                or job.status is not CaseJobStatus.RUNNING
                or job.lease_owner != owner
                or job.lease_generation != expected_generation
            ):
                return False
            terminal = job.attempts >= job.max_attempts
            delay = (
                min(60.0, 2.0 ** max(0, job.attempts - 1))
                if retry_delay_seconds is None
                else max(0.0, retry_delay_seconds)
            )
            job.status = CaseJobStatus.FAILED if terminal else CaseJobStatus.PENDING
            job.last_error = error[:2000]
            job.available_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            job.lease_owner = ""
            job.lease_expires_at = None
            job.updated_at = _now()
            snapshot = self._required(job.case_id)
            if terminal:
                snapshot.phase = (
                    CasePhase.TIMED_OUT
                    if error == "JOB_EXECUTION_TIMEOUT"
                    else CasePhase.ERROR
                )
                snapshot.status = (
                    "timed_out"
                    if error == "JOB_EXECUTION_TIMEOUT"
                    else "durable_job_failed"
                )
            self._append_event(
                snapshot,
                phase=snapshot.phase,
                event_type=("durable_job_failed" if terminal else "durable_job_retry_scheduled"),
                status=job.status.value,
                summary=f"Durable job attempt failed: {job.kind.value}",
                data={
                    "job_id": job.job_id,
                    "attempts": job.attempts,
                    "retry_delay_seconds": 0.0 if terminal else delay,
                },
            )
            self._touch(snapshot)
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                        UPDATE case_jobs SET status=?, job_json=?, available_at=?,
                            lease_owner='', lease_expires_at=NULL, updated_at=?
                        WHERE job_id=? AND status='running' AND lease_owner=?
                          AND lease_generation=?
                        """,
                    (
                        job.status.value,
                        job.model_dump_json(),
                        job.available_at,
                        job.updated_at,
                        job_id,
                        owner,
                        expected_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return False
                await self._persist_snapshot_unlocked(snapshot)
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                await self._restore_case_from_db_unlocked(snapshot.case_id)
                raise

    async def invalidate_stale_jobs(self, case_id: str, current_revision: int) -> int:
        jobs = await self.list_jobs(case_id)
        changed = 0
        for job in jobs:
            if (
                job.status in {CaseJobStatus.PENDING, CaseJobStatus.RUNNING, CaseJobStatus.WAITING}
                and job.case_revision != current_revision
            ):
                job.status = CaseJobStatus.STALE
                job.lease_owner = ""
                job.lease_expires_at = None
                job.updated_at = _now()
                await self._save_job(job)
                changed += 1
        return changed

    async def cancel_case_jobs(self, case_id: str) -> int:
        jobs = await self.list_jobs(case_id)
        changed = 0
        for job in jobs:
            if job.status in {
                CaseJobStatus.PENDING,
                CaseJobStatus.RUNNING,
                CaseJobStatus.WAITING,
            }:
                job.status = CaseJobStatus.CANCELLED
                job.lease_owner = ""
                job.lease_expires_at = None
                job.updated_at = _now()
                await self._save_job(job)
                changed += 1
        return changed

    async def pause_case_jobs(self, case_id: str) -> int:
        jobs = await self.list_jobs(case_id)
        changed = 0
        for job in jobs:
            if job.status in {CaseJobStatus.PENDING, CaseJobStatus.RUNNING}:
                job.status = CaseJobStatus.WAITING
                job.lease_owner = ""
                job.lease_expires_at = None
                job.updated_at = _now()
                await self._save_job(job)
                changed += 1
        return changed

    async def resume_case_jobs(self, case_id: str) -> int:
        jobs = await self.list_jobs(case_id)
        changed = 0
        for job in jobs:
            if job.status is CaseJobStatus.WAITING:
                job.status = CaseJobStatus.PENDING
                job.available_at = _now()
                job.updated_at = _now()
                await self._save_job(job)
                changed += 1
        return changed

    async def recover_expired_jobs(self, *, force: bool = False) -> int:
        db = self._require_db()
        cursor = await db.execute("SELECT job_json FROM case_jobs WHERE status='running'")
        changed = 0
        now = datetime.now(UTC)
        for row in await cursor.fetchall():
            job = CaseJob.model_validate_json(row["job_json"])
            expired = (
                not job.lease_expires_at or datetime.fromisoformat(job.lease_expires_at) <= now
            )
            if force or expired:
                job.status = CaseJobStatus.PENDING
                job.lease_owner = ""
                job.lease_expires_at = None
                job.available_at = _now()
                job.updated_at = _now()
                await self._save_job(job)
                changed += 1
        return changed

    async def release_leases(self, owner: str) -> int:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT job_json FROM case_jobs WHERE status='running' AND lease_owner=?",
            (owner,),
        )
        changed = 0
        for row in await cursor.fetchall():
            job = CaseJob.model_validate_json(row["job_json"])
            job.status = CaseJobStatus.PENDING
            job.lease_owner = ""
            job.lease_expires_at = None
            job.available_at = _now()
            job.updated_at = _now()
            await self._save_job(job)
            changed += 1
        return changed


__all__ = ["DurableCaseRunRegistry"]
