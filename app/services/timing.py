from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

logger = logging.getLogger(__name__)


@contextmanager
def timed_stage(stage_name: str, warn_after_seconds: float | None = None) -> Iterator[None]:
    started_at = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - started_at
        if warn_after_seconds is not None and elapsed > warn_after_seconds:
            logger.warning("%s took %.2fs, above the %.2fs target.", stage_name, elapsed, warn_after_seconds)
        else:
            logger.info("%s completed in %.2fs.", stage_name, elapsed)
