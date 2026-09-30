"""Local replayable trajectory sink for later policy evaluation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class JsonlTrajectorySink:
    def __init__(self, path: str | None):
        self._path = Path(path).resolve() if path else None

    async def append(self, record: dict[str, Any]) -> None:
        if self._path is None:
            return
        await asyncio.to_thread(self._append_sync, record)

    def _append_sync(self, record: dict[str, Any]) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
