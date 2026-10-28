import io
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base, build_engine
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.invoice import InvoiceResult
from app.models.history import ProcessingHistory
from app.models.processing import ProcessingJob
from app.services.document_processing import DocumentProcessingService
from app.services.file_safety import FileValidationError
from app.services.ingestion import DocumentIngestionService
from app.services.processors.ocr import OCRService, OCRUnavailableError
from app.services.processors.pdf_processor import PDFProcessor
from app.services.processors.image_processor import ImageProcessor


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        upload_dir=tmp_path / "uploads",
        processed_dir=tmp_path / "processed",
        max_upload_bytes=5 * 1024 * 1024,
        max_pdf_pages=1000,
        min_pdf_text_chars=24,
        processing_timeout_seconds=30,
        ocr_timeout_seconds=10,
    )


@pytest.fixture
def db_factory(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def make_text_image(text: str, size=(1000, 240)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 80), text, fill="black", font=ImageFont.truetype("arial.ttf", 42) if Path("C:/Windows/Fonts/arial.ttf").exists() else ImageFont.load_default())
    return image


def make_pdf(path: Path, pages: list[str]):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is not installed")
    document = fitz.open()
    for text in pages:
        page = document.new_page(width=612, height=792)
        if text:
            page.insert_text((72, 100), text, fontsize=18)
    document.set_metadata({"title": "fixture invoice", "author": "Care Guide tests"})
    document.save(path)
    document.close()
    return fitz


def upload(filename: str, content: bytes, content_type="application/octet-stream"):
    return type("Upload", (), {"filename": filename, "content_type": content_type, "file": io.BytesIO(content)})()


def test_pdf_extracts_each_page_and_metadata(settings, tmp_path):
    pdf_path = tmp_path / "digital.pdf"
    make_pdf(pdf_path, ["Invoice one page content long enough for text extraction.", "Second page has its own invoice text content."])
    progress = []
    result, started, completed = DocumentProcessingService(settings).process(
        pdf_path, document_id=4, filename="digital.pdf",
        progress=lambda done, total, label: progress.append((done, total, label)),
    )
    assert result.page_count == 2
    assert result.pages_processed == 2
    assert [segment["page_number"] for segment in result.segments] == [1, 2]
    assert "Invoice one" in result.segments[0]["text"]
    assert result.metadata["title"] == "fixture invoice"
    assert completed >= started
    assert [event[:2] for event in progress] == [(1, 2), (2, 2)]


def test_encrypted_pdf_returns_a_password_error(settings, tmp_path):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is not installed")
    path = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()
    with pytest.raises(ValueError, match="requires a password"):
        DocumentProcessingService(settings).process(path, document_id=1, filename=path.name)


def test_pdf_page_limit_is_reported(settings, tmp_path):
    settings.max_pdf_pages = 1
    path = tmp_path / "many.pdf"
    make_pdf(path, ["First readable page has enough text to pass the OCR threshold.", "Second readable page also has enough text."])
    result, _, _ = DocumentProcessingService(settings).process(path, document_id=2, filename="many.pdf")
    assert result.page_count == 2
    assert result.pages_processed == 1
    assert result.status == "partial"
    assert result.warnings[0]["code"] == "page_limit_reached"


def test_scanned_pdf_uses_real_tesseract_when_available(settings, tmp_path):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is not installed")
    image_path = tmp_path / "scan.png"
    make_text_image("SCANNED INVOICE 4812").save(image_path)
    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.insert_image(page.rect, filename=str(image_path))
    doc.save(pdf_path)
    doc.close()
    try:
        OCRService(settings).verify()
    except OCRUnavailableError as exc:
        pytest.skip(f"Tesseract executable is not installed/configured: {exc}")
    result, _, _ = DocumentProcessingService(settings).process(pdf_path, document_id=3, filename="scanned.pdf")
    assert result.ocr_used is True
    assert result.segments[0]["ocr_used"] is True
    assert "4812" in result.segments[0]["text"]
    assert result.segments[0]["ocr_confidence"] is not None


