"""Single-process worker for durable M5 jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from .durable import DurableCaseRunRegistry
from .models import CaseJob

logger = logging.getLogger(__name__)

JobHandler = Callable[[CaseJob], Awaitable[dict]]


class DurableCaseJobWorker:
    def __init__(
        self,
        registry: DurableCaseRunRegistry,
        handler: JobHandler,
        *,
        poll_seconds: float = 0.5,
        lease_seconds: float = 180.0,
    ) -> None:
        self._registry = registry
        self._handler = handler
        self._poll_seconds = max(0.05, poll_seconds)
        self._lease_seconds = max(5.0, lease_seconds)
        self._owner = f"worker-{uuid4()}"
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._active_job: CaseJob | None = None
        self._active_handler: asyncio.Task | None = None

    @property
    def owner(self) -> str:
        return self._owner

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        await self._registry.recover_expired_jobs(force=False)
        self._task = asyncio.create_task(self._run(), name="concord-m5-job-worker")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._registry.release_leases(self._owner)

    async def interrupt_case(self, case_id: str) -> bool:
        """Cooperatively stop an in-flight handler after a lifecycle command."""

        if (
            self._active_job is None
            or self._active_job.case_id != case_id
            or self._active_handler is None
            or self._active_handler.done()
        ):
            return False
        self._active_handler.cancel()
        await asyncio.gather(self._active_handler, return_exceptions=True)
        return True

    async def _run(self) -> None:
        while not self._stopping:
            job = await self._registry.claim_next_job(
                self._owner, lease_seconds=self._lease_seconds
            )
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    pass
                continue
            heartbeat = asyncio.create_task(
                self._heartbeat(job.job_id), name=f"heartbeat:{job.job_id}"
            )
            self._active_job = job
            self._active_handler = asyncio.create_task(
                self._handler(job), name=f"case-job:{job.job_id}"
            )
            try:
                result = await asyncio.wait_for(
                    self._active_handler,
                    timeout=job.execution_timeout_seconds,
                )
                await self._registry.complete_job(
                    job.job_id,
                    self._owner,
                    result,
                    lease_generation=job.lease_generation,
                )
            except TimeoutError:
                logger.warning("durable Case job timed out job_id=%s", job.job_id)
                await self._registry.fail_job(
                    job.job_id,
                    self._owner,
                    "JOB_EXECUTION_TIMEOUT",
                    lease_generation=job.lease_generation,
                )
            except asyncio.CancelledError:
                if self._stopping:
                    raise
                logger.info("durable Case job interrupted job_id=%s", job.job_id)
            except Exception as exc:
                logger.exception("durable Case job failed job_id=%s", job.job_id)
                await self._registry.fail_job(
                    job.job_id,
                    self._owner,
                    str(exc),
                    lease_generation=job.lease_generation,
                )
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self._active_handler = None
                self._active_job = None

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            alive = await self._registry.heartbeat_job(
                job_id,
                self._owner,
                lease_generation=(
                    self._active_job.lease_generation
                    if self._active_job is not None and self._active_job.job_id == job_id
                    else None
                ),
                lease_seconds=self._lease_seconds,
            )
            if not alive:
                return


__all__ = ["DurableCaseJobWorker"]
