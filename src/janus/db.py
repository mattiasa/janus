from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import Config

metadata = MetaData()

greylist_table = Table(
    "greylist",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sender", String(255), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("ip", String(48), nullable=False),
    Column("first_seen", Float, nullable=False),
    Column("last_seen", Float, nullable=False),
    Column("connection_count", Integer, nullable=False, default=0),
    UniqueConstraint("ip", "sender", "recipient", name="uq_triplet"),
    Index("idx_awl", "ip", "first_seen", "connection_count"),
)


@dataclass
class GreylistRow:
    id: int
    ip: str
    sender: str
    recipient: str
    first_seen: float
    last_seen: float
    connection_count: int


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_config(cls, cfg: Config) -> Database:
        engine = create_async_engine(cfg.db_url)
        return cls(engine)

    @classmethod
    def from_url(cls, url: str) -> Database:
        return cls(create_async_engine(url))

    async def create_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def reset(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(greylist_table))

    async def get_entry(self, ip: str, sender: str, recipient: str) -> Optional[GreylistRow]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(greylist_table).where(
                    greylist_table.c.ip == ip,
                    greylist_table.c.sender == sender,
                    greylist_table.c.recipient == recipient,
                )
            )
            row = result.fetchone()
        if row is None:
            return None
        return GreylistRow(
            id=row.id,
            ip=row.ip,
            sender=row.sender,
            recipient=row.recipient,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            connection_count=row.connection_count,
        )

    async def add_entry(self, ip: str, sender: str, recipient: str, now: float) -> None:
        values = dict(
            ip=ip, sender=sender, recipient=recipient,
            first_seen=now, last_seen=now, connection_count=0,
        )
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            stmt = (
                pg_insert(greylist_table)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["ip", "sender", "recipient"])
            )
        else:
            # SQLite: INSERT OR IGNORE; MySQL: INSERT IGNORE
            from sqlalchemy import insert as base_insert
            prefix = "OR IGNORE" if dialect == "sqlite" else "IGNORE"
            stmt = base_insert(greylist_table).values(**values).prefix_with(prefix)

        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def update_entry(self, entry_id: int, connection_count: int, last_seen: float) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(greylist_table)
                .where(greylist_table.c.id == entry_id)
                .values(connection_count=connection_count, last_seen=last_seen)
            )

    async def check_awl(self, ip: str, cutoff: float) -> bool:
        """True if the IP has any entry older than cutoff with connection_count >= 1."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(greylist_table.c.id)
                .where(
                    greylist_table.c.ip == ip,
                    greylist_table.c.first_seen < cutoff,
                    greylist_table.c.connection_count >= 1,
                )
                .limit(1)
            )
            return result.fetchone() is not None

    async def delete_old_entries(self, cutoff: float) -> int:
        """Delete entries last seen before cutoff. Returns the number of deleted rows."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(greylist_table).where(greylist_table.c.last_seen <= cutoff)
            )
            return result.rowcount

    async def close(self) -> None:
        await self._engine.dispose()
