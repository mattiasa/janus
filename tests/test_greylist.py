from unittest.mock import AsyncMock, patch

import pytest

from janus.greylist import ConnectionData, Greylister, Stats
from janus.rbl import RBLChecker


def _data(ip="1.2.3.4", sender="test@example.com", recipient="user@domain.com"):
    return ConnectionData(ip=ip, sender=sender, recipient=recipient)


async def test_first_connection_is_deferred(greylister):
    result = await greylister.check(_data(), now=1000.0)
    assert not result.pass_
    assert result.message is not None
    assert greylister.stats.first_insert == 1
    assert greylister.stats.first_reject == 1


async def test_deferred_message_contains_seconds(greylister):
    result = await greylister.check(_data(), now=1000.0)
    assert "60" in result.message


async def test_retry_before_delay_is_still_deferred(greylister):
    await greylister.check(_data(), now=1000.0)
    result = await greylister.check(_data(), now=1030.0)  # 30s in, need 60s
    assert not result.pass_
    assert "30" in result.message  # 30 seconds remaining


async def test_retry_after_delay_passes(greylister):
    await greylister.check(_data(), now=1000.0)
    result = await greylister.check(_data(), now=1061.0)  # 61s > 60s delay
    assert result.pass_
    assert greylister.stats.admitted_match == 1


async def test_subsequent_requests_always_pass(greylister):
    await greylister.check(_data(), now=1000.0)
    await greylister.check(_data(), now=1061.0)  # establishes count=1
    result = await greylister.check(_data(), now=1200.0)
    assert result.pass_


async def test_stats_counters_update_correctly(greylister):
    d = _data()
    await greylister.check(d, now=1000.0)   # first_insert + first_reject
    await greylister.check(d, now=1061.0)   # update + admitted_match
    assert greylister.stats.first_insert == 1
    assert greylister.stats.first_reject == 1
    assert greylister.stats.update == 1
    assert greylister.stats.admitted_match == 1


async def test_awl_pass_for_new_triplet_from_known_ip(greylister):
    established = _data(sender="old@example.com")
    now = 1000.0
    await greylister.check(established, now=now)
    await greylister.check(established, now=now + 61)  # count becomes 1

    new_triplet = _data(sender="new@example.com")
    result = await greylister.check(new_triplet, now=now + 120)
    assert result.pass_
    assert greylister.stats.admitted_awl == 1


async def test_awl_requires_established_history(greylister):
    """IP with only a new (count=0) entry should not grant AWL."""
    existing = _data(sender="old@example.com")
    await greylister.check(existing, now=1000.0)  # count stays 0

    new_triplet = _data(sender="new@example.com")
    result = await greylister.check(new_triplet, now=1200.0)
    assert not result.pass_
    assert greylister.stats.admitted_awl == 0


async def test_rbl_ip_uses_rbl_delay(cfg, db):
    rbl = RBLChecker(rbls=["rbl.example."])
    stats = Stats()
    gl = Greylister(cfg, db, rbl, stats)

    with patch.object(rbl, "is_in_rbls", new=AsyncMock(return_value=True)):
        result = await gl.check(_data(ip="5.6.7.8"), now=1000.0)
    assert not result.pass_
    assert "3600" in result.message


async def test_rbl_ip_not_admitted_via_awl(cfg, db):
    rbl = RBLChecker(rbls=["rbl.example."])
    stats = Stats()
    gl = Greylister(cfg, db, rbl, stats)

    # Establish history for the IP without RBL
    established = _data(ip="9.10.11.12", sender="old@example.com")
    now = 1000.0
    with patch.object(rbl, "is_in_rbls", new=AsyncMock(return_value=False)):
        await gl.check(established, now=now)
        await gl.check(established, now=now + 61)

    # Now RBL-listed — new triplet from same IP must not get AWL pass
    new_triplet = _data(ip="9.10.11.12", sender="new@example.com")
    with patch.object(rbl, "is_in_rbls", new=AsyncMock(return_value=True)):
        result = await gl.check(new_triplet, now=now + 120)
    assert not result.pass_
    assert stats.admitted_awl == 0


async def test_gl_message_placeholder_replaced(greylister):
    result = await greylister.check(_data(), now=1000.0)
    assert "@SECONDS@" not in result.message


async def test_different_triplets_are_independent(greylister):
    """Two triplets from different IPs are tracked separately."""
    d1 = _data(ip="1.2.3.4", sender="a@example.com")
    d2 = _data(ip="5.6.7.8", sender="b@example.com")  # different IP — no AWL crossover
    await greylister.check(d1, now=1000.0)
    await greylister.check(d1, now=1061.0)  # d1 passes

    result = await greylister.check(d2, now=1100.0)  # d2 is new
    assert not result.pass_
