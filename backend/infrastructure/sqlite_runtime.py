"""Shared SQLite connection policy for the local Concord runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

_ACCESS_LOCKS: dict[Path, asyncio.Lock] = {}


def sqlite_access_lock(path: str | Path) -> asyncio.Lock:
    """Return the process-local lock shared by adapters using one DB file.

    SQLite remains the cross-process authority.  This lock prevents Concord's
    independently constructed adapters from competing for the same local write
    lock inside one event loop.
    """

    resolved = Path(path).resolve()
    lock = _ACCESS_LOCKS.get(resolved)
    if lock is None:
        lock = asyncio.Lock()
        _ACCESS_LOCKS[resolved] = lock
    return lock


async def configure_sqlite_connection(
    db: aiosqlite.Connection,
    *,
    busy_timeout_ms: int = 30_000,
) -> None:
    """Apply the common bounded-concurrency policy to a runtime connection."""

    await db.execute(f"PRAGMA busy_timeout={max(1, busy_timeout_ms)}")
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")


__all__ = ["configure_sqlite_connection", "sqlite_access_lock"]
