from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker
from app import models  # noqa: F401
from app.database import Base, build_engine
from app.models.document import Document
from app.services.transactions import transaction_scope


def test_database_initialization_creates_expected_tables(tmp_path):
    db_path = tmp_path / "nested" / "test.db"
    engine = build_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    expected = {"documents", "processing_jobs", "invoice_results", "line_items", "validation_errors", "processing_history", "manual_corrections"}
    assert expected <= tables
    engine.dispose()


def test_transaction_scope_rolls_back_and_returns_utc_timestamps(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'tx.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        try:
            with transaction_scope(factory) as db:
                document = Document(
                    original_filename="invoice.pdf", stored_filename="id.pdf",
                    original_path="uploads/id.pdf", file_size_bytes=12,
                )
                db.add(document)
                db.flush()
                document_id = document.id
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        with factory() as db:
            assert db.get(Document, document_id) is None

        with transaction_scope(factory) as db:
            db.add(Document(
                original_filename="invoice.pdf", stored_filename="saved.pdf",
                original_path="uploads/saved.pdf", file_size_bytes=12,
            ))
        with factory() as db:
            saved = db.query(Document).filter_by(stored_filename="saved.pdf").one()
            assert saved.created_at.utcoffset().total_seconds() == 0
    finally:
        engine.dispose()
