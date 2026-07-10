import pytest

from janus.config import Config
from janus.db import Database
from janus.greylist import Greylister, Stats
from janus.rbl import RBLChecker


@pytest.fixture
def cfg():
    return Config(
        server_port=19999,
        bind_address="127.0.0.1",
        delay=60,
        rbl_delay=3600,
        gl_message="Temporarily blocked for @SECONDS@ seconds.",
        gc_days=5,
        gc_interval=60,
        db_url="sqlite+aiosqlite:///:memory:",
        control_socket="/tmp/janus-test.sock",
        rbls=[],
    )


@pytest.fixture
async def db(cfg):
    database = Database.from_config(cfg)
    await database.create_tables()
    yield database
    await database.close()


@pytest.fixture
def stats():
    return Stats()


@pytest.fixture
def rbl():
    return RBLChecker(rbls=[])


@pytest.fixture
def greylister(cfg, db, rbl, stats):
    return Greylister(cfg, db, rbl, stats)
