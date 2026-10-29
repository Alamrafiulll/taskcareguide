import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Base, build_engine
from app.models.invoice import InvoiceResult
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.services.financial_validation import is_duplicate_invoice, validate_invoice
from app.services.invoice_comparison import compare_invoices
from app.services.invoice_extraction import extract_invoice, suggest_invoice_corrections
from app.services.ingestion import DocumentIngestionService
from app.api.documents import router as documents_router
from app.database import get_db


GROUND_TRUTH = json.loads((Path(__file__).parent / "fixtures/invoices/ground_truth.json").read_text(encoding="utf-8"))


def invoice_text(*, number="CG-1001", tax="20.00", total="220.00", supplier="Northwind Medical"):
    number_line = f"Invoice Number: {number}" if number is not None else ""
    return "\n".join(filter(None, [
        "INVOICE", number_line, "Invoice Date: 2025-04-12", f"Supplier: {supplier}",
        "Bill To: Care Guide Clinic", "Currency: USD", "Subtotal: 200.00",
        f"Tax: {tax}" if tax is not None else "", f"Total: {total}",
    ]))


def table_rows(line_total="200.00", description="Consultation"):
    return [
        {"row_number": 1, "values": ["Description", "Quantity", "Unit Price", "Line Total"], "cells": []},
        {"row_number": 2, "values": [description, "2", "100.00", line_total], "cells": []},
    ]


def normalized(text=None, *, number="CG-1001", tax="20.00", total="220.00", line_total="200.00", supplier="Northwind Medical"):
    text = text if text is not None else invoice_text(number=number, tax=tax, total=total, supplier=supplier)
    return {"segments": [{"type": "page", "page_number": 1, "text": text,
                          "table": {"rows": table_rows(line_total)}}], "ocr_used": False}


def test_deterministic_extraction_matches_ground_truth_and_keeps_page_evidence():
    result = extract_invoice(normalized())
    expected = GROUND_TRUTH["valid"]
    for field in ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax_amount", "total_amount", "currency"):
        assert result[field] == expected[field]
    item = result["line_items"][0]
    assert item["description"] == expected["line_items"][0]["description"]
    assert Decimal(item["quantity"]) == Decimal("2")
    assert Decimal(item["unit_price"]) == Decimal("100.00")
    assert Decimal(item["line_total"]) == Decimal("200.00")
    assert result["field_evidence"]["invoice_number"][0]["page"] == 1
    assert result["extraction_method"] == "deterministic"


def test_missing_number_and_tax_are_not_invented_and_are_flagged():
    missing_number = extract_invoice(normalized(number=None))
    assert missing_number["invoice_number"] is None
    assert missing_number["field_status"]["invoice_number"] == "missing"
    missing_tax = extract_invoice(normalized(tax=None, total="200.00"))
    assert missing_tax["tax_amount"] is None
    assert any(item["code"] == "field_missing" and item["field"] == "tax_amount" for item in missing_tax["warnings"])


def test_unreadable_value_remains_null_and_uncertain():
    text = invoice_text(number="unreadable")
    result = extract_invoice(normalized(text))
    assert result["invoice_number"] is None
    assert result["field_status"]["invoice_number"] == GROUND_TRUTH["unreadable_invoice_number"]["status"]


def test_local_model_suggestions_only_return_grounded_fields_flagged_for_review(monkeypatch):
    import app.services.invoice_extraction as extraction

    settings = Settings(_env_file=None, ollama_enabled=True, ollama_model="local-test-model")
    source = normalized(total="220.00")
    source_invoice = extract_invoice(source)
    candidate = extraction.InvoiceExtraction.model_validate({
        field: source_invoice[field] for field in extraction.InvoiceExtraction.model_fields
    })
    current = extract_invoice(normalized(total="221.00"))
    source["invoice_validation"] = validate_invoice(current)
    monkeypatch.setattr(extraction, "_ollama_extract", lambda *_: candidate)

    result = suggest_invoice_corrections(source, current, settings)
    assert result["model"] == "local-test-model"
    assert [item["field"] for item in result["suggestions"]] == ["total_amount"]
    assert result["suggestions"][0]["current_value"] == "221.00"
    assert result["suggestions"][0]["suggested_value"] == "220.00"
    assert "Total: 220.00" in result["suggestions"][0]["evidence"]


