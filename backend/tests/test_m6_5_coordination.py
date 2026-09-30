import asyncio

from api.main import (
    _active_chat_tasks,
    _coalesce_pending_chat_inputs,
    _complete_pending_chat_inputs,
    _pending_chat_inputs,
    _register_active_chat,
)
from api.schemas import ChatAttachmentInput, ChatRequest


def run(awaitable):
    return asyncio.run(awaitable)


def test_newest_execution_coalesces_uncommitted_user_bursts_and_attachments():
    case_id = "case-opaque"
    first = ChatRequest(
        message="先是 VPN 连不上",
        case_id=case_id,
        attachments=[ChatAttachmentInput(kind="log", name="first.log", content="691")],
    )
    second = ChatRequest(message="另外我刚改过密码", case_id=case_id)
    _pending_chat_inputs[case_id] = {1: first, 2: second}

    merged = _coalesce_pending_chat_inputs(case_id, 2, second)

    assert "VPN 连不上" in merged.message
    assert "刚改过密码" in merged.message
    assert [item.name for item in merged.attachments] == ["first.log"]
    _complete_pending_chat_inputs(case_id, 2)
    assert case_id not in _pending_chat_inputs


def test_registering_new_generation_cancels_older_synchronous_work():
    async def scenario():
        case_id = "case-cancel-old"
        first_started = asyncio.Event()

        async def first_run():
            _register_active_chat(case_id, 1, ChatRequest(message="first", case_id=case_id))
            first_started.set()
            await asyncio.Event().wait()

        async def second_run():
            _register_active_chat(case_id, 2, ChatRequest(message="second", case_id=case_id))
            await asyncio.sleep(0)

        first_task = asyncio.create_task(first_run())
        await first_started.wait()
        second_task = asyncio.create_task(second_run())
        await second_task
        await asyncio.gather(first_task, return_exceptions=True)
        cancelled = first_task.cancelled()
        _active_chat_tasks.pop(case_id, None)
        _pending_chat_inputs.pop(case_id, None)
        return cancelled

    assert run(scenario()) is True
