from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.services.job_store import job_store
from app.services.processing import process_job

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 3


class JobRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self._process_pending_jobs()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _process_pending_jobs(self) -> None:
        while True:
            job = await asyncio.to_thread(job_store.claim_next_job)
            if job is None:
                return
            try:
                await asyncio.to_thread(process_job, job.id)
            except RuntimeError as exc:
                logger.warning("Job processing failed for %s: %s", job.id, exc)


job_runner = JobRunner()