def test_scanned_pdf_keeps_page_scoped_ocr_failure(settings, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is not installed")
    image_path = tmp_path / "scan.png"
    make_text_image("OCR FAILURE PATH").save(image_path)
    pdf_path = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.insert_image(page.rect, filename=str(image_path))
    doc.save(pdf_path)
    doc.close()
    processor = PDFProcessor(settings, OCRService(settings))
    def unavailable(_image):
        raise OCRUnavailableError("test: executable unavailable")
    monkeypatch.setattr(processor.ocr, "image_to_text", unavailable)
    output = processor.process(pdf_path, document_id=9, filename="scan.pdf", started_at=datetime.now(timezone.utc),
                               deadline=10**20)
    assert output.segments[0]["errors"][0]["code"] == "page_ocr_failed"
    assert output.segments[0]["errors"][0]["page"] == 1


def test_multipage_tiff_continues_when_one_ocr_frame_fails(settings, tmp_path, monkeypatch):
    path = tmp_path / "multi.tiff"
    first, second = make_text_image("TIFF PAGE ONE"), make_text_image("TIFF PAGE TWO")
    first.save(path, save_all=True, append_images=[second], format="TIFF")
    processor = ImageProcessor(settings, OCRService(settings))
    def unavailable(_image):
        raise OCRUnavailableError("test: executable unavailable")
    monkeypatch.setattr(processor.ocr, "image_to_text", unavailable)
    output = processor.process(path, document_id=10, filename="multi.tiff", started_at=datetime.now(timezone.utc),
                               deadline=10**20)
    assert output.page_count == 2
    assert [segment["errors"][0]["page"] for segment in output.segments] == [1, 2]
    assert output.pages_processed == 2


@pytest.mark.parametrize("extension,format_name", [("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"), ("tif", "TIFF")])
def test_image_files_validate_and_multipage_tiff_reports_frames(settings, tmp_path, extension, format_name):
    image_path = tmp_path / f"fixture.{extension}"
    make_text_image("local image invoice fixture").save(image_path, format=format_name)
    service = DocumentProcessingService(settings)
    assert service.validate(image_path, image_path.name, image_path.stat().st_size) == f".{extension}"

    multipage_path = tmp_path / "multiple.tiff"
    first, second = make_text_image("FIRST TIFF PAGE"), make_text_image("SECOND TIFF PAGE")
    first.save(multipage_path, save_all=True, append_images=[second], format="TIFF")
    assert service.validate(multipage_path, multipage_path.name, multipage_path.stat().st_size) == ".tiff"
    if not shutil.which("tesseract") and not settings.tesseract_cmd:
        pytest.skip("Image fixture/signature validated; OCR integration skipped because Tesseract is unavailable")
    result, _, _ = service.process(multipage_path, document_id=1, filename=multipage_path.name)
    assert result.page_count == 2
    assert len(result.segments) == 2


def test_image_ocr_integration_when_tesseract_is_available(settings, tmp_path):
    image_path = tmp_path / "ocr.png"
    make_text_image("LOCAL OCR TEST 7284").save(image_path)
    try:
        OCRService(settings).verify()
    except OCRUnavailableError as exc:
        pytest.skip(f"Tesseract executable is not installed/configured: {exc}")
    result, _, _ = DocumentProcessingService(settings).process(image_path, document_id=7, filename=image_path.name)
    assert result.ocr_used
    assert "7284" in result.segments[0]["text"]


def test_configured_missing_tesseract_path_is_reported(settings, tmp_path):
    settings.tesseract_cmd = tmp_path / "missing-tesseract.exe"
    with pytest.raises(OCRUnavailableError, match="was not found"):
        OCRService(settings).verify()


def test_excel_processes_all_sheets_and_keeps_cell_coordinates(settings, tmp_path):
    openpyxl = pytest.importorskip("openpyxl", reason="openpyxl is not installed")
    workbook = openpyxl.Workbook()
    invoice = workbook.active
    invoice.title = "Invoice Header"
    invoice.append(["Vendor", "Amount"])
    invoice.append(["Clinic One", 19.95])
    items = workbook.create_sheet("Line Items")
    items.append(["Description", "Qty"])
    items.append(["Consultation", 2])
    path = tmp_path / "invoice.xlsx"
    workbook.save(path)
    result, _, _ = DocumentProcessingService(settings).process(path, document_id=5, filename=path.name)
    assert [sheet["name"] for sheet in result.worksheet_metadata] == ["Invoice Header", "Line Items"]
    assert result.segments[0]["table"]["rows"][1]["cells"][0]["coordinate"] == "A2"
    assert result.segments[0]["table"]["rows"][1]["values"] == ["Clinic One", "19.95"]


def test_legacy_xls_processes_when_xlrd_and_fixture_writer_exist(settings, tmp_path):
    xlwt = pytest.importorskip("xlwt", reason="xlwt test fixture writer is not installed")
    pytest.importorskip("xlrd", reason="xlrd is not installed")
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Legacy")
    sheet.write(0, 0, "Vendor")
    sheet.write(1, 0, "Legacy Clinic")
    second = workbook.add_sheet("Legacy Items")
    second.write(0, 0, "Service")
    second.write(1, 0, "Assessment")
    path = tmp_path / "legacy.xls"
    workbook.save(str(path))
    result, _, _ = DocumentProcessingService(settings).process(path, document_id=6, filename=path.name)
    assert [sheet["name"] for sheet in result.worksheet_metadata] == ["Legacy", "Legacy Items"]
    assert "Legacy Clinic" in result.segments[0]["text"]


def test_csv_keeps_blank_rows_inconsistent_columns_and_context(settings, tmp_path):
    path = tmp_path / "claims.csv"
    path.write_text("vendor,amount\nClinic,12.50\n\nOther,3,extra\n", encoding="utf-8")
    result, _, _ = DocumentProcessingService(settings).process(path, document_id=8, filename=path.name)
    assert result.worksheet_metadata[0]["row_count"] == 4
    assert result.segments[0]["table"]["rows"][2]["values"] == [None, None, None]
    assert any(item["code"] == "inconsistent_column_count" for item in result.warnings)
    assert "A2: Clinic" in result.segments[0]["text"]


def test_csv_decodes_utf16_and_malformed_csv_reports_error(settings, tmp_path):
    path = tmp_path / "utf16.csv"
    path.write_text("name;amount\nClinic;12\n", encoding="utf-16")
    result, _, _ = DocumentProcessingService(settings).process(path, document_id=9, filename=path.name)
    assert result.metadata["encoding"] == "utf-16"
    malformed = tmp_path / "bad.csv"
    malformed.write_text('name,amount\n"unterminated,10', encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed CSV"):
        DocumentProcessingService(settings).process(malformed, document_id=10, filename=malformed.name)


@pytest.mark.parametrize("filename,content,code", [
    ("empty.csv", b"", "empty_file"),
    ("wrong.pdf", b"this is not a pdf", "invalid_signature"),
    ("notes.exe", b"MZ", "unsupported_format"),
    ("broken.xlsx", b"PK but no workbook", "invalid_signature"),
    ("corrupt.png", b"\x89PNG\r\n\x1a\nnot really a png", "corrupt_image"),
])
def test_empty_corrupt_and_unsupported_inputs_are_useful_errors(settings, tmp_path, filename, content, code):
    path = tmp_path / filename
    path.write_bytes(content)
    with pytest.raises(FileValidationError) as error:
        DocumentProcessingService(settings).validate(path, filename, len(content))
    assert error.value.code == code


def test_structurally_corrupt_pdf_is_recorded_as_processing_failure(settings, db_factory):
    service = DocumentIngestionService(settings)
    with db_factory() as db:
        result = service.ingest(upload("corrupt.pdf", b"%PDF-1.7\nnot a real PDF structure"), db)
        assert result["status"] == "failed"
        assert result["errors"][0]["code"] == "processing_failed"
        document = db.get(Document, result["source_document_id"])
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == document.id))
        extraction = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document.id))
        assert document.status == DocumentStatus.FAILED
        assert job.status == JobStatus.FAILED
        assert document.error_details
        assert Path(result["json_result_path"]).is_file()
        assert Path(extraction.extracted_text_path).is_file()


