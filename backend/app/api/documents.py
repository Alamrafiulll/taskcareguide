import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.document import Document
from app.models.history import ProcessingHistory
from app.models.invoice import InvoiceResult, ManualCorrection, ValidationError
from app.schemas.ingestion import IngestionBatchResponse
from app.schemas.invoice import InvoiceComparisonRequest, InvoiceComparisonResponse, InvoiceCorrectionCreate, InvoiceResultRead, ManualCorrectionRead
from app.services.invoice_comparison import compare_invoices
from app.services.financial_validation import validate_invoice
from app.services.ingestion import DocumentIngestionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])
INVOICE_FIELDS = {
    "invoice_number": "invoice_number", "invoice_date": "invoice_date", "supplier_name": "vendor_name",
    "vendor_name": "vendor_name", "customer_name": "customer_name", "subtotal": "subtotal",
    "tax_amount": "tax_amount", "total_amount": "total_amount", "currency": "currency",
}


@router.post("/ingest", response_model=IngestionBatchResponse)
def ingest_documents(files: list[UploadFile] = File(...), db: Session = Depends(get_db)) -> IngestionBatchResponse:
    """Upload and synchronously extract each file, isolating errors per item."""
    settings = get_settings()
    service = DocumentIngestionService(settings)
    results = []
    for index, upload in enumerate(files):
        if index >= settings.max_batch_files:
            filename = upload.filename or "document"
            results.append({
                "source_document_id": None, "filename": filename, "format": "",
                "status": "failed", "page_count": None, "pages_processed": 0,
                "worksheet_metadata": [], "segments": [], "metadata": {}, "ocr_used": False,
                "warnings": [], "errors": [{"code": "batch_file_limit", "message": f"Batch is limited to {settings.max_batch_files} files."}],
                "is_duplicate": False,
            })
            upload.file.close()
            continue
        try:
            results.append(service.ingest(upload, db))
        except Exception as exc:
            logger.exception("Unexpected failure while handling uploaded file", extra={"filename": upload.filename})
            results.append({
                "source_document_id": None, "filename": upload.filename or "document", "format": "",
                "status": "failed", "page_count": None, "pages_processed": 0,
                "worksheet_metadata": [], "segments": [], "metadata": {}, "ocr_used": False,
                "warnings": [], "errors": [{"code": "ingestion_failed", "message": str(exc)}],
                "is_duplicate": False,
            })
        finally:
            upload.file.close()
    return IngestionBatchResponse(
        total_files=len(results),
        processed_files=sum(item.get("status") in {"completed", "completed_with_errors", "partial"} for item in results),
        failed_files=sum(item.get("status") == "failed" for item in results),
        duplicate_files=sum(item.get("status") == "duplicate" for item in results),
        results=results,
    )


def _safe_invoice_payload(result: InvoiceResult) -> InvoiceResultRead:
    payload = InvoiceResultRead.model_validate(result)
    if payload.raw_extraction:
        payload.raw_extraction = {key: value for key, value in payload.raw_extraction.items()
                                  if key not in {"json_result_path", "text_result_path"}}
    return payload


@router.get("/{document_id}/invoice", response_model=InvoiceResultRead)
def read_invoice(document_id: int, db: Session = Depends(get_db)) -> InvoiceResultRead:
    result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found", "message": "No invoice extraction exists for this document."})
    return _safe_invoice_payload(result)


