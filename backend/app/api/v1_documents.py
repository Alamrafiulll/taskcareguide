from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.history import ProcessingHistory
from app.models.invoice import InvoiceResult, LineItem, ManualCorrection, ValidationError
from app.models.processing import ProcessingJob
from app.models.search import DocumentSearchIndex
from app.models.worker import WorkerHeartbeat
from app.schemas.documents import (DocumentHistoryItem, DocumentMetadata, DocumentPage, DocumentSummary,
                                   DocumentUploadItem, DocumentUploadResponse, InvoiceEditRequest,
                                   RetryResponse)
from app.schemas.invoice import InvoiceCorrectionSuggestionsRead, InvoiceResultRead
from app.services.financial_validation import validate_invoice
from app.services.invoice_extraction import suggest_invoice_corrections
from app.services.ingestion import DocumentIngestionService
from app.services.semantic_search import EmbeddingUnavailable, semantic_rank

router = APIRouter(prefix="/api/documents", tags=["documents-v1"])


def _latest_job_subquery():
    return (select(ProcessingJob.document_id.label("document_id"),
                   func.max(ProcessingJob.id).label("job_id"))
            .group_by(ProcessingJob.document_id).subquery())


def _base_query():
    latest = _latest_job_subquery()
    counts = select(ValidationError.invoice_result_id.label("invoice_id"),
                    func.count(ValidationError.id).label("error_count")).group_by(ValidationError.invoice_result_id).subquery()
    return (select(Document, InvoiceResult, ProcessingJob, func.coalesce(counts.c.error_count, 0))
            .outerjoin(InvoiceResult, InvoiceResult.document_id == Document.id)
            .outerjoin(latest, latest.c.document_id == Document.id)
            .outerjoin(ProcessingJob, latest.c.job_id == ProcessingJob.id)
            .outerjoin(counts, counts.c.invoice_id == InvoiceResult.id))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered_query(*, keyword: str | None = None, status_filter: str | None = None,
                    invoice_number: str | None = None, supplier: str | None = None,
                    customer: str | None = None, currency: str | None = None,
                    date_from: date | None = None, date_to: date | None = None,
                    validation_status: str | None = None, correction_status: str | None = None):
    query = _base_query()
    if status_filter:
        query = query.where(Document.status == status_filter)
    if invoice_number:
        query = query.where(InvoiceResult.invoice_number.ilike(f"%{_escape_like(invoice_number)}%", escape="\\"))
    if supplier:
        query = query.where(InvoiceResult.vendor_name.ilike(f"%{_escape_like(supplier)}%", escape="\\"))
    if customer:
        query = query.where(InvoiceResult.customer_name.ilike(f"%{_escape_like(customer)}%", escape="\\"))
    if currency:
        query = query.where(InvoiceResult.currency.ilike(_escape_like(currency), escape="\\"))
    if date_from:
        query = query.where(InvoiceResult.invoice_date >= date_from.isoformat())
    if date_to:
        query = query.where(InvoiceResult.invoice_date <= date_to.isoformat())
    if validation_status == "has_errors":
        query = query.where(select(func.count(ValidationError.id)).where(ValidationError.invoice_result_id == InvoiceResult.id).correlate(InvoiceResult).scalar_subquery() > 0)
    elif validation_status == "valid":
        query = query.where(InvoiceResult.id.is_not(None),
                            select(func.count(ValidationError.id)).where(ValidationError.invoice_result_id == InvoiceResult.id).correlate(InvoiceResult).scalar_subquery() == 0)
    elif validation_status == "pending":
        query = query.where(InvoiceResult.id.is_(None))
    elif validation_status:
        raise HTTPException(status_code=422, detail={"code": "invalid_validation_status", "message": "Use has_errors, valid, or pending."})
    ai_corrected = select(ManualCorrection.id).where(
        ManualCorrection.document_id == Document.id,
        ManualCorrection.correction_source == "local_model").correlate(Document).exists()
    human_corrected = select(ManualCorrection.id).where(
        ManualCorrection.document_id == Document.id,
        ManualCorrection.correction_source == "human").correlate(Document).exists()
    any_findings = select(func.count(ValidationError.id)).where(
        ValidationError.invoice_result_id == InvoiceResult.id).correlate(InvoiceResult).scalar_subquery()
    if correction_status == "needs_review":
        query = query.where(InvoiceResult.id.is_not(None), any_findings > 0)
    elif correction_status == "ai_corrected":
        query = query.where(ai_corrected)
    elif correction_status == "human_corrected":
        query = query.where(human_corrected)
    elif correction_status == "not_needed":
        query = query.where(InvoiceResult.id.is_not(None), any_findings == 0,
                            ~ai_corrected, ~human_corrected)
    elif correction_status:
        raise HTTPException(status_code=422, detail={"code": "invalid_correction_status",
            "message": "Use needs_review, ai_corrected, human_corrected, or not_needed."})
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        query = query.outerjoin(DocumentSearchIndex, DocumentSearchIndex.document_id == Document.id).where(or_(
            Document.original_filename.ilike(pattern, escape="\\"),
            DocumentSearchIndex.searchable_text.ilike(pattern, escape="\\"),
            InvoiceResult.invoice_number.ilike(pattern, escape="\\"),
            InvoiceResult.vendor_name.ilike(pattern, escape="\\"),
            InvoiceResult.customer_name.ilike(pattern, escape="\\"),
            InvoiceResult.currency.ilike(pattern, escape="\\"),
            InvoiceResult.invoice_date.ilike(pattern, escape="\\"),
        ))
    return query


