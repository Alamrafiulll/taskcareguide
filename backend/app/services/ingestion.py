import hashlib
import json
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.history import ProcessingHistory
from app.models.invoice import InvoiceResult
from app.models.processing import ProcessingJob
from app.services.document_processing import DocumentProcessingService
from app.services.file_safety import FileValidationError

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


class DocumentIngestionService:
    def __init__(self, settings: Settings | None = None, processing: DocumentProcessingService | None = None):
        self.settings = settings or get_settings()
        self.processing = processing or DocumentProcessingService(self.settings)

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
            normalized = _json_safe(normalized)
            json_path = self.settings.processed_dir / f"{internal_id}.json"
            text_path = Path(normalized["text_result_path"])
            text_output = "\n\n".join(
                f"[{segment.get('type')} {segment.get('page_number', segment.get('sheet_name', ''))}]\n{segment.get('text', '')}"
                for segment in output.segments
            )
            json_path = Path(normalized["json_result_path"])
            _write_atomic(json_path, json.dumps(normalized, ensure_ascii=False, indent=2, default=str))
            _write_atomic(text_path, text_output)
            db.add(InvoiceResult(document_id=document.id, extracted_text_path=str(text_path), raw_extraction=normalized))
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
