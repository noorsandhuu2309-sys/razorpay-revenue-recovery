"""Engine, session and bootstrap for the OMNIX platform database.

SQLite, one file next to the other OMNIX stores. The pragmas below are not
optional decoration — without WAL, the background job threads and the request
threads deadlock on the first concurrent write, which is exactly the workload
this database has.

Schema changes go through Alembic (`migrations/`). `init_db()` adopts a
pre-Alembic database automatically on first boot — see its docstring — so no
manual stamping is needed on an existing install.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .schema import Base

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "omnix_platform.db"

_engine: Engine | None = None
_Session: sessionmaker | None = None
_lock = threading.Lock()


def _url() -> str:
    """OMNIX_DATABASE_URL wins, so a deployment can point at Postgres without
    touching code. Otherwise the local SQLite file."""
    return os.environ.get("OMNIX_DATABASE_URL") or f"sqlite:///{DEFAULT_PATH}"


def engine() -> Engine:
    global _engine, _Session
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        url = _url()
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # check_same_thread=False because job threads and request threads
            # share the engine; SQLAlchemy's pool does the isolation.
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        elif url.startswith(("postgresql", "postgres")):
            # Sized for the workload rather than left at SQLAlchemy's default 5.
            # OMNIX runs several long-lived background threads — the intent
            # sweep, the TERRA service, the model keeper, one per execution —
            # and each holds a connection for the length of its transaction. A
            # pool of 5 deadlocks the API the moment three runs are in flight,
            # which presents as the app hanging rather than as a database error.
            kwargs.update(
                pool_size=int(os.environ.get("OMNIX_DB_POOL_SIZE") or 10),
                max_overflow=int(os.environ.get("OMNIX_DB_MAX_OVERFLOW") or 20),
                pool_recycle=1800,      # under most managed-Postgres idle cutoffs
                pool_timeout=30,
            )
        eng = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(eng, "connect")
            def _pragmas(dbapi_conn, _record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                # WAL: concurrent readers during a write. Without it the
                # execution engine blocks every API request while a job runs.
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _engine = eng
        _Session = sessionmaker(bind=eng, expire_on_commit=False, future=True)
        return _engine


def _alembic_config():
    """Alembic config pointed at the repo's migrations directory."""
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def init_db() -> None:
    """Bring the schema up to date. Safe to call on every boot.

    Three cases, because this database predates its migrations:

      * **Fresh database** — run the migrations. Normal path from here on.
      * **Existing database, never stamped** — it was built by `create_all()`
        before Alembic existed. Creating the tables it is missing and then
        stamping it as current adopts it without a destructive rebuild. This
        branch runs once per pre-existing database and never again.
      * **Stamped database** — plain upgrade.

    Falling back to `create_all()` if Alembic is unavailable is deliberate: a
    missing dev dependency should not stop the server booting, and `create_all`
    is correct for every case except altering an existing column.
    """
    eng = engine()
    try:
        from alembic import command
        from alembic.runtime.migration import MigrationContext
    except ImportError:
        Base.metadata.create_all(eng)
        return

    try:
        with eng.connect() as conn:
            stamped = MigrationContext.configure(conn).get_current_revision()
        inspector = __import__("sqlalchemy").inspect(eng)
        has_tables = bool(inspector.get_table_names())
        cfg = _alembic_config()

        if stamped is None and has_tables:
            # Legacy create_all() database. Add what the new models introduced,
            # then declare it current so future migrations apply cleanly.
            Base.metadata.create_all(eng)
            command.stamp(cfg, "head")
        else:
            command.upgrade(cfg, "head")
    except Exception:
        # Never let a migration problem stop the app from starting locally.
        # A schema that is behind shows up as a query error with a real
        # message; a server that refuses to boot shows up as nothing at all.
        Base.metadata.create_all(eng)


@contextmanager
def session() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on error.

    Callers in request handlers should let exceptions propagate to FastAPI;
    callers inside job threads should catch, because a crashed thread loses the
    run silently.
    """
    if _Session is None:
        engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def healthy() -> tuple[bool, str]:
    """Cheap probe for /api/health and PULSE."""
    try:
        with session() as s:
            s.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]
