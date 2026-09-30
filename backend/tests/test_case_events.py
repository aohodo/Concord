import asyncio

import pytest

from core.adaptive_resolution import CaseEvent, CaseEventType
from infrastructure.case_events import CaseEventStore


def run(awaitable):
    return asyncio.run(awaitable)


def test_sqlite_case_event_store_survives_restart_and_deduplicates(tmp_path):
    path = tmp_path / "events.sqlite3"
    first = CaseEventStore(path)
    run(
        first.bind_thread(
            thread_id="thread-1",
            case_id="case-1",
            tenant_id="tenant-a",
            actor_id="user-a",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    event = CaseEvent(
        event_id="event-fixed",
        thread_id="thread-1",
        case_id="case-1",
        tenant_id="tenant-a",
        actor_id="user-a",
        type=CaseEventType.USER_EVIDENCE,
        payload={"raw_messages": ["刚才又弹了一个报错"]},
    )
    stored = run(first.enqueue(event))
    duplicate = run(first.enqueue(event))

    restarted = CaseEventStore(path)
    pending = run(restarted.list_events("thread-1", include_consumed=False))
    drained = run(restarted.drain("thread-1"))
    replay_after_uncheckpointed_crash = run(restarted.drain("thread-1"))

    assert stored.sequence == 1
    assert duplicate.sequence == 1
    assert [item.event_id for item in pending] == ["event-fixed"]
    assert [item.sequence for item in drained] == [1]
    assert [item.event_id for item in replay_after_uncheckpointed_crash] == [
        "event-fixed"
    ]
    assert run(restarted.drain("thread-1", after_sequence=1)) == []


def test_case_event_store_enforces_thread_ownership(tmp_path):
    store = CaseEventStore(tmp_path / "events.sqlite3")
    run(
        store.bind_thread(
            thread_id="thread-private",
            case_id="case-private",
            tenant_id="tenant-a",
            actor_id="user-a",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )

    with pytest.raises(PermissionError):
        run(
            store.assert_owner(
                "thread-private", tenant_id="tenant-a", actor_id="user-b"
            )
        )

    with pytest.raises(ValueError, match="case_id"):
        run(
            store.enqueue(
                CaseEvent(
                    thread_id="thread-private",
                    case_id="different-case",
                    tenant_id="tenant-a",
                    actor_id="user-a",
                    type=CaseEventType.USER_EVIDENCE,
                )
            )
        )
