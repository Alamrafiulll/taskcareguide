from datetime import datetime
from decimal import Decimal
from pydantic import Field
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
    severity: str
    created_at: datetime


class InvoiceResultRead(APIModel):
    id: int
    document_id: int
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    extracted_text_path: str | None
    raw_extraction: dict | None
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
