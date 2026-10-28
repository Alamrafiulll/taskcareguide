from datetime import datetime
from pydantic import Field
from app.models.enums import JobStatus
from app.schemas.common import APIModel


class ProcessingJobRead(APIModel):
    id: int
    document_id: int
    status: JobStatus
    retry_count: int = Field(ge=0)
    error_details: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
