from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .db import Database
from .rbl import RBLChecker

logger = logging.getLogger(__name__)


@dataclass
class ConnectionData:
    ip: str
    sender: str
    recipient: str
    queue_id: Optional[str] = None


@dataclass
class GreylistResult:
    pass_: bool
    message: Optional[str] = None


@dataclass
class Stats:
    clients: int = 0
    requests: int = 0
    first_insert: int = 0
    admitted_match: int = 0
    admitted_awl: int = 0
    first_reject: int = 0
    update: int = 0
    requests_per_second: float = 0.0
    version: str = "janus-0.1.0"


class Greylister:
    def __init__(self, cfg: Config, db: Database, rbl: RBLChecker, stats: Stats) -> None:
        self._cfg = cfg
        self._db = db
        self._rbl = rbl
        self.stats = stats

    async def check(self, data: ConnectionData, now: Optional[float] = None) -> GreylistResult:
        if now is None:
            now = time.time()
        cfg = self._cfg

        in_rbl = await self._rbl.is_in_rbls(data.ip)
        delay = cfg.rbl_delay if in_rbl else cfg.delay

        row = await self._db.get_entry(data.ip, data.sender, data.recipient)
        if row is None:
            await self._db.add_entry(data.ip, data.sender, data.recipient, now)
            self.stats.first_insert += 1
            time_left = float(delay)
            current_count = 0
        else:
            time_left = delay - (now - row.first_seen)
            if time_left < 0:
                current_count = row.connection_count + 1
                await self._db.update_entry(row.id, current_count, now)
            else:
                current_count = row.connection_count
            self.stats.update += 1

        # Pass: triplet has been retried successfully (count incremented to >= 1)
        if row is not None and current_count >= 1:
            logger.warning(
                "helm pass from=<%s> to=<%s> ip=%s",
                data.sender, data.recipient, data.ip,
            )
            self.stats.admitted_match += 1
            return GreylistResult(pass_=True)

        # Pass: AWL — IP has successful history and is not RBL-listed
        if not in_rbl and await self._db.check_awl(data.ip, now - cfg.delay):
            logger.warning(
                "helm awl from=<%s> to=<%s> ip=%s",
                data.sender, data.recipient, data.ip,
            )
            self.stats.admitted_awl += 1
            return GreylistResult(pass_=True)

        logger.warning(
            "helm blocked from=<%s> to=<%s> ip=%s delay remaining=%d",
            data.sender, data.recipient, data.ip, int(time_left),
        )
        self.stats.first_reject += 1
        msg = cfg.gl_message.replace("@SECONDS@", str(int(time_left)))
        return GreylistResult(pass_=False, message=msg)
