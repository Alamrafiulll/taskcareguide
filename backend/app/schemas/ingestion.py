from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExtractedSegment(BaseModel):
    type: str
    page_number: int | None = None
    sheet_name: str | None = None
    text: str = ""
    ocr_used: bool = False
    ocr_confidence: float | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    table: dict[str, Any] | None = None
    truncated: bool = False


class DocumentProcessingResult(BaseModel):
    source_document_id: int | None = None
    filename: str
    format: str
    status: str
    page_count: int | None = None
    pages_processed: int = 0
    rows_processed: int = 0
    worksheet_metadata: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[ExtractedSegment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ocr_used: bool = False
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    processing_duration_seconds: float | None = None
    sha256: str | None = None
    json_result_path: str | None = None
    text_result_path: str | None = None
    is_duplicate: bool = False
    duplicate_of_document_id: int | None = None


class IngestionBatchResponse(BaseModel):
    total_files: int
    processed_files: int
    failed_files: int
    duplicate_files: int
    results: list[DocumentProcessingResult]
