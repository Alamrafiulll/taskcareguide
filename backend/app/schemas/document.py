from datetime import datetime
from pydantic import Field
from app.models.enums import DocumentStatus
from app.schemas.common import APIModel


class DocumentCreate(APIModel):
    original_filename: str = Field(min_length=1, max_length=255)
    stored_filename: str = Field(min_length=1, max_length=255)
    original_path: str = Field(min_length=1)
    file_size_bytes: int = Field(ge=0)
    mime_type: str | None = Field(default=None, max_length=127)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class DocumentRead(DocumentCreate):
    id: int
    status: DocumentStatus
    error_details: str | None
    created_at: datetime
    updated_at: datetime
