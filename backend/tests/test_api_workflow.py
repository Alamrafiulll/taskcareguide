import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.documents import router as invoice_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.api.v1_documents import router as documents_router
from app.config import Settings
from app.database import Base, build_engine, get_db
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.processing import ProcessingJob
from app.services.ingestion import DocumentIngestionService
from app.services.worker import DocumentWorker


def make_csv(total="220.00", marker=""):
    rows = ["Invoice Number,CG-801", "Invoice Date,2025-05-01", "Supplier,Northwind Medical",
            "Bill To,Care Guide Clinic", "Currency,USD", "Subtotal,200.00", "Tax,20.00",
            f"Total,{total}", "", "Description,Quantity,Unit Price,Line Total", "Consultation,2,100.00,200.00"]
    if marker:
        rows.append(f"Note,{marker}")
    return ("\n".join(rows) + "\n").encode()


@pytest.fixture
def api_context(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, upload_dir=tmp_path / "uploads", processed_dir=tmp_path / "processed",
                        worker_max_attempts=3, worker_poll_interval_seconds=0.1)
    import app.api.v1_documents as v1
    import app.api.search as search
    import app.api.documents as legacy
    monkeypatch.setattr(v1, "get_settings", lambda: settings)
    monkeypatch.setattr(search, "get_settings", lambda: settings)
    monkeypatch.setattr(legacy, "get_settings", lambda: settings)
    application = FastAPI()
    application.include_router(health_router, prefix="/api")
    application.include_router(documents_router)
    application.include_router(invoice_router, prefix="/api")
    application.include_router(search_router)
    def override_db():
        with factory() as db:
            yield db
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client, settings, factory
    engine.dispose()


def test_upload_queues_persisted_documents_worker_results_search_and_comparison(api_context):
    client, settings, factory = api_context
    response = client.post("/api/documents/upload", files=[
        ("files", ("invoice.csv", make_csv(), "text/csv")),
        ("files", ("invoice-copy.csv", make_csv(total="221.00", marker="second-source"), "text/csv")),
    ])
    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted_count"] == 2 and payload["rejected_count"] == 0
    ids = [item["document_id"] for item in payload["items"]]
    assert client.get(f"/api/documents/{ids[0]}/status").json()["status"] == "queued"
    worker = DocumentWorker(settings, factory, DocumentIngestionService(settings), worker_id="api-test-worker")
    assert worker.run_once()
    assert worker.run_once()
    metadata = client.get(f"/api/documents/{ids[0]}")
    assert metadata.status_code == 200 and metadata.json()["status"] == "completed"
    extraction = client.get(f"/api/documents/{ids[0]}/invoice")
    assert extraction.status_code == 200
    assert extraction.json()["invoice_number"] == "CG-801"
    assert extraction.json()["raw_extraction"]["invoice"]["supplier_name"] == "Northwind Medical"
    assert "json_result_path" not in extraction.json()["raw_extraction"]
    assert client.get(f"/api/documents/{ids[0]}/result").status_code == 200
    history = client.get(f"/api/documents/{ids[0]}/history")
    assert history.status_code == 200 and any(row["event_type"] == "processing_completed" for row in history.json())
    listing = client.get("/api/documents", params={"supplier": "northwind", "status": "completed", "page": 1, "page_size": 1})
    assert listing.status_code == 200 and listing.json()["total"] == 2 and len(listing.json()["items"]) == 1
    keyword = client.get("/api/search", params={"query": "consultation", "mode": "keyword"})
    assert keyword.status_code == 200 and keyword.json()["total"] == 2
    assert "searchable_text" not in keyword.text and str(settings.upload_dir) not in keyword.text
    comparison = client.post("/api/documents/compare", json={"document_a_id": ids[0], "document_b_id": ids[1]})
    assert comparison.status_code == 200
    assert comparison.json()["comparable"]
    assert any(item["field"] == "total_amount" and item["difference_a_minus_b"] == "-1.00"
               for item in comparison.json()["mismatched_fields"])
    errors = client.get(f"/api/documents/{ids[1]}/validation-errors")
    assert errors.status_code == 200
    corrected = client.put(f"/api/documents/{ids[0]}/invoice", json={
        "total_amount": "220.00", "line_items": [{"description": "Consultation", "quantity": "2",
        "unit_price": "100", "line_total": "200"}], "reason": "Verified against source", "corrected_by": "reviewer"})
    assert corrected.status_code == 200
    corrections = client.get(f"/api/documents/{ids[0]}/invoice/corrections")
    assert corrections.status_code == 200 and len(corrections.json()) == 2
    assert corrections.json()[0]["reason"] == "Verified against source"


