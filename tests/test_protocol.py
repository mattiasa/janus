import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from janus.greylist import ConnectionData, GreylistResult
from janus.server import _handle_client, _read_request


def _make_reader(lines: list[str]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line.encode())
    reader.feed_eof()
    return reader


async def test_read_request_well_formed():
    reader = _make_reader([
        "request=smtpd_access_policy\n",
        "client_address=1.2.3.4\n",
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "queue_id=ABC123\n",
        "\n",
    ])
    data = await _read_request(reader)
    assert data is not None
    assert data.ip == "1.2.3.4"
    assert data.sender == "test@example.com"
    assert data.recipient == "user@domain.com"
    assert data.queue_id == "ABC123"


async def test_read_request_eof_returns_none():
    reader = asyncio.StreamReader()
    reader.feed_eof()
    data = await _read_request(reader)
    assert data is None


async def test_read_request_missing_client_address_returns_none():
    reader = _make_reader([
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "\n",
    ])
    data = await _read_request(reader)
    assert data is None


async def test_handle_client_pass_sends_dunno(greylister):
    reader = _make_reader([
        "client_address=1.2.3.4\n",
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "\n",
    ])
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    with patch.object(greylister, "check", new=AsyncMock(return_value=GreylistResult(pass_=True))):
        await _handle_client(reader, writer, greylister)

    written = b"".join(call.args[0] for call in writer.write.call_args_list)
    assert b"action=dunno" in written


async def test_handle_client_defer_sends_defer_if_permit(greylister):
    reader = _make_reader([
        "client_address=1.2.3.4\n",
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "\n",
    ])
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    deferred = GreylistResult(pass_=False, message="Temporarily blocked for 60 seconds.")
    with patch.object(greylister, "check", new=AsyncMock(return_value=deferred)):
        await _handle_client(reader, writer, greylister)

    written = b"".join(call.args[0] for call in writer.write.call_args_list)
    assert b"action=defer_if_permit" in written
    assert b"Temporarily blocked" in written


async def test_handle_client_multiple_requests_on_same_connection(greylister):
    """Postfix keeps connections open for multiple requests."""
    reader = _make_reader([
        "client_address=1.2.3.4\n",
        "sender=a@example.com\n",
        "recipient=u@domain.com\n",
        "\n",
        "client_address=2.3.4.5\n",
        "sender=b@example.com\n",
        "recipient=v@domain.com\n",
        "\n",
    ])
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    with patch.object(
        greylister, "check", new=AsyncMock(return_value=GreylistResult(pass_=True))
    ) as mock_check:
        await _handle_client(reader, writer, greylister)

    assert mock_check.call_count == 2


async def test_handle_client_check_error_sends_dunno(greylister):
    """On unexpected errors in check(), respond dunno (fail-open)."""
    reader = _make_reader([
        "client_address=1.2.3.4\n",
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "\n",
    ])
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    with patch.object(greylister, "check", new=AsyncMock(side_effect=RuntimeError("db error"))):
        await _handle_client(reader, writer, greylister)

    written = b"".join(call.args[0] for call in writer.write.call_args_list)
    assert b"action=dunno" in written


async def test_response_ends_with_blank_line():
    """Each policy response must end with a blank line per the Postfix protocol spec."""
    greylister_mock = MagicMock()
    greylister_mock.stats = MagicMock(clients=0, requests=0)
    greylister_mock.check = AsyncMock(return_value=GreylistResult(pass_=True))

    reader = _make_reader([
        "client_address=1.2.3.4\n",
        "sender=test@example.com\n",
        "recipient=user@domain.com\n",
        "\n",
    ])
    written_chunks: list[bytes] = []
    writer = MagicMock()
    writer.write = lambda data: written_chunks.append(data)
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    await _handle_client(reader, writer, greylister_mock)
    combined = b"".join(written_chunks)
    assert combined.endswith(b"\n\n")
