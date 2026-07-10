import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from janus.gc import run_gc


async def test_gc_deletes_old_entries_after_initial_delay(cfg, db):
    now = 1_000_000.0
    cutoff = now - cfg.gc_days * 86400  # entries older than gc_days

    # Add one old entry and one recent entry
    await db.add_entry("1.2.3.4", "old@a.com", "r@b.com", cutoff - 1)
    await db.add_entry("2.3.4.5", "new@a.com", "r@b.com", now)

    # First sleep is the 600s initial delay; second sleep ends the loop.
    sleep_count = 0

    async def fast_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError

    with patch("janus.gc.asyncio.sleep", new=fast_sleep), \
         patch("janus.gc.time.time", return_value=now):
        with pytest.raises(asyncio.CancelledError):
            await run_gc(cfg, db)

    assert await db.get_entry("1.2.3.4", "old@a.com", "r@b.com") is None
    assert await db.get_entry("2.3.4.5", "new@a.com", "r@b.com") is not None


async def test_gc_runs_periodically(cfg, db):
    """GC should run multiple times when not cancelled."""
    call_count = 0
    now = 1_000_000.0
    sleep_count = 0

    async def fast_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError

    original_delete = db.delete_old_entries
    async def counting_delete(cutoff):
        nonlocal call_count
        call_count += 1
        return await original_delete(cutoff)

    with patch("janus.gc.asyncio.sleep", new=fast_sleep), \
         patch("janus.gc.time.time", return_value=now), \
         patch.object(db, "delete_old_entries", new=counting_delete):
        with pytest.raises(asyncio.CancelledError):
            await run_gc(cfg, db)

    # Called once per GC run (sleep 0 = initial delay, sleeps 1,2 = gc intervals)
    assert call_count == 2


async def test_gc_cancellation_propagates(cfg, db):
    """CancelledError must not be swallowed."""
    async def instant_cancel(seconds):
        raise asyncio.CancelledError

    with patch("janus.gc.asyncio.sleep", new=instant_cancel):
        with pytest.raises(asyncio.CancelledError):
            await run_gc(cfg, db)