def test_upload_rejects_unsupported_formats_and_invalid_ids(api_context):
    client, _settings, _factory = api_context
    response = client.post("/api/documents/upload", files=[("files", ("payload.exe", b"not executable", "application/octet-stream"))])
    assert response.status_code == 422
    assert (response.json().get("error") or response.json().get("detail"))["code"] == "no_files_accepted"
    assert client.get("/api/documents/99999").status_code == 404
    assert client.post("/api/documents/99999/retry").status_code == 404


def test_failed_job_can_be_retried_and_worker_completes_it(api_context):
    client, settings, factory = api_context
    uploaded = client.post("/api/documents/upload", files=[("files", ("retry.csv", make_csv(), "text/csv"))])
    document_id = uploaded.json()["items"][0]["document_id"]

    class FailingProcessor:
        def process_existing(self, _document_id, _db):
            raise RuntimeError("temporary local parser failure")

    worker = DocumentWorker(settings, factory, FailingProcessor(), worker_id="retry-worker")
    for _ in range(3):
        assert worker.run_once()
        with factory() as db:
            job = db.query(ProcessingJob).filter_by(document_id=document_id).one()
            job.next_attempt_at = datetime.now(timezone.utc)
            db.commit()
    with factory() as db:
        job = db.query(ProcessingJob).filter_by(document_id=document_id).one()
        assert job.status == JobStatus.FAILED and job.attempt_count == 3
        assert "temporary local parser failure" in job.error_details
    retried = client.post(f"/api/documents/{document_id}/retry")
    assert retried.status_code == 202 and retried.json()["status"] == "queued"
    worker.processor = DocumentIngestionService(settings)
    assert worker.run_once()
    assert client.get(f"/api/documents/{document_id}/status").json()["status"] == "completed"


def test_permanently_invalid_source_is_not_retried(api_context):
    client, settings, factory = api_context
    uploaded = client.post("/api/documents/upload", files=[("files", ("bad.csv", make_csv(), "text/csv"))])
    doc_id = uploaded.json()["items"][0]["document_id"]
    with factory() as db:
        document = db.get(Document, doc_id)
        (settings.upload_dir / document.stored_filename).write_bytes(b"\x00binary invalid csv")
    worker = DocumentWorker(settings, factory, DocumentIngestionService(settings), worker_id="invalid-worker")
    assert worker.run_once()
    with factory() as db:
        job = db.query(ProcessingJob).filter_by(document_id=doc_id).one()
        assert job.status == JobStatus.FAILED and job.permanent_failure is True
    response = client.post(f"/api/documents/{doc_id}/retry")
    assert response.status_code == 422
    assert (response.json().get("error") or response.json().get("detail"))["code"] == "permanent_file_error"


def test_semantic_search_uses_and_persists_cosine_embeddings(api_context, monkeypatch):
    client, settings, factory = api_context
    first = client.post("/api/documents/upload", files=[("files", ("a.csv", make_csv(marker="clinic care"), "text/csv"))]).json()
    second = client.post("/api/documents/upload", files=[("files", ("b.csv", make_csv(marker="other"), "text/csv"))]).json()
    worker = DocumentWorker(settings, factory, DocumentIngestionService(settings), worker_id="semantic-worker")
    assert worker.run_once() and worker.run_once()

    class TinyModel:
        def encode(self, texts, **_kwargs):
            import numpy as np
            return np.asarray([[1.0, 0.0] if text == "specialized query" or "clinic care" in text else [0.0, 1.0]
                               for text in texts], dtype=float)

    import app.services.semantic_search as semantic
    monkeypatch.setattr(semantic, "_load_model", lambda _name: TinyModel())
    response = client.get("/api/search", params={"query": "specialized query", "mode": "semantic"})
    assert response.status_code == 200
    assert response.json()["results"][0]["document"]["id"] == first["items"][0]["document_id"]
    with factory() as db:
        from app.models.embedding import DocumentEmbedding
        assert db.query(DocumentEmbedding).count() == 2


def test_semantic_search_reports_missing_optional_model_without_breaking_keyword(api_context, monkeypatch):
    client, settings, factory = api_context
    uploaded = client.post("/api/documents/upload", files=[("files", ("one.csv", make_csv(), "text/csv"))]).json()
    worker = DocumentWorker(settings, factory, DocumentIngestionService(settings), worker_id="no-model-worker")
    assert worker.run_once()

    import app.services.semantic_search as semantic
    monkeypatch.setattr(semantic, "_load_model", lambda _name: (_ for _ in ()).throw(
        semantic.EmbeddingUnavailable("Local embedding package/model is unavailable.")))
    semantic_result = client.get("/api/search", params={"query": "invoice", "mode": "semantic"})
    keyword_result = client.get("/api/search", params={"query": "invoice", "mode": "keyword"})
    assert semantic_result.status_code == 503
    assert keyword_result.status_code == 200 and keyword_result.json()["total"] == 1
    assert uploaded["items"][0]["status"] == "queued"

