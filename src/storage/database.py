"""
database.py — Database Connection & Session Management
======================================================
WHY this abstraction layer?
  - Single source of truth for the DB connection string
  - get_session() returns a context manager → guarantees sessions are always
    closed (no connection leaks), even if an exception is raised mid-query
  - init_db() can be called once at app startup to create all tables safely
  - Swapping MySQL → SQLite means changing ONE env var, nothing else

SQLALCHEMY CONNECTION POOL:
  - pool_pre_ping=True: tests connections before use (handles MySQL's 8hr timeout)
  - pool_recycle=3600: recycles connections every hour to avoid stale handles
"""

import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import Base

load_dotenv()


def _build_connection_url() -> str:
    """
    Constructs the SQLAlchemy connection URL from environment variables.

    Format:  mysql+pymysql://user:password@host:port/dbname
    pymysql is the pure-Python MySQL driver — no system-level dependencies.
    """
    host     = os.getenv("DB_HOST", "localhost")
    port     = os.getenv("DB_PORT", "3306")
    name     = os.getenv("DB_NAME", "expense_tracker")
    user     = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")

    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"


# ── Engine (one per process) ────────────────────────────────────────────────────
# The engine manages the connection pool. Create it once at module load time.
engine = create_engine(
    _build_connection_url(),
    pool_pre_ping=True,    # Validates connections before handing them out
    pool_recycle=3600,     # Recycles connections every hour
    echo=False,            # Set True during development to log all SQL
)

# ── Session factory ─────────────────────────────────────────────────────────────
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager that yields a database session and handles cleanup.

    Usage:
        with get_session() as db:
            results = db.query(Transaction).filter(...).all()

    WHY context manager?
      - Automatically commits on success, rolls back on exception
      - Always closes the session → no connection pool exhaustion
      - Clean, Pythonic API — callers never touch session lifecycle
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """
    Creates all tables defined in models.py if they don't already exist.
    Safe to call multiple times — SQLAlchemy uses CREATE TABLE IF NOT EXISTS.

    Called once at Streamlit app startup (app.py).
    """
    Base.metadata.create_all(bind=engine)


def check_connection() -> tuple[bool, str]:
    """
    Tests the database connection. Returns (success, message).
    Used in the Settings page to validate credentials without crashing.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connected successfully"
    except Exception as exc:
        return False, str(exc)
