from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
    return engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from app.models import document, embedding, history, invoice, processing, search, worker  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # Additive SQLite upgrades for databases created by earlier assessment stages.
    additions = {
        "invoice_results": {
            "customer_name": "VARCHAR(255)",
            "extraction_method": "VARCHAR(32)",
            "field_confidence": "JSON",
        },
        "validation_errors": {
            "explanation": "TEXT",
            "expected_value": "JSON",
            "observed_value": "JSON",
        },
        "manual_corrections": {"reason": "TEXT", "correction_source": "VARCHAR(24) NOT NULL DEFAULT 'human'"},
        "processing_jobs": {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "worker_id": "VARCHAR(64)",
            "lease_until": "DATETIME",
            "next_attempt_at": "DATETIME",
            "permanent_failure": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}'))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
