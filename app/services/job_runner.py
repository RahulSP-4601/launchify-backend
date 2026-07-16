from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.config import get_settings
from app.services.job_store import job_store
from app.services.processing import process_job

logger = logging.getLogger(__name__)


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
            try:
                await self._process_pending_jobs()
            except Exception:
                logger.exception("Job runner loop failed unexpectedly.")
            await asyncio.sleep(get_settings().job_runner_poll_interval_seconds)

    async def _process_pending_jobs(self) -> None:
        while True:
            job = await asyncio.to_thread(job_store.claim_next_job)
            if job is None:
                return
            try:
                await asyncio.to_thread(process_job, job.id)
            except Exception as exc:
                logger.exception("Job processing crashed for %s.", job.id)
                await asyncio.to_thread(job_store.mark_failed, job.id, f"Unexpected processing failure: {exc}")


job_runner = JobRunner()