def test_ocr_and_conflicting_source_values_are_flagged_for_human_review():
    segments = [{"type": "page", "page_number": 1, "text": invoice_text() + "\nTotal: 221.00", "ocr_used": True}]
    result = extract_invoice({"segments": segments, "ocr_used": True})
    assert result["field_status"]["total_amount"] == "uncertain"
    assert any(item["code"] == "field_conflicting_evidence" and item["field"] == "total_amount" for item in result["warnings"])
    assert any(item["code"] == "field_low_quality" for item in result["warnings"])
    findings = validate_invoice(result)
    assert {item["code"] for item in findings} >= {"conflicting_source_values", "ocr_source"}


def test_financial_validation_exact_decimal_reconciliation_and_line_rules():
    base = extract_invoice(normalized())
    assert validate_invoice(base) == []
    incorrect = extract_invoice(normalized(total=GROUND_TRUTH["incorrect_total"]["expected_total"]))
    assert GROUND_TRUTH["incorrect_total"]["reconciliation_error"] in {e["code"] for e in validate_invoice(incorrect)}
    mismatch = extract_invoice(normalized(line_total=GROUND_TRUTH["line_sum_mismatch"]["observed_line_sum"]))
    errors = validate_invoice(mismatch)
    assert GROUND_TRUTH["line_sum_mismatch"]["error"] in {e["code"] for e in errors}
    invalid_date = {**base, "invoice_date": "2025-02-30"}
    assert "invalid_invoice_date" in {e["code"] for e in validate_invoice(invalid_date)}


def test_duplicate_detection_requires_supplier_and_invoice_identifier():
    first = {"invoice_number": "CG-1001", "supplier_name": "Northwind Medical Ltd"}
    duplicate = {"invoice_number": "cg 1001", "supplier_name": "Northwind Medical"}
    unrelated_amount_same = {"invoice_number": "CG-1002", "supplier_name": "Northwind Medical"}
    assert is_duplicate_invoice(first, duplicate)
    assert not is_duplicate_invoice(first, unrelated_amount_same)
    assert not is_duplicate_invoice({"total_amount": "220.00"}, {"total_amount": "220.00"})


def test_comparison_both_directions_reports_exact_amount_difference():
    a = extract_invoice(normalized())
    b = extract_invoice(normalized(total="221.00"))
    forward = compare_invoices(a, b)
    reverse = compare_invoices(b, a)
    assert forward["comparable"] and reverse["comparable"]
    mismatch = next(item for item in forward["mismatched_fields"] if item["field"] == "total_amount")
    assert mismatch["difference_a_minus_b"] == "-1.00"
    reverse_mismatch = next(item for item in reverse["mismatched_fields"] if item["field"] == "total_amount")
    assert reverse_mismatch["difference_a_minus_b"] == "1.00"
    unrelated = extract_invoice(normalized(number="CG-2002", supplier="Other Supplier"))
    assert not compare_invoices(a, unrelated)["comparable"]


def test_invalid_ollama_output_falls_back_to_deterministic(monkeypatch):
    import app.services.invoice_extraction as extraction
    from app.config import Settings

    calls = []
    def bad(*_args, **_kwargs):
        calls.append(1)
        return None
    monkeypatch.setattr(extraction, "_ollama_extract", bad)
    settings = Settings(_env_file=None, ollama_enabled=True, ollama_model="local-model")
    output = extract_invoice(normalized(), settings)
    assert output["invoice_number"] == "CG-1001"
    assert output["extraction_method"] == "deterministic"


def test_invoice_schema_rejects_unknown_keys_and_bad_currency():
    from app.schemas.invoice import InvoiceExtraction
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate({"invoice_number": "CG-1001", "invented_field": "value"})
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate({"currency": "DOLLARS"})


