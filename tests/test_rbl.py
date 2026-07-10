from unittest.mock import AsyncMock, patch

import dns.asyncresolver
import pytest
from dns.resolver import NXDOMAIN, NoAnswer

from janus.rbl import RBLChecker


@pytest.fixture
def rbl_checker():
    return RBLChecker(rbls=["rbl.example."])


async def test_listed_ip_returns_true(rbl_checker):
    with patch("dns.asyncresolver.resolve", new=AsyncMock(return_value=object())):
        assert await rbl_checker.is_in_rbls("1.2.3.4")


async def test_unlisted_ip_returns_false(rbl_checker):
    with patch("dns.asyncresolver.resolve", new=AsyncMock(side_effect=NXDOMAIN())):
        assert not await rbl_checker.is_in_rbls("1.2.3.4")


async def test_no_answer_returns_false(rbl_checker):
    with patch("dns.asyncresolver.resolve", new=AsyncMock(side_effect=NoAnswer())):
        assert not await rbl_checker.is_in_rbls("1.2.3.4")


async def test_dns_exception_returns_false(rbl_checker):
    with patch("dns.asyncresolver.resolve", new=AsyncMock(side_effect=Exception("timeout"))):
        assert not await rbl_checker.is_in_rbls("1.2.3.4")


async def test_ip_address_reversal(rbl_checker):
    """1.2.3.4 should be looked up as 4.3.2.1.rbl.example."""
    captured = []

    async def mock_resolve(hostname, rtype):
        captured.append(hostname)
        raise NXDOMAIN()

    with patch("dns.asyncresolver.resolve", new=mock_resolve):
        await rbl_checker.is_in_rbls("1.2.3.4")

    assert captured == ["4.3.2.1.rbl.example."]


async def test_no_rbls_configured_returns_false():
    checker = RBLChecker(rbls=[])
    assert not await checker.is_in_rbls("1.2.3.4")


async def test_first_matching_rbl_short_circuits():
    """Should return True after the first matching RBL without querying the rest."""
    checker = RBLChecker(rbls=["first.rbl.", "second.rbl."])
    call_count = 0

    async def mock_resolve(hostname, rtype):
        nonlocal call_count
        call_count += 1
        if "first.rbl" in hostname:
            return object()
        raise NXDOMAIN()

    with patch("dns.asyncresolver.resolve", new=mock_resolve):
        result = await checker.is_in_rbls("1.2.3.4")

    assert result is True
    assert call_count == 1
