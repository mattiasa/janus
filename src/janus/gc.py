from __future__ import annotations

import asyncio
import logging
import time

from .config import Config
from .db import Database

logger = logging.getLogger(__name__)

_INITIAL_DELAY = 600  # 10 minutes, matching Helm


async def run_gc(cfg: Config, db: Database) -> None:
    logger.info("Garbage collector started, initial delay %ds", _INITIAL_DELAY)
    try:
        await asyncio.sleep(_INITIAL_DELAY)
        while True:
            logger.info("Running garbage collection")
            cutoff = time.time() - cfg.gc_days * 86400
            deleted = await db.delete_old_entries(cutoff)
            logger.info("Garbage collection complete, deleted %d entries", deleted)
            await asyncio.sleep(cfg.gc_interval)
    except asyncio.CancelledError:
        raise
