from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    connect_args: dict = {}

    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        prefix = "sqlite:///"
        if url.startswith(prefix):
            db_path = url[len(prefix):]
            if db_path and not db_path.startswith(":memory:"):
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
        # Default isolation_level=None for SQLite lets SQLAlchemy manage
        # transactions explicitly instead of pysqlite's implicit BEGIN, which
        # mixes badly with long-running write transactions.
        connect_args["isolation_level"] = None

    eng = create_engine(url, future=True, connect_args=connect_args)

    if is_sqlite:
        # SQLite-only resilience pragmas. WAL lets readers proceed while a
        # writer holds the file; busy_timeout gives any blocked connection
        # up to 10s to wait out a competing writer before raising
        # "database is locked".
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=10000")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
            finally:
                cur.close()

    return eng


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
