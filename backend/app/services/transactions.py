from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal


@contextmanager
def transaction_scope(session_factory: sessionmaker = SessionLocal) -> Generator[Session, None, None]:
    """Commit a unit of work on success; roll it back on any error."""
    session = session_factory()
    try:
        with session.begin():
            yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
