from __future__ import annotations

import asyncio
import logging

from app.services.database import ensure_schema
from app.services.job_runner import job_runner

logger = logging.getLogger(__name__)


async def main() -> None:
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
