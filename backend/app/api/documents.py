import logging

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas.ingestion import IngestionBatchResponse
from app.services.ingestion import DocumentIngestionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


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
