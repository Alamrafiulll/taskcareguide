from datetime import datetime
from decimal import Decimal
from pydantic import Field, ConfigDict
from app.schemas.common import APIModel


class LineItemRead(APIModel):
    id: int
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    position: int


class ValidationErrorRead(APIModel):
    id: int
    field_name: str
    code: str
    message: str
    explanation: str | None = None
    expected_value: dict | list | str | None = None
    observed_value: dict | list | str | None = None
    severity: str
    created_at: datetime


class InvoiceResultRead(APIModel):
    id: int
    document_id: int
    vendor_name: str | None
    customer_name: str | None = None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    extracted_text_path: str | None
    raw_extraction: dict | None
    extraction_method: str | None = None
    field_confidence: dict | None = None
    created_at: datetime
    updated_at: datetime
    line_items: list[LineItemRead] = Field(default_factory=list)
    validation_errors: list[ValidationErrorRead] = Field(default_factory=list)


class ManualCorrectionCreate(APIModel):
    document_id: int = Field(gt=0)
    field_name: str = Field(min_length=1, max_length=127)
    previous_value: dict | None = None
    corrected_value: dict
    corrected_by: str | None = Field(default=None, max_length=255)


class ExtractedLineItem(APIModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    source_snippet: str | None = None


class InvoiceExtraction(APIModel):
    """Validated invoice shape used by deterministic and optional AI extraction."""
    model_config = ConfigDict(extra="forbid")
    invoice_number: str | None = None
    invoice_date: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    line_items: list[ExtractedLineItem] = Field(default_factory=list, max_length=2000)


class InvoiceCorrectionCreate(APIModel):
    field_name: str = Field(min_length=1, max_length=127)
    corrected_value: object
    corrected_by: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class ManualCorrectionRead(APIModel):
    id: int
    document_id: int
    field_name: str
    previous_value: dict | None
    corrected_value: dict
    corrected_by: str | None
    reason: str | None = None
    correction_source: str = "human"
    created_at: datetime


class InvoiceCorrectionSuggestion(APIModel):
    field: str
    current_value: object
    suggested_value: object
    evidence: list[str] = Field(default_factory=list)


class InvoiceCorrectionSuggestionsRead(APIModel):
    document_id: int
    status: str
    provider: str = "ollama"
    model: str
    suggestions: list[InvoiceCorrectionSuggestion] = Field(default_factory=list)


class InvoiceComparisonRequest(APIModel):
    document_a_id: int = Field(gt=0)
    document_b_id: int = Field(gt=0)


class InvoiceComparisonResponse(APIModel):
    comparable: bool
    review_recommended: bool
    matched_fields: list[dict]
    mismatched_fields: list[dict]
    missing_fields: list[dict]
    reasons: list[str] = Field(default_factory=list)