def _summary(document: Document, invoice: InvoiceResult | None, job: ProcessingJob | None,
             error_count: int) -> DocumentSummary:
    return DocumentSummary(
        id=document.id, filename=document.original_filename, mime_type=document.mime_type,
        file_size_bytes=document.file_size_bytes, sha256=document.sha256, status=document.status,
        error_details=document.error_details, created_at=document.created_at, updated_at=document.updated_at,
        job=job, invoice_number=invoice.invoice_number if invoice else None,
        supplier_name=invoice.vendor_name if invoice else None, customer_name=invoice.customer_name if invoice else None,
        currency=invoice.currency if invoice else None, invoice_date=invoice.invoice_date if invoice else None,
        validation_status=("has_errors" if error_count else "valid") if invoice else "pending",
        validation_error_count=error_count,
        correction_status=("ai_corrected" if invoice and any(
            item.correction_source == "local_model" for item in document.corrections) else
            "human_corrected" if invoice and document.corrections else
            "needs_review" if invoice and error_count else "not_needed" if invoice else "pending"),
    )


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_documents(files: list[UploadFile] = File(...), db: Session = Depends(get_db)) -> DocumentUploadResponse:
    settings = get_settings()
    service = DocumentIngestionService(settings)
    items = []
    for index, upload in enumerate(files):
        try:
            if index >= settings.max_batch_files:
                items.append(DocumentUploadItem(filename=upload.filename or "document", status="rejected",
                    error={"code": "batch_file_limit", "message": f"Batch is limited to {settings.max_batch_files} files."}))
                continue
            items.append(DocumentUploadItem.model_validate(service.enqueue(upload, db)))
        finally:
            upload.file.close()
    accepted = sum(item.status == "queued" for item in items)
    rejected = sum(item.status == "rejected" for item in items)
    if files and accepted == 0 and rejected:
        raise HTTPException(status_code=422, detail={"code": "no_files_accepted", "message": "No uploaded file passed validation.",
                                                     "details": [item.model_dump(mode="json") for item in items]})
    return DocumentUploadResponse(accepted_count=accepted, rejected_count=rejected, items=items)


