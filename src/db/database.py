"""Database connection and schema management.

`Database.migrate()` runs the Alembic migrations in `migrations/` (the same command the team
runs with `alembic upgrade head`). The app calls it at start-up, so a new clone needs no manual
database step. Switching to PostgreSQL later is a change of DATABASE_URL, not of code.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import PROJECT_ROOT
from src.utils.logging_config import get_logger

logger = get_logger("db")
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


class DatabaseError(Exception):
    """Raised when the database cannot be opened or migrated."""


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # SQLite ignores FOREIGN KEY constraints unless asked
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}   # Streamlit uses threads
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool                     # one shared in-memory DB
            else:
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.engine = create_engine(url, future=True, **kwargs)
        except Exception as exc:
            raise DatabaseError(f"Cannot open database ({type(exc).__name__}). Check DATABASE_URL.") from exc
        if url.startswith("sqlite"):
            _enable_sqlite_foreign_keys(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transaction scope: commit on success, roll back on any error."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def migrate(self) -> None:
        """Upgrade the schema to the latest Alembic revision."""
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.set_main_option("sqlalchemy.url", self.url)
        try:
            with self.engine.begin() as connection:
                cfg.attributes["connection"] = connection   # reuse our engine (needed for :memory:)
                command.upgrade(cfg, "head")
        except Exception as exc:
            raise DatabaseError(f"Database migration failed: {exc}") from exc
        logger.info("Database schema is up to date")
