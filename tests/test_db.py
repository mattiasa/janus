import pytest

from janus.db import Database


async def test_add_and_get_entry(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "sender@a.com", "rcpt@b.com", now)
    row = await db.get_entry("1.2.3.4", "sender@a.com", "rcpt@b.com")
    assert row is not None
    assert row.ip == "1.2.3.4"
    assert row.sender == "sender@a.com"
    assert row.recipient == "rcpt@b.com"
    assert row.first_seen == now
    assert row.last_seen == now
    assert row.connection_count == 0


async def test_get_entry_returns_none_for_unknown(db):
    row = await db.get_entry("9.9.9.9", "x@x.com", "y@y.com")
    assert row is None


async def test_update_entry(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "sender@a.com", "rcpt@b.com", now)
    row = await db.get_entry("1.2.3.4", "sender@a.com", "rcpt@b.com")
    await db.update_entry(row.id, 1, now + 100)
    updated = await db.get_entry("1.2.3.4", "sender@a.com", "rcpt@b.com")
    assert updated.connection_count == 1
    assert updated.last_seen == now + 100
    assert updated.first_seen == now  # unchanged


async def test_duplicate_add_is_idempotent(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "sender@a.com", "rcpt@b.com", now)
    await db.add_entry("1.2.3.4", "sender@a.com", "rcpt@b.com", now + 5)
    row = await db.get_entry("1.2.3.4", "sender@a.com", "rcpt@b.com")
    assert row.first_seen == now  # original, not overwritten


async def test_check_awl_true(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "old@a.com", "rcpt@b.com", now)
    row = await db.get_entry("1.2.3.4", "old@a.com", "rcpt@b.com")
    await db.update_entry(row.id, 1, now + 60)
    # cutoff = now + 61 means first_seen=1000 < 1061, so it qualifies
    assert await db.check_awl("1.2.3.4", now + 61)


async def test_check_awl_false_count_zero(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "new@a.com", "rcpt@b.com", now)
    assert not await db.check_awl("1.2.3.4", now + 61)


async def test_check_awl_false_too_recent(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "recent@a.com", "rcpt@b.com", now)
    row = await db.get_entry("1.2.3.4", "recent@a.com", "rcpt@b.com")
    await db.update_entry(row.id, 1, now + 60)
    # cutoff = now + 30 means first_seen=1000 is NOT < 1030 — wait, 1000 < 1030 is True
    # The AWL cutoff is "now - delay". So if delay=60 and we're at now+30, cutoff=now+30-60=now-30
    # first_seen=1000 > 970, so it should NOT qualify (entry too recent)
    assert not await db.check_awl("1.2.3.4", now - 30)


async def test_check_awl_unknown_ip(db):
    assert not await db.check_awl("5.5.5.5", 9999999.0)


async def test_delete_old_entries(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "old@a.com", "rcpt@b.com", now)
    await db.add_entry("2.3.4.5", "new@a.com", "rcpt@b.com", now + 500)
    # Delete entries last_seen <= now + 100 (covers the first, not the second)
    deleted = await db.delete_old_entries(now + 100)
    assert deleted == 1
    assert await db.get_entry("1.2.3.4", "old@a.com", "rcpt@b.com") is None
    assert await db.get_entry("2.3.4.5", "new@a.com", "rcpt@b.com") is not None


async def test_reset_clears_all_entries(db):
    now = 1000.0
    await db.add_entry("1.2.3.4", "a@a.com", "b@b.com", now)
    await db.add_entry("2.3.4.5", "c@c.com", "d@d.com", now)
    await db.reset()
    assert await db.get_entry("1.2.3.4", "a@a.com", "b@b.com") is None
    assert await db.get_entry("2.3.4.5", "c@c.com", "d@d.com") is None
