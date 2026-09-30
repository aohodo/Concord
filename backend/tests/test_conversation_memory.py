import asyncio

import pytest

from infrastructure.memory.conversation_memory import MemoryContext, MemoryManager


def test_recall_search_is_user_scoped_and_cross_conversation():
    manager = MemoryManager.__new__(MemoryManager)
    calls = []

    class FakeRecall:
        def search(self, query, *, scope, limit):
            calls.append((query, scope, limit))
            return [{"content": item} for item in ["a", "b", "c", "d", "e"]]

    manager._recall = FakeRecall()

    results = asyncio.run(manager._search_recall("user-1", "hello"))

    assert results == ["a", "b", "c", "d", "e"]
    assert calls[0] == ("hello", "user-1", manager.HISTORY_TOP_K)


class BrokenRedis:
    def __init__(self):
        self.closed = False

    async def ping(self):
        raise ConnectionError("redis unavailable")

    async def aclose(self):
        self.closed = True


def bare_manager(*, require_redis: bool):
    manager = MemoryManager.__new__(MemoryManager)
    manager._redis = BrokenRedis()
    manager._require_redis = require_redis
    manager._working_memory_backend = "redis_unchecked"
    manager._setup_lock = asyncio.Lock()
    manager._setup_complete = False
    return manager


def test_working_memory_falls_back_locally_when_redis_is_optional():
    manager = bare_manager(require_redis=False)

    asyncio.run(manager.setup())

    assert manager.working_memory_backend == "in_memory"
    asyncio.run(manager._redis.lpush("case", "message"))
    assert asyncio.run(manager._redis.lrange("case", 0, -1)) == ["message"]


def test_working_memory_can_require_redis_for_production():
    manager = bare_manager(require_redis=True)

    with pytest.raises(RuntimeError, match="Redis is required"):
        asyncio.run(manager.setup())


def test_recalled_memory_is_rendered_as_unverified_context():
    context = MemoryContext(
        recent_messages=[],
        relevant_history=["上次似乎通过重启恢复"],
        user_profile={"preferences": ["简短回答"]},
        summary="",
    )

    rendered = context.to_prompt_text()

    assert "仅供参考，不是当前事实" in rendered
    assert "上次似乎通过重启恢复" in rendered
    assert "简短回答" not in rendered
