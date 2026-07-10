from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path
from typing import Optional

from .config import Config, load as load_config
from .db import Database
from .gc import run_gc
from .greylist import Greylister, Stats
from .rbl import RBLChecker
from .server import GreylistServer

_USAGE = "usage: janus <config.toml> <start|stop|statistics|create-database|reset-database|gc>"
_COMMANDS = {"start", "stop", "statistics", "create-database", "reset-database", "gc"}


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    syslog_paths = ["/dev/log", "/var/run/syslog"]
    handler: logging.Handler = logging.StreamHandler()
    for path in syslog_paths:
        if Path(path).exists():
            try:
                handler = logging.handlers.SysLogHandler(
                    address=path,
                    facility=logging.handlers.SysLogHandler.LOG_MAIL,
                )
                break
            except OSError:
                pass
    handler.setFormatter(logging.Formatter("janus: %(message)s"))
    root.addHandler(handler)


async def _track_rps(stats: Stats) -> None:
    last_requests = stats.requests
    last_time = time.monotonic()
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        cur = stats.requests
        delta_t = now - last_time
        if delta_t > 0:
            stats.requests_per_second = (cur - last_requests) / delta_t
        last_requests = cur
        last_time = now


async def _handle_control(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    db: Database,
    greylister: Greylister,
    shutdown_event: asyncio.Event,
) -> None:
    try:
        line = await reader.readline()
        cmd = json.loads(line.decode())
        command = cmd.get("cmd")

        if command == "stop":
            writer.write(b'{"result": "ok"}\n')
            await writer.drain()
            writer.close()
            shutdown_event.set()
            return

        if command == "gc":
            cutoff = time.time() - greylister._cfg.gc_days * 86400
            deleted = await db.delete_old_entries(cutoff)
            response = {"result": "ok", "deleted": deleted}

        elif command == "statistics":
            s = greylister.stats
            response = {
                "clients": s.clients,
                "version": s.version,
                "requests": s.requests,
                "first_insert": s.first_insert,
                "admitted_match": s.admitted_match,
                "admitted_awl": s.admitted_awl,
                "first_reject": s.first_reject,
                "update": s.update,
                "requests_per_second": round(s.requests_per_second, 3),
            }

        else:
            response = {"error": f"unknown command: {command!r}"}

        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
    except Exception as e:
        try:
            writer.write(json.dumps({"error": str(e)}).encode() + b"\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()


async def _run_control_server(
    cfg: Config,
    db: Database,
    greylister: Greylister,
    shutdown_event: asyncio.Event,
) -> None:
    sock_path = Path(cfg.control_socket)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.unlink(missing_ok=True)

    server = await asyncio.start_unix_server(
        lambda r, w: _handle_control(r, w, db, greylister, shutdown_event),
        path=str(sock_path),
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        sock_path.unlink(missing_ok=True)


async def run_server(cfg: Config) -> None:
    db = Database.from_config(cfg)
    rbl = RBLChecker.from_config(cfg)
    stats = Stats()
    greylister = Greylister(cfg, db, rbl, stats)
    server = GreylistServer(cfg, greylister)
    shutdown_event = asyncio.Event()

    await server.start()

    gc_task = asyncio.create_task(run_gc(cfg, db), name="gc")
    rps_task = asyncio.create_task(_track_rps(stats), name="rps")
    ctrl_task = asyncio.create_task(
        _run_control_server(cfg, db, greylister, shutdown_event), name="control"
    )

    try:
        await shutdown_event.wait()
    finally:
        gc_task.cancel()
        rps_task.cancel()
        ctrl_task.cancel()
        await asyncio.gather(gc_task, rps_task, ctrl_task, return_exceptions=True)
        await server.stop()
        await db.close()


async def _send_control_command(cfg: Config, cmd: dict) -> dict:
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.control_socket)
    except (FileNotFoundError, ConnectionRefusedError):
        print(
            f"Error: cannot connect to {cfg.control_socket}. Is janus running?",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        writer.write(json.dumps(cmd).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        return json.loads(line.decode())
    finally:
        writer.close()


async def _setup_db(cfg: Config, command: str) -> None:
    db = Database.from_config(cfg)
    try:
        if command == "create-database":
            await db.create_tables()
            print("Database created.")
        elif command == "reset-database":
            await db.reset()
            print("Database reset.")
    finally:
        await db.close()


def main() -> None:
    if len(sys.argv) != 3:
        print(_USAGE, file=sys.stderr)
        sys.exit(1)

    config_path, command = sys.argv[1], sys.argv[2]

    if command not in _COMMANDS:
        print(f"Unknown command: {command}\n{_USAGE}", file=sys.stderr)
        sys.exit(1)

    try:
        cfg = load_config(config_path)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    _setup_logging()

    if command == "start":
        asyncio.run(run_server(cfg))
    elif command in ("create-database", "reset-database"):
        asyncio.run(_setup_db(cfg, command))
    else:
        result = asyncio.run(_send_control_command(cfg, {"cmd": command}))
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if command == "statistics":
            stat_types = {
                "clients": "gauge", "version": "string", "requests": "counter",
                "first_insert": "counter", "admitted_match": "counter",
                "admitted_awl": "counter", "first_reject": "counter",
                "update": "counter", "requests_per_second": "gauge",
            }
            for key, value in result.items():
                kind = stat_types.get(key, "value")
                print(f"{kind}/{key}: {value}")
        else:
            print(result.get("result", "ok"))


if __name__ == "__main__":
    main()
