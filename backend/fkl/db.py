"""Engine and session handling for the append-only store."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import SETTINGS
from .models import Base

_engine: Engine | None = None
_Session: sessionmaker | None = None


def _ensure_parent_dir(db_url: str) -> None:
    if db_url.startswith("sqlite:///"):
        Path(db_url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)


def get_engine(db_url: str | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _Session
    if _engine is None:
        url = db_url or SETTINGS.db_url
        _ensure_parent_dir(url)
        _engine = create_engine(url, future=True)

        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine. Used by tests that point at a different DB."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


class SchemaOutOfDate(RuntimeError):
    """The database predates the current model definitions."""


def init_db(db_url: str | None = None) -> Engine:
    """Create any missing tables, and refuse to run against a stale database.

    `create_all` adds missing tables but never missing *columns*, so a database
    written by an earlier phase keeps working right up until something reads a
    column that is not there. Checking up front turns a confusing runtime error
    into one sentence naming the file to delete.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        missing = {c.name for c in table.columns} - present
        if missing:
            raise SchemaOutOfDate(
                f"table '{table.name}' is missing {sorted(missing)}. "
                f"This database was written by an earlier version of the schema. "
                f"Delete it and re-ingest: the PDFs are the source of truth."
            )
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