def test_corrupt_file_does_not_interrupt_next_valid_file(settings, db_factory):
    service = DocumentIngestionService(settings)
    with db_factory() as db:
        corrupt = service.ingest(upload("bad.csv", b'column\n"not closed'), db)
        valid = service.ingest(upload("good.csv", b"vendor,total\nClinic,25.00\n", "text/csv"), db)
        assert corrupt["status"] == "failed"
        assert valid["status"] == "completed"
        stored = db.scalar(select(Document).where(Document.id == valid["source_document_id"]))
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == stored.id))
        result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == stored.id))
        progress = db.scalar(select(ProcessingHistory).where(
            ProcessingHistory.document_id == stored.id,
            ProcessingHistory.event_type == "progress",
        ))
        assert stored.status == DocumentStatus.COMPLETED
        assert job.status == JobStatus.COMPLETED
        assert result.raw_extraction["segments"][0]["table"]["rows"][1]["values"] == ["Clinic", "25.00"]
        assert progress.event_data == {"completed": 2, "total": 2}
        assert Path(result.extracted_text_path).is_file()


def test_duplicate_hash_is_identified_without_saving_a_second_document(settings, db_factory):
    service = DocumentIngestionService(settings)
    payload = b"vendor,total\nClinic,25.00\n"
    with db_factory() as db:
        first = service.ingest(upload("one.csv", payload), db)
        duplicate = service.ingest(upload("two.csv", payload), db)
        assert first["status"] == "completed"
        assert duplicate["status"] == "duplicate"
        assert duplicate["duplicate_of_document_id"] == first["source_document_id"]
        assert len(db.scalars(select(Document)).all()) == 1


