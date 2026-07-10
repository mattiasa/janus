from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .greylist import ConnectionData, Greylister

logger = logging.getLogger(__name__)


async def _read_request(reader: asyncio.StreamReader) -> Optional[ConnectionData]:
    """Read one Postfix policy request (key=value lines terminated by a blank line).

    Returns None on EOF or when mandatory fields are missing.
    """
    fields: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line:
            return None
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if decoded == "":
            break
        if "=" in decoded:
            key, _, value = decoded.partition("=")
            fields[key] = value

    ip = fields.get("client_address")
    sender = fields.get("sender")
    recipient = fields.get("recipient")

    if not ip or sender is None or recipient is None:
        logger.warning("Received request missing mandatory fields: %s", fields)
        return None

    return ConnectionData(
        ip=ip,
        sender=sender,
        recipient=recipient,
        queue_id=fields.get("queue_id"),
    )


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    greylister: Greylister,
) -> None:
    greylister.stats.clients += 1
    try:
        while True:
            try:
                data = await _read_request(reader)
            except Exception as e:
                logger.error("Error reading request: %s", e)
                writer.write(b"action=dunno\n\n")
                await writer.drain()
                break

            if data is None:
                break

            greylister.stats.requests += 1

            try:
                result = await greylister.check(data)
                action = "dunno" if result.pass_ else f"defer_if_permit {result.message}"
            except Exception as e:
                logger.error("Error in greylisting check: %s", e)
                action = "dunno"

            writer.write(f"action={action}\n\n".encode())
            await writer.drain()
    finally:
        greylister.stats.clients -= 1
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


class GreylistServer:
    def __init__(self, cfg, greylister: Greylister) -> None:
        self._cfg = cfg
        self._greylister = greylister
        self._server: Optional[asyncio.Server] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            lambda r, w: _handle_client(r, w, self._greylister),
            host=self._cfg.bind_address,
            port=self._cfg.server_port,
        )
        logger.warning(
            "Janus listening on %s:%d", self._cfg.bind_address, self._cfg.server_port
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
