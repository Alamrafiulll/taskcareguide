import hashlib
import json
import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.history import ProcessingHistory
from app.models.invoice import InvoiceResult, LineItem, ValidationError
from app.models.processing import ProcessingJob
from app.models.search import DocumentSearchIndex
from app.services.document_processing import DocumentProcessingService
from app.services.file_safety import FileValidationError
from app.services.invoice_extraction import extract_invoice
from app.services.financial_validation import is_duplicate_invoice, validate_invoice

logger = logging.getLogger(__name__)
CHUNK_SIZE = 1024 * 1024


def display_filename(raw_name: str | None) -> str:
    # Treat both slash styles as separators regardless of the host OS.
    name = PurePosixPath((raw_name or "document").replace("\\", "/")).name
    safe = "".join(char for char in name if char.isprintable() and char not in "\x00")
    return (safe or "document")[:255]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _decimal_or_none(value):
    if value is None:
        return None
    from decimal import Decimal
    return Decimal(str(value))


class DocumentIngestionService:
    def __init__(self, settings: Settings | None = None, processing: DocumentProcessingService | None = None):
        self.settings = settings or get_settings()
        self.processing = processing or DocumentProcessingService(self.settings)

    def enqueue(self, upload, db: Session) -> dict[str, Any]:
        """Validate and durably queue an upload without doing document extraction."""
        filename = display_filename(getattr(upload, "filename", None))
        extension = Path(filename).suffix.lower()
        internal_id = uuid4().hex
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings.upload_dir / f".{internal_id}.part"
        stored_path = self.settings.upload_dir / f"{internal_id}{extension}"
        digest = hashlib.sha256()
        size = 0
        try:
            with temp_path.open("wb") as target:
                while True:
                    chunk = upload.file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise FileValidationError("file_too_large", f"File exceeds the configured {self.settings.max_upload_bytes}-byte limit")
                    digest.update(chunk)
                    target.write(chunk)
            sha256 = digest.hexdigest()
            self.processing.validate(temp_path, filename, size)
            existing = db.scalar(select(Document).where(Document.sha256 == sha256).order_by(Document.id).limit(1))
            if existing:
                return {"document_id": existing.id, "job_id": None, "filename": filename, "status": "duplicate",
                        "sha256": sha256, "duplicate_of_document_id": existing.id, "error": None}
            stored_path = self.settings.upload_dir / f"{internal_id}{extension}"
            temp_path.replace(stored_path)
            document = Document(
                original_filename=filename, stored_filename=stored_path.name,
                original_path=str(stored_path.relative_to(self.settings.upload_dir.parent)),
                mime_type=mimetypes.guess_type(filename)[0] or getattr(upload, "content_type", None),
                file_size_bytes=size, sha256=sha256, status=DocumentStatus.QUEUED,
            )
            job = ProcessingJob(status=JobStatus.QUEUED, retry_count=0, attempt_count=0)
            document.jobs.append(job)
            document.history.append(ProcessingHistory(event_type="uploaded", status=JobStatus.QUEUED.value,
                                                      event_data={"sha256": sha256, "size_bytes": size}))
            db.add(document)
            db.commit()
            db.refresh(document)
            db.refresh(job)
            return {"document_id": document.id, "job_id": job.id, "filename": filename, "status": "queued",
                    "sha256": sha256, "duplicate_of_document_id": None, "error": None}
        except FileValidationError as exc:
            db.rollback()
            stored_path.unlink(missing_ok=True)
            return {"document_id": None, "job_id": None, "filename": filename, "status": "rejected",
                    "sha256": None, "duplicate_of_document_id": None, "error": {"code": exc.code, "message": str(exc)}}
        except Exception:
            db.rollback()
            stored_path.unlink(missing_ok=True)
            logger.exception("Could not queue uploaded file")
            return {"document_id": None, "job_id": None, "filename": filename, "status": "rejected",
                    "sha256": None, "duplicate_of_document_id": None,
                    "error": {"code": "upload_failed", "message": "The file could not be accepted for processing."}}
        finally:
            temp_path.unlink(missing_ok=True)

    def process_existing(self, document_id: int, db: Session) -> dict[str, Any]:
        """Process a worker-claimed document and persist all extraction results."""
        document = db.get(Document, document_id)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} was not found")
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == document_id)
                        .order_by(ProcessingJob.id.desc()).limit(1))
        if job is None or job.status != JobStatus.RUNNING:
            raise RuntimeError("Document job is not claimed by a worker")
        stored_name = document.stored_filename
        if Path(stored_name).name != stored_name or stored_name.startswith("."):
            raise FileValidationError("invalid_storage_name", "Stored document name is invalid")
        source_path = (self.settings.upload_dir / stored_name).resolve()
        if source_path.parent != self.settings.upload_dir.resolve():
            raise FileValidationError("invalid_storage_path", "Stored document path is outside the upload directory")
        self.processing.validate(source_path, document.original_filename, document.file_size_bytes)
        started = datetime.now(timezone.utc)

        def progress(done: int, total: int | None, label: str | None) -> None:
            db.add(ProcessingHistory(document_id=document.id, event_type="progress", status=JobStatus.RUNNING.value,
                                     details=label, event_data={"completed": done, "total": total}))
            job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=self.settings.worker_lease_seconds)
            db.commit()

        output, actual_started, completed = self.processing.process(
            source_path, document_id=document.id, filename=document.original_filename, progress=progress)
        normalized = {
            "source_document_id": document.id, "filename": document.original_filename, "format": output.format,
            "status": output.status, "page_count": output.page_count, "pages_processed": output.pages_processed,
            "worksheet_metadata": output.worksheet_metadata, "segments": output.segments, "metadata": output.metadata,
            "ocr_used": output.ocr_used, "warnings": output.warnings, "errors": output.errors,
            "processing_started_at": actual_started.isoformat(), "processing_completed_at": completed.isoformat(),
            "processing_duration_seconds": round((completed - actual_started).total_seconds(), 3),
            "sha256": document.sha256, "rows_processed": sum(item.get("row_count", 0) for item in output.worksheet_metadata),
            "is_duplicate": False,
        }
        invoice = extract_invoice(normalized, self.settings)
        financial_errors = validate_invoice(invoice, rounding_tolerance=self.settings.invoice_rounding_tolerance,
                                            allow_negative_amounts=self.settings.allow_negative_invoice_amounts)
        existing = db.scalars(select(InvoiceResult).where(InvoiceResult.invoice_number.is_not(None),
                                                          InvoiceResult.vendor_name.is_not(None))).all()
        duplicate_ids = [row.document_id for row in existing if is_duplicate_invoice(
            invoice, {"invoice_number": row.invoice_number, "supplier_name": row.vendor_name})]
        if duplicate_ids:
            financial_errors.append({"code": "duplicate_invoice", "field": "invoice_number", "severity": "warning",
                                     "message": "The supplier and normalized invoice identifier match a previously processed invoice.",
                                     "explanation": "Matching amount alone is not considered a duplicate.",
                                     "expected_value": {"existing_document_ids": duplicate_ids},
                                     "observed_value": {"invoice_number": invoice.get("invoice_number"),
                                                        "supplier_name": invoice.get("supplier_name")}})
        normalized.update({"invoice": invoice, "invoice_validation": financial_errors,
                           "duplicate_invoice_document_ids": duplicate_ids,
                           "review_recommended": bool(invoice.get("warnings") or financial_errors)})
        normalized["warnings"].extend(invoice.get("warnings", []))
        normalized = _json_safe(normalized)
        searchable_text = "\n".join([document.original_filename, *[segment.get("text", "") for segment in output.segments]])
        search_index = db.get(DocumentSearchIndex, document.id)
        if search_index is None:
            search_index = DocumentSearchIndex(document_id=document.id, searchable_text="")
            db.add(search_index)
        search_index.searchable_text = searchable_text[:self.settings.search_index_max_chars]
        self.settings.processed_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(stored_name).stem
        json_path = self.settings.processed_dir / f"{stem}.json"
        text_path = self.settings.processed_dir / f"{stem}.txt"
        _write_atomic(json_path, json.dumps(normalized, ensure_ascii=False, indent=2))
        text_content = "\n\n".join(f"[{segment.get('type')} {segment.get('page_number', segment.get('sheet_name', ''))}]\n{segment.get('text', '')}" for segment in output.segments)
        _write_atomic(text_path, text_content)
        invoice_result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document.id))
        if invoice_result is None:
            invoice_result = InvoiceResult(document_id=document.id)
            db.add(invoice_result)
        else:
            invoice_result.line_items.clear()
            invoice_result.validation_errors.clear()
        invoice_result.vendor_name = invoice.get("supplier_name")
        invoice_result.customer_name = invoice.get("customer_name")
        invoice_result.invoice_number = invoice.get("invoice_number")
        invoice_result.invoice_date = invoice.get("invoice_date")
        invoice_result.currency = invoice.get("currency")
        invoice_result.subtotal = _decimal_or_none(invoice.get("subtotal"))
        invoice_result.tax_amount = _decimal_or_none(invoice.get("tax_amount"))
        invoice_result.total_amount = _decimal_or_none(invoice.get("total_amount"))
        invoice_result.extracted_text_path = str(text_path)
        invoice_result.raw_extraction = normalized
        invoice_result.extraction_method = invoice.get("extraction_method")
        invoice_result.field_confidence = invoice.get("field_confidence")
        for position, item in enumerate(invoice.get("line_items", [])):
            invoice_result.line_items.append(LineItem(description=item.get("description") or "Unspecified item",
                quantity=_decimal_or_none(item.get("quantity")), unit_price=_decimal_or_none(item.get("unit_price")),
                amount=_decimal_or_none(item.get("line_total")), position=position))
        for finding in financial_errors:
            invoice_result.validation_errors.append(ValidationError(field_name=finding["field"], code=finding["code"],
                message=finding["message"], severity=finding["severity"], explanation=finding["explanation"],
                expected_value=finding["expected_value"], observed_value=finding["observed_value"]))
        document.status = DocumentStatus.COMPLETED
        document.error_details = None if not output.errors else json.dumps(output.errors, ensure_ascii=False)
        job.status = JobStatus.COMPLETED
        job.completed_at = completed
        job.lease_until = None
        job.error_details = None
        document.history.append(ProcessingHistory(event_type="processing_completed", status=output.status,
            details=f"Processed {output.pages_processed} page(s) and {normalized['rows_processed']} row(s).",
            event_data={"warnings": output.warnings, "errors": output.errors}))
        db.commit()
        return normalized

    def ingest(self, upload, db: Session) -> dict[str, Any]:
        request_started = datetime.now(timezone.utc)
        original_filename = display_filename(getattr(upload, "filename", None))
        extension = Path(original_filename).suffix.lower()
        internal_id = uuid4().hex
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        self.settings.processed_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings.upload_dir / f".{internal_id}.part"
        original_path = self.settings.upload_dir / f"{internal_id}{extension}"
        sha256 = hashlib.sha256()
        size_bytes = 0
        document: Document | None = None
        job: ProcessingJob | None = None
        try:
            with temp_path.open("wb") as target:
                while True:
                    chunk = upload.file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > self.settings.max_upload_bytes:
                        raise FileValidationError("file_too_large", f"File exceeds the configured {self.settings.max_upload_bytes}-byte limit")
                    sha256.update(chunk)
                    target.write(chunk)
            digest = sha256.hexdigest()
            self.processing.validate(temp_path, original_filename, size_bytes)
            duplicate = db.scalar(select(Document).where(Document.sha256 == digest).order_by(Document.id).limit(1))
            if duplicate:
                return {
                    "source_document_id": duplicate.id, "filename": original_filename,
                    "format": extension.lstrip("."), "status": "duplicate", "is_duplicate": True,
                    "duplicate_of_document_id": duplicate.id, "sha256": digest,
                    "processing_started_at": request_started.isoformat(),
                    "processing_completed_at": datetime.now(timezone.utc).isoformat(),
                    "processing_duration_seconds": 0.0,
                    "page_count": None, "pages_processed": 0, "worksheet_metadata": [],
                    "segments": [], "metadata": {}, "ocr_used": False, "warnings": [],
                    "errors": [{"code": "duplicate_file", "message": "Identical content is already stored.", "document_id": duplicate.id}],
                }
            temp_path.replace(original_path)
            relative_original = str(original_path.relative_to(self.settings.upload_dir.parent))
            document = Document(
                original_filename=original_filename, stored_filename=original_path.name,
                original_path=relative_original, mime_type=mimetypes.guess_type(original_filename)[0] or getattr(upload, "content_type", None),
                file_size_bytes=size_bytes, sha256=digest, status=DocumentStatus.QUEUED,
            )
            job = ProcessingJob(status=JobStatus.QUEUED, retry_count=0)
            document.jobs.append(job)
            document.history.append(ProcessingHistory(event_type="uploaded", status=DocumentStatus.QUEUED.value,
                                                      event_data={"sha256": digest, "size_bytes": size_bytes}))
            db.add(document)
            db.commit()
            db.refresh(document)
            db.refresh(job)

            started = datetime.now(timezone.utc)
            document.status = DocumentStatus.PROCESSING
            job.status = JobStatus.RUNNING
            job.started_at = started
            document.history.append(ProcessingHistory(event_type="processing_started", status=JobStatus.RUNNING.value))
            db.commit()

            last_progress = 0
            def progress(done: int, total: int | None, label: str | None) -> None:
                nonlocal last_progress
                if done == last_progress or (done % 10 != 0 and (total is None or done != total)):
                    return
                last_progress = done
                history = ProcessingHistory(
                    document_id=document.id, event_type="progress", status=JobStatus.RUNNING.value,
                    details=label, event_data={"completed": done, "total": total},
                )
                db.add(history)
                db.commit()

            output, actual_started, completed = self.processing.process(
                original_path, document_id=document.id, filename=original_filename, progress=progress,
            )
            normalized = {
                "source_document_id": document.id,
                "filename": original_filename,
                "format": output.format,
                "status": output.status,
                "page_count": output.page_count,
                "pages_processed": output.pages_processed,
                "worksheet_metadata": output.worksheet_metadata,
                "segments": output.segments,
                "metadata": output.metadata,
                "ocr_used": output.ocr_used,
                "warnings": output.warnings,
                "errors": output.errors,
                "processing_started_at": actual_started.isoformat(),
                "processing_completed_at": completed.isoformat(),
                "processing_duration_seconds": round((completed - actual_started).total_seconds(), 3),
                "sha256": digest,
                "rows_processed": sum(item.get("row_count", 0) for item in output.worksheet_metadata),
                "json_result_path": str(self.settings.processed_dir / f"{internal_id}.json"),
                "text_result_path": str(self.settings.processed_dir / f"{internal_id}.txt"),
                "is_duplicate": False,
            }
            invoice = extract_invoice(normalized, self.settings)
            from decimal import Decimal
            financial_errors = validate_invoice(
                {**invoice, "field_candidates": invoice.get("field_candidates", {})},
                rounding_tolerance=self.settings.invoice_rounding_tolerance,
                allow_negative_amounts=self.settings.allow_negative_invoice_amounts,
            )
            duplicate_invoice_ids = []
            if invoice.get("invoice_number") and invoice.get("supplier_name"):
                prior_results = db.scalars(select(InvoiceResult).where(
                    InvoiceResult.invoice_number.is_not(None), InvoiceResult.vendor_name.is_not(None)
                )).all()
                for prior in prior_results:
                    prior_data = {"invoice_number": prior.invoice_number, "supplier_name": prior.vendor_name}
                    if is_duplicate_invoice(invoice, prior_data):
                        duplicate_invoice_ids.append(prior.document_id)
                if duplicate_invoice_ids:
                    financial_errors.append({
                        "code": "duplicate_invoice", "field": "invoice_number", "severity": "warning",
                        "message": "The supplier and normalized invoice identifier match a previously processed invoice.",
                        "explanation": "Duplicate detection uses supplier identity plus invoice identifier; matching amounts alone are not considered duplicates.",
                        "expected_value": {"existing_document_ids": duplicate_invoice_ids},
                        "observed_value": {"supplier_name": invoice.get("supplier_name"), "invoice_number": invoice.get("invoice_number")},
                    })
            normalized["invoice"] = invoice
            normalized["invoice_validation"] = financial_errors
            normalized["duplicate_invoice_document_ids"] = duplicate_invoice_ids
            normalized["warnings"].extend(invoice.get("warnings", []))
            normalized["review_recommended"] = bool(invoice.get("warnings") or financial_errors)
            normalized = _json_safe(normalized)
            searchable_text = "\n".join([original_filename, *[segment.get("text", "") for segment in output.segments]])
            db.add(DocumentSearchIndex(document_id=document.id,
                                       searchable_text=searchable_text[:self.settings.search_index_max_chars]))
            json_path = self.settings.processed_dir / f"{internal_id}.json"
            text_path = Path(normalized["text_result_path"])
            text_output = "\n\n".join(
                f"[{segment.get('type')} {segment.get('page_number', segment.get('sheet_name', ''))}]\n{segment.get('text', '')}"
                for segment in output.segments
            )
            json_path = Path(normalized["json_result_path"])
            _write_atomic(json_path, json.dumps(normalized, ensure_ascii=False, indent=2, default=str))
            _write_atomic(text_path, text_output)
            db_invoice = InvoiceResult(
                document_id=document.id, extracted_text_path=str(text_path), raw_extraction=normalized,
                vendor_name=invoice.get("supplier_name"), customer_name=invoice.get("customer_name"),
                invoice_number=invoice.get("invoice_number"), invoice_date=invoice.get("invoice_date"),
                currency=invoice.get("currency"), subtotal=_decimal_or_none(invoice.get("subtotal")),
                tax_amount=_decimal_or_none(invoice.get("tax_amount")),
                total_amount=_decimal_or_none(invoice.get("total_amount")),
                extraction_method=invoice.get("extraction_method"), field_confidence=invoice.get("field_confidence"),
            )
            for position, item in enumerate(invoice.get("line_items", [])):
                db_invoice.line_items.append(LineItem(
                    description=item.get("description") or "Unspecified item",
                    quantity=_decimal_or_none(item.get("quantity")),
                    unit_price=_decimal_or_none(item.get("unit_price")),
                    amount=_decimal_or_none(item.get("line_total")), position=position,
                ))
            for error in financial_errors:
                db_invoice.validation_errors.append(ValidationError(
                    field_name=error["field"], code=error["code"], message=error["message"],
                    severity=error["severity"], explanation=error["explanation"],
                    expected_value=error["expected_value"], observed_value=error["observed_value"],
                ))
            db.add(db_invoice)
            document.status = DocumentStatus.COMPLETED
            document.error_details = json.dumps({"warnings": output.warnings, "errors": output.errors}, ensure_ascii=False) if (output.warnings or output.errors) else None
            job.status = JobStatus.COMPLETED
            job.completed_at = completed
            completed_details = (f"Processed {output.pages_processed} of {output.page_count} pages"
                                 if output.page_count is not None else
                                 f"Processed {len(output.worksheet_metadata)} worksheet(s), {normalized['rows_processed']} row(s)")
            document.history.append(ProcessingHistory(event_type="processing_completed", status=output.status,
                                                      details=completed_details,
                                                      event_data={"warnings": output.warnings, "errors": output.errors,
                                                                  "json_path": str(json_path)}))
            db.commit()
            return normalized
        except FileValidationError as exc:
            db.rollback()
            return self._failure(original_filename, extension, exc.code, str(exc), request_started)
        except Exception as exc:
            db.rollback()
            logger.exception("Document ingestion or extraction failed", extra={"document_id": getattr(document, "id", None)})
            if document is not None and job is not None:
                now = datetime.now(timezone.utc)
                document.status = DocumentStatus.FAILED
                document.error_details = f"{type(exc).__name__}: {exc}"
                job.status = JobStatus.FAILED
                job.error_details = document.error_details
                job.completed_at = now
                failed = {
                    "source_document_id": document.id, "filename": original_filename,
                    "format": extension.lstrip("."), "status": "failed", "page_count": None,
                    "pages_processed": 0, "worksheet_metadata": [], "segments": [], "metadata": {},
                    "ocr_used": False, "warnings": [],
                    "errors": [{"code": "processing_failed", "message": document.error_details}],
                    "processing_completed_at": now.isoformat(), "sha256": document.sha256,
                    "processing_started_at": (job.started_at or request_started).isoformat(),
                    "processing_duration_seconds": round((now - (job.started_at or request_started)).total_seconds(), 3),
                    "rows_processed": 0,
                }
                try:
                    result_path = self.settings.processed_dir / f"{Path(document.stored_filename).stem}.json"
                    text_path = self.settings.processed_dir / f"{Path(document.stored_filename).stem}.txt"
                    failed["json_result_path"] = str(result_path)
                    failed["text_result_path"] = str(text_path)
                    failed["is_duplicate"] = False
                    document.history.append(ProcessingHistory(event_type="processing_failed", status=JobStatus.FAILED.value,
                                                              details=document.error_details))
                    _write_atomic(result_path, json.dumps(failed, ensure_ascii=False, indent=2))
                    _write_atomic(text_path, "")
                    db.add(InvoiceResult(document_id=document.id, raw_extraction=failed,
                                         extracted_text_path=str(text_path)))
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Could not persist document processing failure", extra={"document_id": document.id})
                return failed
            return self._failure(original_filename, extension, "processing_failed", f"{type(exc).__name__}: {exc}", request_started)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _failure(filename: str, extension: str, code: str, message: str,
                 started_at: datetime | None = None) -> dict[str, Any]:
        completed_at = datetime.now(timezone.utc)
        started_at = started_at or completed_at
        return {
            "source_document_id": None, "filename": filename,
            "format": extension.lstrip("."), "status": "failed", "page_count": None,
            "pages_processed": 0, "worksheet_metadata": [], "segments": [], "metadata": {},
            "ocr_used": False, "warnings": [], "errors": [{"code": code, "message": message}],
            "is_duplicate": False,
            "processing_started_at": started_at.isoformat(),
            "processing_completed_at": completed_at.isoformat(),
            "processing_duration_seconds": round((completed_at - started_at).total_seconds(), 3),
            "rows_processed": 0,
        }