@router.get("", response_model=DocumentPage)
def list_documents(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                   status_filter: str | None = Query(default=None, alias="status"),
                   invoice_number: str | None = None, supplier: str | None = None,
                   customer: str | None = None, currency: str | None = None,
                   date_from: date | None = None, date_to: date | None = None,
                   validation_status: str | None = None, correction_status: str | None = None,
                   keyword: str | None = None,
                   db: Session = Depends(get_db)) -> DocumentPage:
    query = _filtered_query(keyword=keyword, status_filter=status_filter, invoice_number=invoice_number,
        supplier=supplier, customer=customer, currency=currency, date_from=date_from,
        date_to=date_to, validation_status=validation_status, correction_status=correction_status)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.execute(query.order_by(Document.created_at.desc(), Document.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return DocumentPage(total=total, page=page, page_size=page_size,
                        items=[_summary(doc, invoice, job, count) for doc, invoice, job, count in rows])


@router.get("/{document_id}", response_model=DocumentMetadata)
def document_metadata(document_id: int, db: Session = Depends(get_db)) -> DocumentMetadata:
    rows = db.execute(_base_query().where(Document.id == document_id)).first()
    if rows is None:
        raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
    document, invoice, job, count = rows
    summary = _summary(document, invoice, job, count)
    return DocumentMetadata(**summary.model_dump(), original_extension=Path(document.original_filename).suffix.lower(),
                            result_available=invoice is not None)


@router.get("/{document_id}/status", response_model=DocumentSummary)
def document_status(document_id: int, db: Session = Depends(get_db)) -> DocumentSummary:
    return document_metadata(document_id, db)


@router.get("/{document_id}/history", response_model=list[DocumentHistoryItem])
def document_history(document_id: int, db: Session = Depends(get_db)) -> list[ProcessingHistory]:
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
    return list(db.scalars(select(ProcessingHistory).where(ProcessingHistory.document_id == document_id)
                           .order_by(ProcessingHistory.created_at, ProcessingHistory.id)).all())


@router.get("/{document_id}/validation-errors")
def validation_errors(document_id: int, db: Session = Depends(get_db)) -> list[dict]:
    result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if result is None:
        if db.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
        return []
    return [{"id": item.id, "field": item.field_name, "code": item.code, "severity": item.severity,
             "message": item.message, "explanation": item.explanation,
             "expected_value": item.expected_value, "observed_value": item.observed_value,
             "created_at": item.created_at.isoformat()} for item in result.validation_errors]


@router.get("/{document_id}/result")
def download_result(document_id: int, view: Literal["original", "corrected"] = Query(default="corrected"),
                    db: Session = Depends(get_db)) -> Response:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
    invoice = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if invoice is None:
        raise HTTPException(status_code=409, detail={"code": "result_not_ready", "message": "No generated result is available yet."})
    stored = document.stored_filename
    if Path(stored).name != stored or stored.startswith("."):
        raise HTTPException(status_code=500, detail={"code": "invalid_storage_name", "message": "Generated result cannot be resolved safely."})
    root = get_settings().processed_dir.resolve()
    result_path = (root / f"{Path(stored).stem}.json").resolve()
    if result_path.parent != root or not result_path.is_file():
        raise HTTPException(status_code=404, detail={"code": "result_not_found", "message": "Generated JSON result was not found."})
    try:
        normalized = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail={"code": "result_not_found", "message": "Generated JSON result could not be read."}) from exc
    if view == "corrected":
        raw = invoice.raw_extraction or {}
        current = dict(normalized.get("invoice") or {})
        current.update(raw.get("invoice") or {})
        current.update({
            "invoice_number": invoice.invoice_number, "invoice_date": invoice.invoice_date,
            "supplier_name": invoice.vendor_name, "customer_name": invoice.customer_name,
            "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
            "tax_amount": str(invoice.tax_amount) if invoice.tax_amount is not None else None,
            "total_amount": str(invoice.total_amount) if invoice.total_amount is not None else None,
            "currency": invoice.currency,
        })
        raw_items = current.get("line_items") or []
        current["line_items"] = [
            {**(raw_items[index] if index < len(raw_items) else {}),
             "description": item.description, "quantity": str(item.quantity) if item.quantity is not None else None,
             "unit_price": str(item.unit_price) if item.unit_price is not None else None,
             "line_total": str(item.amount) if item.amount is not None else None}
            for index, item in enumerate(sorted(invoice.line_items, key=lambda value: value.position))]
        normalized["invoice"] = current
        normalized["invoice_validation"] = [{
            "field": item.field_name, "code": item.code, "severity": item.severity,
            "message": item.message, "explanation": item.explanation,
            "expected_value": item.expected_value, "observed_value": item.observed_value,
        } for item in invoice.validation_errors]
        corrections = list(db.scalars(select(ManualCorrection).where(
            ManualCorrection.document_id == document_id).order_by(ManualCorrection.created_at, ManualCorrection.id)).all())
        normalized["correction_status"] = "ai_corrected" if any(
            item.correction_source == "local_model" for item in corrections) else (
            "human_corrected" if corrections else "not_corrected")
        normalized["correction_history"] = [{
            "field_name": item.field_name, "previous_value": item.previous_value,
            "corrected_value": item.corrected_value, "corrected_by": item.corrected_by,
            "correction_source": item.correction_source, "reason": item.reason,
            "created_at": item.created_at.isoformat(),
        } for item in corrections]
        if raw.get("model_correction"):
            normalized["model_correction"] = raw["model_correction"]
    filename = f"document-{document_id}-{view}-result.json"
    return Response(content=json.dumps(normalized, ensure_ascii=False, indent=2, default=str),
                    media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{document_id}/invoice/ai-suggestions", response_model=InvoiceCorrectionSuggestionsRead)
def invoice_ai_suggestions(document_id: int, db: Session = Depends(get_db)) -> InvoiceCorrectionSuggestionsRead:
    settings = get_settings()
    if not settings.ollama_enabled or not settings.ollama_model:
        raise HTTPException(status_code=503, detail={"code": "local_ai_disabled",
            "message": "Local AI suggestions are disabled. Configure OLLAMA_ENABLED and OLLAMA_MODEL to use Ollama."})
    result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found", "message": "No invoice extraction exists for this document."})
    raw = result.raw_extraction or {}
    proposals = suggest_invoice_corrections(raw, raw.get("invoice") or {}, settings)
    if proposals is None:
        raise HTTPException(status_code=503, detail={"code": "local_ai_unavailable",
            "message": "Ollama did not return valid, evidence-grounded invoice JSON. Existing extracted values were not changed."})
    return InvoiceCorrectionSuggestionsRead(document_id=document_id,
        status="suggestions_ready" if proposals["suggestions"] else "no_changes",
        model=proposals["model"], suggestions=proposals["suggestions"])


@router.post("/{document_id}/retry", response_model=RetryResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_failed_document(document_id: int, db: Session = Depends(get_db)) -> RetryResponse:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == document_id)
                    .order_by(ProcessingJob.id.desc()).limit(1))
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Processing job was not found."})
    if job.status != JobStatus.FAILED:
        raise HTTPException(status_code=409, detail={"code": "job_not_failed", "message": "Only failed jobs can be retried."})
    if job.permanent_failure:
        raise HTTPException(status_code=422, detail={"code": "permanent_file_error", "message": "This file failed permanent validation and cannot be retried without replacing the file."})
    settings = get_settings()
    if job.retry_count >= settings.worker_max_attempts:
        raise HTTPException(status_code=409, detail={"code": "retry_limit_reached", "message": "The configured manual retry limit has been reached."})
    source = settings.upload_dir / document.stored_filename
    try:
        DocumentIngestionService(settings).processing.validate(source, document.original_filename, document.file_size_bytes)
    except Exception:
        job.permanent_failure = True
        job.error_details = "Stored source file is missing or no longer passes validation."
        db.commit()
        raise HTTPException(status_code=422, detail={"code": "source_not_retryable", "message": job.error_details})
    job.status = JobStatus.QUEUED
    job.retry_count += 1
    job.attempt_count = 0
    job.next_attempt_at = datetime.now(timezone.utc)
    job.worker_id = None
    job.lease_until = None
    job.completed_at = None
    job.error_details = None
    document.status = DocumentStatus.QUEUED
    document.error_details = None
    document.history.append(ProcessingHistory(document_id=document.id, event_type="retry_requested",
        status=JobStatus.QUEUED.value, event_data={"job_id": job.id, "retry_count": job.retry_count}))
    db.commit()
    return RetryResponse(document_id=document.id, job_id=job.id, status=job.status, retry_count=job.retry_count)


@router.put("/{document_id}/invoice", response_model=InvoiceResultRead)
def edit_invoice(document_id: int, request: InvoiceEditRequest, db: Session = Depends(get_db)) -> InvoiceResultRead:
    result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found", "message": "No invoice extraction exists for this document."})
    fields = request.model_fields_set - {"reason", "corrected_by", "correction_source", "correction_model", "line_items"}
    if not fields and "line_items" not in request.model_fields_set:
        raise HTTPException(status_code=422, detail={"code": "empty_correction", "message": "Provide at least one invoice field or line_items."})
    raw = dict(result.raw_extraction or {})
    raw_invoice = dict(raw.get("invoice") or {})
    raw.setdefault("original_invoice", raw_invoice.copy())
    audit_entries = []
    mapping = {"supplier_name": "vendor_name"}
    for field in fields:
        column = mapping.get(field, field)
        previous = getattr(result, column)
        corrected = getattr(request, field)
        if corrected is not None and isinstance(corrected, str):
            corrected = corrected.strip()
        setattr(result, column, corrected)
        raw_invoice[field] = str(corrected) if isinstance(corrected, Decimal) else corrected
        audit_entries.append((field, {"value": str(previous) if previous is not None else None},
                              {"value": str(corrected) if corrected is not None else None}))
        statuses = dict(raw_invoice.get("field_status") or {})
        statuses[field] = "human_corrected"
        raw_invoice["field_status"] = statuses
    if "line_items" in request.model_fields_set:
        before = [{"description": item.description, "quantity": str(item.quantity) if item.quantity is not None else None,
                   "unit_price": str(item.unit_price) if item.unit_price is not None else None,
                   "line_total": str(item.amount) if item.amount is not None else None} for item in result.line_items]
        proposed = request.line_items or []
        after = [item.model_dump(mode="json") for item in proposed]
        audit_entries.append(("line_items", {"value": before}, {"value": after}))
        result.line_items.clear()
        for position, item in enumerate(proposed):
            result.line_items.append(LineItem(description=item.description, quantity=item.quantity,
                unit_price=item.unit_price, amount=item.line_total, position=position))
        raw_invoice["line_items"] = after
    raw["invoice"] = raw_invoice
    if audit_entries:
        if request.correction_source == "local_model":
            raw["model_correction"] = {
                "status": "accepted", "model": request.correction_model or get_settings().ollama_model,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "fields": [field for field, *_ in audit_entries],
            }
        raw["correction_status"] = {
            "source": request.correction_source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    data = {**raw_invoice, "supplier_name": raw_invoice.get("supplier_name") or result.vendor_name,
            "line_items": raw_invoice.get("line_items", [])}
    findings = validate_invoice(data, rounding_tolerance=get_settings().invoice_rounding_tolerance,
                                allow_negative_amounts=get_settings().allow_negative_invoice_amounts)
    result.validation_errors.clear()
    for item in findings:
        result.validation_errors.append(ValidationError(field_name=item["field"], code=item["code"], message=item["message"],
            severity=item["severity"], explanation=item["explanation"], expected_value=item["expected_value"],
            observed_value=item["observed_value"]))
    result.raw_extraction = raw
    for field, previous, corrected in audit_entries:
        db.add(ManualCorrection(document_id=document_id, field_name=field, previous_value=previous,
            corrected_value=corrected, corrected_by=request.corrected_by, reason=request.reason,
            correction_source=request.correction_source))
    event_type = "ai_suggestions_accepted" if request.correction_source == "local_model" else "invoice_corrected"
    db.add(ProcessingHistory(document_id=document_id, event_type=event_type, status="reviewed",
        details=request.reason or "Invoice fields were corrected.",
        event_data={"fields": sorted(field for field, *_ in audit_entries),
                    "correction_source": request.correction_source,
                    "model": request.correction_model if request.correction_source == "local_model" else None}))
    db.commit()
    db.refresh(result)
    payload = InvoiceResultRead.model_validate(result)
    payload.raw_extraction = {key: value for key, value in (payload.raw_extraction or {}).items()
                              if key not in {"json_result_path", "text_result_path"}}
    return payload
