from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models.enums import DocumentStatus, JobStatus
from app.schemas.common import APIModel


class DocumentUploadItem(APIModel):
    document_id: int | None = None
    filename: str
    status: str
    job_id: int | None = None
    sha256: str | None = None
    error: dict | None = None
    duplicate_of_document_id: int | None = None


class DocumentUploadResponse(APIModel):
    accepted_count: int
    rejected_count: int
    items: list[DocumentUploadItem]


class DocumentJobSummary(APIModel):
    id: int
    status: JobStatus
    retry_count: int
    attempt_count: int
    error_details: str | None
    permanent_failure: bool
    started_at: datetime | None
    completed_at: datetime | None


class DocumentSummary(APIModel):
    id: int
    filename: str
    mime_type: str | None
    file_size_bytes: int
    sha256: str | None
    status: DocumentStatus
    error_details: str | None
    created_at: datetime
    updated_at: datetime
    job: DocumentJobSummary | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    currency: str | None = None
    invoice_date: str | None = None
    validation_status: str | None = None
    validation_error_count: int = 0
    correction_status: str = "not_needed"


class DocumentPage(APIModel):
    total: int
    page: int
    page_size: int
    items: list[DocumentSummary]


class DocumentMetadata(DocumentSummary):
    original_extension: str
    result_available: bool


class DocumentHistoryItem(APIModel):
    id: int
    event_type: str
    status: str | None
    details: str | None
    event_data: dict | None
    created_at: datetime


class InvoiceEditLineItem(APIModel):
    description: str = Field(min_length=1, max_length=2000)
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class InvoiceEditRequest(APIModel):
    reason: str | None = Field(default=None, max_length=2000)
    corrected_by: str | None = Field(default=None, max_length=255)
    correction_source: Literal["human", "local_model", "ai_model"] = "human"
    correction_model: str | None = Field(default=None, max_length=127)
    invoice_number: str | None = None
    invoice_date: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    line_items: list[InvoiceEditLineItem] | None = Field(default=None, max_length=2000)


class RetryResponse(APIModel):
    document_id: int
    job_id: int
    status: JobStatus
    retry_count: int


class SearchResult(APIModel):
    document: DocumentSummary
    score: float | None = None
    match_type: str


class SearchResponse(APIModel):
    query: str
    mode: Literal["keyword", "semantic", "hybrid"]
    total: int
    results: list[SearchResult]
    semantic_available: bool