def test_ollama_malformed_response_retries_are_bounded(monkeypatch):
    import app.services.invoice_extraction as extraction
    from app.config import Settings

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"response":"not-json"}'
    calls = []
    def malformed(*_args, **_kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(extraction.urllib.request, "urlopen", malformed)
    settings = Settings(_env_file=None, ollama_enabled=True, ollama_model="local-model")
    assert extraction._ollama_extract(settings, "Invoice Number: CG-1001") is None
    assert len(calls) == extraction.RETRY_LIMIT == 2


def test_ingestion_persists_invoice_validation_and_comparison_with_pdf_and_xlsx(tmp_path):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required for PDF fixture")
    pd = pytest.importorskip("pandas", reason="pandas is required for XLSX fixture")
    pytest.importorskip("openpyxl", reason="openpyxl is required for XLSX fixture")
    engine = build_engine(f"sqlite:///{(tmp_path / 'invoice.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, upload_dir=tmp_path / "uploads", processed_dir=tmp_path / "processed",
                        database_url=f"sqlite:///{(tmp_path / 'invoice.db').as_posix()}", min_pdf_text_chars=0)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((60, 80), invoice_text() + "\nDescription | Quantity | Unit Price | Line Total\nConsultation | 2 | 100.00 | 200.00", fontsize=12)
    pdf_bytes = pdf.tobytes()
    pdf.close()
    workbook = tmp_path / "invoice.xlsx"
    rows = [["Invoice Number", "CG-1001"], ["Invoice Date", "2025-04-12"], ["Supplier", "Northwind Medical"],
            ["Bill To", "Care Guide Clinic"], ["Currency", "USD"], ["Subtotal", "200.00"], ["Tax", "20.00"],
            ["Total", "220.00"], [None], ["Description", "Quantity", "Unit Price", "Line Total"],
            ["Consultation", 2, 100, 200]]
    pd.DataFrame(rows).to_excel(workbook, header=False, index=False)
    service = DocumentIngestionService(settings)
    with factory() as db:
        result_pdf = service.ingest(_upload("invoice.pdf", pdf_bytes), db)
        result_xlsx = service.ingest(_upload("invoice.xlsx", workbook.read_bytes()), db)
        assert result_pdf["invoice"]["invoice_number"] == "CG-1001"
        assert result_pdf["invoice_validation"] == []
        stored = db.query(InvoiceResult).filter_by(document_id=result_pdf["source_document_id"]).one()
        assert stored.extraction_method == "deterministic"
        assert stored.vendor_name == "Northwind Medical"
        assert len(stored.line_items) == 1
        assert "duplicate_invoice" in {item["code"] for item in result_xlsx["invoice_validation"]}
        comparison = compare_invoices(result_pdf["invoice"], result_xlsx["invoice"])
        assert comparison["comparable"]
        assert not comparison["mismatched_fields"]
        assert not comparison["missing_fields"]
        reverse = compare_invoices(result_xlsx["invoice"], result_pdf["invoice"])
        assert reverse["comparable"] and not reverse["mismatched_fields"]
    engine.dispose()


def test_correction_endpoint_preserves_original_and_comparison_api_reads_stored_values(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'corrections.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for index, total in ((1, "220.00"), (2, "221.00")):
            document = Document(original_filename=f"invoice{index}.pdf", stored_filename=f"{index}.pdf",
                                original_path=f"uploads/{index}.pdf", file_size_bytes=10,
                                status=DocumentStatus.COMPLETED)
            invoice = InvoiceResult(invoice_number="CG-1001", invoice_date="2025-04-12", vendor_name="Northwind Medical",
                                    customer_name="Care Guide Clinic", currency="USD", subtotal=Decimal("200"),
                                    tax_amount=Decimal("20"), total_amount=Decimal(total), extraction_method="deterministic",
                                    raw_extraction={"invoice": {"invoice_number": "CG-1001", "total_amount": total}})
            document.invoice_result = invoice
            db.add(document)
        db.commit()
        docs = db.query(Document).order_by(Document.id).all()
        ids = [doc.id for doc in docs]
    app = FastAPI()
    app.include_router(documents_router)
    def override_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        correction = client.post(f"/documents/{ids[0]}/invoice/corrections",
                                 json={"field_name": "total_amount", "corrected_value": "219.99", "corrected_by": "reviewer"})
        assert correction.status_code == 200
        history = client.get(f"/documents/{ids[0]}/invoice/corrections")
        assert history.status_code == 200
        assert history.json()[0]["previous_value"] == {"value": "220.00"}
        assert history.json()[0]["created_at"]
        compared = client.post("/documents/compare", json={"document_a_id": ids[0], "document_b_id": ids[1]})
        assert compared.status_code == 200
        result = compared.json()
        total = next(item for item in result["mismatched_fields"] if item["field"] == "total_amount")
        assert total["source_a"] == "219.99" and total["difference_a_minus_b"] == "-1.01"
    with factory() as db:
        corrected = db.query(InvoiceResult).filter_by(document_id=ids[0]).one()
        assert corrected.raw_extraction["original_invoice"]["total_amount"] == "220.00"
        assert db.query(InvoiceResult).filter_by(document_id=ids[0]).one().document.corrections[0].previous_value == {"value": "220.00"}
    engine.dispose()


def _upload(filename: str, content: bytes):
    return type("Upload", (), {"filename": filename, "content_type": "application/octet-stream", "file": io.BytesIO(content)})()
