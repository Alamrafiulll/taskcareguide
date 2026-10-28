import pytest
from pydantic import ValidationError
from app.schemas.document import DocumentCreate
from app.schemas.invoice import ManualCorrectionCreate


def test_document_schema_accepts_valid_metadata():
    data = DocumentCreate(original_filename="invoice.pdf", stored_filename="safe-id.pdf", original_path="data/uploads/safe-id.pdf", file_size_bytes=128)
    assert data.file_size_bytes == 128


def test_document_schema_rejects_negative_size():
    with pytest.raises(ValidationError):
        DocumentCreate(original_filename="invoice.pdf", stored_filename="safe-id.pdf", original_path="data/uploads/safe-id.pdf", file_size_bytes=-1)


def test_manual_correction_requires_value():
    with pytest.raises(ValidationError):
        ManualCorrectionCreate(document_id=1, field_name="total_amount")