def test_upload_filename_never_becomes_a_storage_path(settings, db_factory):
    service = DocumentIngestionService(settings)
    with db_factory() as db:
        result = service.ingest(upload("..\\..\\outside.csv", b"name\nclinic\n"), db)
        document = db.get(Document, result["source_document_id"])
        assert Path(document.original_path).name == document.stored_filename
        assert document.stored_filename.endswith(".csv")
        assert ".." not in document.original_path


def test_upload_size_limit_is_enforced_while_streaming(settings, db_factory):
    settings.max_upload_bytes = 8
    service = DocumentIngestionService(settings)
    with db_factory() as db:
        result = service.ingest(upload("large.csv", b"123456789,more"), db)
        assert result["status"] == "failed"
        assert result["errors"][0]["code"] == "file_too_large"
        assert not list(settings.upload_dir.glob("*.csv"))


def test_expanded_xlsx_and_image_pixel_caps_are_enforced(settings, tmp_path):
    settings.max_archive_uncompressed_bytes = 8
    workbook_path = tmp_path / "expanded.xlsx"
    with zipfile.ZipFile(workbook_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "content types")
        archive.writestr("xl/workbook.xml", "a much longer workbook body")
    with pytest.raises(FileValidationError) as archive_error:
        DocumentProcessingService(settings).validate(workbook_path, workbook_path.name, workbook_path.stat().st_size)
    assert archive_error.value.code == "archive_too_large"

    settings.max_image_pixels = 100
    image_path = tmp_path / "huge.png"
    make_text_image("large").save(image_path)
    with pytest.raises(FileValidationError) as image_error:
        DocumentProcessingService(settings).validate(image_path, image_path.name, image_path.stat().st_size)
    assert image_error.value.code == "image_too_large"


def test_document_ingest_api_returns_per_file_results(settings, db_factory, monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app

    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
    def override_db():
        with db_factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.post("/documents/ingest", files=[
                ("files", ("good.csv", b"vendor,total\nClinic,18.50\n", "text/csv")),
                ("files", ("bad.csv", b'"bad csv', "text/csv")),
            ])
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_files"] == 2
        assert payload["processed_files"] == 1
        assert payload["failed_files"] == 1
        assert payload["results"][0]["source_document_id"] is not None
        assert payload["results"][1]["errors"][0]["code"] == "processing_failed"
    finally:
        app.dependency_overrides.pop(get_db, None)
