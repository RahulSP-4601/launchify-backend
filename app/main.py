from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import configure_logging, get_settings
from app.services.database import ensure_schema
from app.services.job_runner import job_runner

configure_logging()
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not settings.serves_api:
        raise RuntimeError("PROCESS_ROLE=worker must start via `python -m app.worker`, not `uvicorn app.main:app`.")
    logger.info("Startup: ensuring database schema")
    ensure_schema()
    logger.info("Startup: process role=%s job_runner_enabled=%s", settings.process_role, settings.should_run_job_runner)
    if settings.should_run_job_runner:
        logger.info("Startup: starting job runner")
        job_runner.start()
    else:
        logger.info("Startup: job runner disabled for this process")
    try:
        yield
    finally:
        if settings.should_run_job_runner:
            logger.info("Shutdown: stopping job runner")
            await job_runner.stop()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
