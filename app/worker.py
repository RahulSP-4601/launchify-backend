from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.services.database import ensure_schema
from app.services.job_runner import job_runner

logger = logging.getLogger(__name__)
settings = get_settings()


async def main() -> None:
    if not settings.serves_worker:
        raise RuntimeError("PROCESS_ROLE=web must start via `uvicorn app.main:app`, not `python -m app.worker`.")
    logger.info("Worker startup: ensuring database schema")
    ensure_schema()
    logger.info("Worker startup: starting job runner")
    job_runner.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        logger.info("Worker shutdown: stopping job runner")
        await job_runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
