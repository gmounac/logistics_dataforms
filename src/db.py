"""Engine, session factory and shared column types."""

import sqlite3
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite has no timezone support: store naive UTC, hand back aware UTC.

    Rejects naive datetimes on the way in so callers can't sneak in local time.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """Per-connection SQLite setup.

    * foreign_keys — SQLite leaves FK enforcement off by default.
    * journal_mode=WAL — readers don't block the writer and vice versa, so the
      phone and the PC can hit the yard at the same time. Persisted in the file
      header; a no-op for an in-memory database.
    * busy_timeout — with WAL you can still collide on the single writer lock;
      wait up to 5s for it instead of raising "database is locked".
    * synchronous=NORMAL — safe to pair with WAL (no corruption on crash, only
      the last transaction is at risk on power loss) and much faster.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def make_engine(url: str = "sqlite:///yard.db", *, echo: bool = False):
    return create_engine(url, echo=echo)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    from src import models  # noqa: F401  ensure tables are registered

    Base.metadata.create_all(engine)
