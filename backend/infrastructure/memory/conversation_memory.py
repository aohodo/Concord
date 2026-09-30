"""Bounded conversational context with explicit authority boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import redis.asyncio as redis

from infrastructure.retrieval.sqlite_text_index import SQLiteTextIndex

logger = logging.getLogger(__name__)


class MsgRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    role: MsgRole
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    recent_messages: list[Message]
    relevant_history: list[str]
    user_profile: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_prompt_text(self) -> str:
        sections: list[str] = []
        if self.relevant_history:
            history = "\n".join(f"- {item}" for item in self.relevant_history[:3])
            sections.append(f"[召回的历史叙述：仅供参考，不是当前事实]\n{history}")
        if self.recent_messages:
            lines = [
                f"{message.role.value}: {message.content}"
                for message in self.recent_messages[-8:]
            ]
            sections.append("[最近对话]\n" + "\n".join(lines))
        return "\n\n".join(sections)


class _LocalWorkingStore:
    """Process-local development fallback implementing the Redis operations used here."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def ping(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> None:
        async with self._lock:
            self._lists[key].insert(0, value)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        async with self._lock:
            stop = None if end < 0 else end + 1
            return list(self._lists.get(key, [])[start:stop])

    async def llen(self, key: str) -> int:
        async with self._lock:
            return len(self._lists.get(key, []))

    async def ltrim(self, key: str, start: int, end: int) -> None:
        async with self._lock:
            stop = None if end < 0 else end + 1
            self._lists[key] = self._lists.get(key, [])[start:stop]

    async def expire(self, key: str, seconds: int) -> None:
        del key, seconds

    async def aclose(self) -> None:
        return None


class MemoryManager:
    """Keep recent turns ephemeral and expose lexical recall as unverified context."""

    WORKING_MAX = 20
    HISTORY_TOP_K = 5
    TTL_SECONDS = 86_400

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        recall_index_path: str = "./data/retrieval",
        require_redis: bool = False,
        **_: Any,
    ) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._require_redis = require_redis
        self._working_memory_backend = "redis_unchecked"
        self._setup_lock = asyncio.Lock()
        self._setup_complete = False
        self._recall = SQLiteTextIndex(recall_index_path, "conversation_recall")
        self._recall_index_backend = "sqlite_fts"

    async def setup(self) -> None:
        if self._setup_complete:
            return
        async with self._setup_lock:
            if self._setup_complete:
                return
            try:
                await self._redis.ping()
                self._working_memory_backend = "redis"
            except Exception as exc:
                if self._require_redis:
                    raise RuntimeError("Redis is required but unavailable") from exc
                await self._redis.aclose()
                self._redis = _LocalWorkingStore()
                self._working_memory_backend = "in_memory"
                logger.warning("Redis unavailable; using process-local working memory")
            self._setup_complete = True

    @property
    def working_memory_backend(self) -> str:
        return self._working_memory_backend

    @property
    def recall_index_backend(self) -> str:
        return self._recall_index_backend

    async def add_message(
        self,
        user_id: str,
        conv_id: str,
        role: MsgRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.setup()
        key = self._key(user_id, conv_id)
        message = Message(role=role, content=str(content), metadata=dict(metadata or {}))
        await self._redis.lpush(key, self._serialize(message))
        await self._redis.expire(key, self.TTL_SECONDS)
        if await self._redis.llen(key) > self.WORKING_MAX:
            overflow = await self._redis.lrange(key, self.WORKING_MAX, -1)
            await self._store_recall(user_id, conv_id, overflow)
            await self._redis.ltrim(key, 0, self.WORKING_MAX - 1)

    async def get_context(
        self,
        user_id: str,
        conv_id: str,
        query: str = "",
    ) -> MemoryContext:
        await self.setup()
        recent = await self._recent(user_id, conv_id)
        recall = await self._search_recall(user_id, query)
        return MemoryContext(recent_messages=recent, relevant_history=recall)

    async def _recent(self, user_id: str, conv_id: str) -> list[Message]:
        rows = await self._redis.lrange(self._key(user_id, conv_id), 0, self.WORKING_MAX - 1)
        messages = [self._deserialize(row) for row in reversed(rows)]
        return [item for item in messages if item is not None]

    async def _store_recall(
        self,
        user_id: str,
        conv_id: str,
        serialized_messages: list[str],
    ) -> None:
        messages = [self._deserialize(row) for row in reversed(serialized_messages)]
        document = "\n".join(
            f"{item.role.value}: {item.content}" for item in messages if item is not None
        ).strip()
        if not document:
            return
        await asyncio.to_thread(
            self._recall.upsert,
            doc_id=str(uuid4()),
            scope=str(user_id),
            title=f"conversation:{conv_id}",
            content=document[:4000],
            metadata={
                "conv_id": str(conv_id),
                "created_at": datetime.now(UTC).isoformat(),
                "authority": "unverified_history",
            },
        )

    async def _search_recall(self, user_id: str, query: str) -> list[str]:
        if not query.strip():
            return []
        try:
            rows = await asyncio.to_thread(
                self._recall.search,
                query,
                scope=str(user_id),
                limit=self.HISTORY_TOP_K,
            )
        except Exception as exc:  # noqa: BLE001 - recall is optional context
            logger.warning("Recall index query failed: %s", exc)
            return []
        return [str(item["content"]) for item in rows if str(item.get("content", "")).strip()]

    async def close(self) -> None:
        await self._redis.aclose()

    @staticmethod
    def _key(user_id: str, conv_id: str) -> str:
        return f"concord:working:{user_id}:{conv_id}"

    @staticmethod
    def _serialize(message: Message) -> str:
        return json.dumps(
            {
                "role": message.role.value,
                "content": message.content,
                "timestamp": message.timestamp.isoformat(),
                "metadata": message.metadata,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _deserialize(raw: str) -> Message | None:
        try:
            value = json.loads(raw)
            return Message(
                role=MsgRole(value["role"]),
                content=str(value["content"]),
                timestamp=datetime.fromisoformat(value["timestamp"]),
                metadata=dict(value.get("metadata", {})),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
