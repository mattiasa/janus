from __future__ import annotations

import tomllib
from dataclasses import dataclass, field


@dataclass
class Config:
    server_port: int
    bind_address: str = "127.0.0.1"
    delay: int = 60
    rbl_delay: int = 3600
    gl_message: str = "Temporarily blocked for @SECONDS@ seconds."
    gc_days: int = 5
    gc_interval: int = 60
    db_url: str = "sqlite+aiosqlite:///greylist.db"
    control_socket: str = "/var/run/janus/janus.sock"
    rbls: list[str] = field(default_factory=list)


def load(path: str) -> Config:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if "server_port" not in data:
        raise ValueError("server_port is required in configuration")
    return Config(**data)
