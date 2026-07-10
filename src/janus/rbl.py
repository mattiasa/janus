from __future__ import annotations

import logging
from typing import Sequence

import dns.asyncresolver
from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers

from .config import Config

logger = logging.getLogger(__name__)


class RBLChecker:
    def __init__(self, rbls: Sequence[str]) -> None:
        self._rbls = list(rbls)

    @classmethod
    def from_config(cls, cfg: Config) -> RBLChecker:
        return cls(cfg.rbls)

    async def is_in_rbls(self, ip: str) -> bool:
        if not self._rbls:
            return False
        reversed_ip = ".".join(reversed(ip.split(".")))
        for rbl in self._rbls:
            if await self._is_in_rbl(reversed_ip, rbl):
                return True
        return False

    async def _is_in_rbl(self, reversed_ip: str, rbl: str) -> bool:
        hostname = f"{reversed_ip}.{rbl}"
        try:
            await dns.asyncresolver.resolve(hostname, "A")
            logger.debug("ip=%s found in rbl %s", reversed_ip, rbl)
            return True
        except (NXDOMAIN, NoAnswer, NoNameservers):
            return False
        except Exception as e:
            logger.debug("DNS lookup failed for %s: %s", hostname, e)
            return False