@router.post("/{document_id}/invoice/corrections", response_model=InvoiceResultRead)
def correct_invoice(document_id: int, correction: InvoiceCorrectionCreate, db: Session = Depends(get_db)) -> InvoiceResult:
    column = INVOICE_FIELDS.get(correction.field_name)
    if column is None:
        raise HTTPException(status_code=422, detail={"code": "unsupported_correction_field", "message": "Corrections currently support invoice header fields."})
    result = db.scalar(select(InvoiceResult).where(InvoiceResult.document_id == document_id))
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found", "message": "No invoice extraction exists for this document."})
    current = getattr(result, column)
    previous = {"value": str(current) if current is not None else None}
    new_value = correction.corrected_value
    if column in {"subtotal", "tax_amount", "total_amount"}:
        try:
            new_value = Decimal(str(new_value)) if new_value is not None else None
        except InvalidOperation as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_correction", "message": "Monetary corrections must be exact decimal values."}) from exc
    elif new_value is not None:
        new_value = str(new_value).strip()
    setattr(result, column, new_value)
    result.raw_extraction = dict(result.raw_extraction or {})
    current_invoice = dict(result.raw_extraction.get("invoice") or {})
    result.raw_extraction.setdefault("original_invoice", current_invoice.copy())
    invoice = current_invoice
    canonical = "supplier_name" if column == "vendor_name" else column
    invoice[canonical] = str(new_value) if new_value is not None else None
    statuses = dict(invoice.get("field_status") or {})
    statuses[canonical] = "human_corrected"
    invoice["field_status"] = statuses
    result.raw_extraction["invoice"] = invoice
    checked = validate_invoice(invoice, rounding_tolerance=get_settings().invoice_rounding_tolerance,
                               allow_negative_amounts=get_settings().allow_negative_invoice_amounts)
    result.validation_errors.clear()
    for finding in checked:
        result.validation_errors.append(ValidationError(
            field_name=finding["field"], code=finding["code"], message=finding["message"],
            severity=finding["severity"], explanation=finding["explanation"],
            expected_value=finding["expected_value"], observed_value=finding["observed_value"],
        ))
    db.add(ManualCorrection(document_id=document_id, field_name=correction.field_name,
                            previous_value=previous, corrected_value={"value": str(new_value) if new_value is not None else None},
                            corrected_by=correction.corrected_by, reason=correction.reason))
    db.add(ProcessingHistory(document_id=document_id, event_type="invoice_field_corrected", status="reviewed",
                             details=f"Manually corrected {correction.field_name}.",
                             event_data={"field": correction.field_name}))
    db.commit()
    db.refresh(result)
    return _safe_invoice_payload(result)


@router.get("/{document_id}/invoice/corrections", response_model=list[ManualCorrectionRead])
def read_invoice_corrections(document_id: int, db: Session = Depends(get_db)) -> list[ManualCorrection]:
    if db.scalar(select(Document.id).where(Document.id == document_id)) is None:
        raise HTTPException(status_code=404, detail={"code": "document_not_found", "message": "Document was not found."})
    return list(db.scalars(select(ManualCorrection).where(ManualCorrection.document_id == document_id)
                           .order_by(ManualCorrection.created_at, ManualCorrection.id)).all())


@router.post("/compare", response_model=InvoiceComparisonResponse)
def compare_documents(request: InvoiceComparisonRequest, db: Session = Depends(get_db)) -> dict:
    if request.document_a_id == request.document_b_id:
        raise HTTPException(status_code=422, detail={"code": "comparison_requires_two_documents", "message": "Select two different documents."})
    results = db.scalars(select(InvoiceResult).where(InvoiceResult.document_id.in_([request.document_a_id, request.document_b_id]))).all()
    by_document = {result.document_id: result for result in results}
    missing = [doc_id for doc_id in (request.document_a_id, request.document_b_id) if doc_id not in by_document]
    if missing:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found", "message": f"No invoice extraction exists for document(s): {missing}."})
    def invoice_data(result: InvoiceResult) -> dict:
        raw = result.raw_extraction or {}
        data = dict(raw.get("invoice") or {})
        # Prefer the stored, potentially human-corrected values.
        data.update({"invoice_number": result.invoice_number, "invoice_date": result.invoice_date,
                     "supplier_name": result.vendor_name, "customer_name": result.customer_name,
                     "subtotal": str(result.subtotal) if result.subtotal is not None else None,
                     "tax_amount": str(result.tax_amount) if result.tax_amount is not None else None,
                     "total_amount": str(result.total_amount) if result.total_amount is not None else None,
                     "currency": result.currency})
        return data
    return compare_invoices(invoice_data(by_document[request.document_a_id]), invoice_data(by_document[request.document_b_id]))
