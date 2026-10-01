"""SQLite repository for M7 outcomes, experiences, signals and proposals."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from infrastructure.sqlite_runtime import (
    configure_sqlite_connection,
    sqlite_access_lock,
)

from .models import (
    ExperienceFeedback,
    ExperienceStatus,
    FailureSignal,
    ImprovementProposal,
    OutcomeVerification,
    ProposalStatus,
    StructuredExperience,
    utc_now,
)


class ContinualImprovementRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).resolve()
        self._db: aiosqlite.Connection | None = None
        self._lock = sqlite_access_lock(self._path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await configure_sqlite_connection(self._db)
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS m7_outcomes (
                outcome_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                case_revision INTEGER NOT NULL,
                trace_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                eligible INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, case_id, case_revision, trace_id)
            );
            CREATE INDEX IF NOT EXISTS idx_m7_outcomes_case
                ON m7_outcomes(tenant_id, case_id, created_at);

            CREATE TABLE IF NOT EXISTS m7_experiences (
                experience_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                source_case_revision INTEGER NOT NULL,
                source_trace_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                domain TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tenant_id, source_case_id, source_case_revision, source_trace_id, kind)
            );
            CREATE INDEX IF NOT EXISTS idx_m7_experiences_retrieve
                ON m7_experiences(tenant_id, status, domain, updated_at);

            CREATE TABLE IF NOT EXISTS m7_experience_feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id TEXT NOT NULL,
                target_case_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(experience_id, target_case_id, outcome)
            );

            CREATE TABLE IF NOT EXISTS m7_failure_signals (
                signal_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                category TEXT NOT NULL,
                signature TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, source_case_id, category, signature)
            );
            CREATE INDEX IF NOT EXISTS idx_m7_failure_cluster
                ON m7_failure_signals(tenant_id, category, signature, created_at);

            CREATE TABLE IF NOT EXISTS m7_improvement_proposals (
                proposal_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_m7_proposals_status
                ON m7_improvement_proposals(tenant_id, status, updated_at);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("continual improvement repository is not initialized")
        return self._db

    async def save_outcome(self, outcome: OutcomeVerification) -> OutcomeVerification:
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                INSERT INTO m7_outcomes(
                    outcome_id, tenant_id, case_id, case_revision, trace_id,
                    verdict, eligible, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, case_id, case_revision, trace_id) DO NOTHING
                """,
                (
                    outcome.outcome_id,
                    outcome.tenant_id,
                    outcome.case_id,
                    outcome.case_revision,
                    outcome.trace_id,
                    outcome.verdict.value,
                    int(outcome.eligible_for_experience),
                    outcome.model_dump_json(),
                    outcome.verified_at,
                ),
            )
            await db.commit()
            row = await (
                await db.execute(
                    """
                    SELECT payload_json FROM m7_outcomes
                    WHERE tenant_id=? AND case_id=? AND case_revision=? AND trace_id=?
                    """,
                    (
                        outcome.tenant_id,
                        outcome.case_id,
                        outcome.case_revision,
                        outcome.trace_id,
                    ),
                )
            ).fetchone()
            return OutcomeVerification.model_validate_json(row["payload_json"])

    async def save_experience(self, experience: StructuredExperience) -> StructuredExperience:
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                INSERT INTO m7_experiences(
                    experience_id, tenant_id, source_case_id, source_case_revision,
                    source_trace_id, kind, status, domain, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    tenant_id, source_case_id, source_case_revision, source_trace_id, kind
                ) DO NOTHING
                """,
                (
                    experience.experience_id,
                    experience.tenant_id,
                    experience.source_case_id,
                    experience.source_case_revision,
                    experience.source_trace_id,
                    experience.kind.value,
                    experience.status.value,
                    experience.applicability.domain,
                    experience.model_dump_json(),
                    experience.created_at,
                    experience.updated_at,
                ),
            )
            await db.commit()
            row = await (
                await db.execute(
                    """
                    SELECT payload_json FROM m7_experiences
                    WHERE tenant_id=? AND source_case_id=? AND source_case_revision=?
                      AND source_trace_id=? AND kind=?
                    """,
                    (
                        experience.tenant_id,
                        experience.source_case_id,
                        experience.source_case_revision,
                        experience.source_trace_id,
                        experience.kind.value,
                    ),
                )
            ).fetchone()
            return StructuredExperience.model_validate_json(row["payload_json"])

    async def list_experiences(
        self,
        *,
        tenant_id: str,
        statuses: set[ExperienceStatus] | None = None,
    ) -> list[StructuredExperience]:
        db = self._require_db()
        selected = statuses or {ExperienceStatus.ACTIVE}
        placeholders = ",".join("?" for _ in selected)
        rows = await (
            await db.execute(
                f"""
                SELECT payload_json FROM m7_experiences
                WHERE tenant_id=? AND status IN ({placeholders})
                ORDER BY updated_at DESC
                """,
                (tenant_id, *(item.value for item in selected)),
            )
        ).fetchall()
        return [StructuredExperience.model_validate_json(row["payload_json"]) for row in rows]

    async def record_feedback(self, feedback: ExperienceFeedback) -> None:
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                """
                INSERT INTO m7_experience_feedback(
                    experience_id, target_case_id, outcome, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(experience_id, target_case_id, outcome) DO NOTHING
                """,
                (
                    feedback.experience_id,
                    feedback.target_case_id,
                    feedback.outcome,
                    feedback.model_dump_json(),
                    feedback.created_at,
                ),
            )
            if cursor.rowcount:
                row = await (
                    await db.execute(
                        "SELECT payload_json FROM m7_experiences WHERE experience_id=?",
                        (feedback.experience_id,),
                    )
                ).fetchone()
                if row is not None:
                    experience = StructuredExperience.model_validate_json(row["payload_json"])
                    experience.reuse_count += 1
                    if feedback.outcome == "supported":
                        experience.supported_reuse_count += 1
                    elif feedback.outcome == "contradicted":
                        experience.contradicted_reuse_count += 1
                    experience.updated_at = utc_now()
                    await db.execute(
                        """
                        UPDATE m7_experiences
                        SET payload_json=?, status=?, updated_at=?
                        WHERE experience_id=?
                        """,
                        (
                            experience.model_dump_json(),
                            experience.status.value,
                            experience.updated_at,
                            experience.experience_id,
                        ),
                    )
            await db.commit()

    async def save_failure_signal(self, signal: FailureSignal) -> FailureSignal:
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                INSERT INTO m7_failure_signals(
                    signal_id, tenant_id, source_case_id, category,
                    signature, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, source_case_id, category, signature) DO NOTHING
                """,
                (
                    signal.signal_id,
                    signal.tenant_id,
                    signal.source_case_id,
                    signal.category.value,
                    signal.signature,
                    signal.model_dump_json(),
                    signal.created_at,
                ),
            )
            await db.commit()
            row = await (
                await db.execute(
                    """
                    SELECT payload_json FROM m7_failure_signals
                    WHERE tenant_id=? AND source_case_id=? AND category=? AND signature=?
                    """,
                    (
                        signal.tenant_id,
                        signal.source_case_id,
                        signal.category.value,
                        signal.signature,
                    ),
                )
            ).fetchone()
            return FailureSignal.model_validate_json(row["payload_json"])

    async def list_failure_signals(self, *, tenant_id: str) -> list[FailureSignal]:
        db = self._require_db()
        rows = await (
            await db.execute(
                """
                SELECT payload_json FROM m7_failure_signals
                WHERE tenant_id=? ORDER BY created_at
                """,
                (tenant_id,),
            )
        ).fetchall()
        return [FailureSignal.model_validate_json(row["payload_json"]) for row in rows]

    async def save_proposal(self, proposal: ImprovementProposal) -> ImprovementProposal:
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                INSERT INTO m7_improvement_proposals(
                    proposal_id, tenant_id, kind, status, version,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status,
                    version=excluded.version,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    proposal.proposal_id,
                    proposal.tenant_id,
                    proposal.kind.value,
                    proposal.status.value,
                    proposal.version,
                    proposal.model_dump_json(),
                    proposal.created_at,
                    proposal.updated_at,
                ),
            )
            await db.commit()
            return proposal.model_copy(deep=True)

    async def list_proposals(
        self,
        *,
        tenant_id: str,
        status: ProposalStatus | None = None,
    ) -> list[ImprovementProposal]:
        db = self._require_db()
        if status is None:
            cursor = await db.execute(
                """
                SELECT payload_json FROM m7_improvement_proposals
                WHERE tenant_id=? ORDER BY updated_at DESC
                """,
                (tenant_id,),
            )
        else:
            cursor = await db.execute(
                """
                SELECT payload_json FROM m7_improvement_proposals
                WHERE tenant_id=? AND status=? ORDER BY updated_at DESC
                """,
                (tenant_id, status.value),
            )
        rows = await cursor.fetchall()
        return [ImprovementProposal.model_validate_json(row["payload_json"]) for row in rows]

    async def get_proposal(
        self, *, tenant_id: str, proposal_id: str
    ) -> ImprovementProposal | None:
        db = self._require_db()
        row = await (
            await db.execute(
                """
                SELECT payload_json FROM m7_improvement_proposals
                WHERE tenant_id=? AND proposal_id=?
                """,
                (tenant_id, proposal_id),
            )
        ).fetchone()
        return (
            ImprovementProposal.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )


__all__ = ["ContinualImprovementRepository"]
