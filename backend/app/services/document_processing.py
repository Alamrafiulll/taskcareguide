from datetime import datetime, timezone
from pathlib import Path
import time

from app.config import Settings, get_settings
from app.services.file_safety import SUPPORTED_EXTENSIONS, FileValidationError, validate_file
from app.services.processors import CSVProcessor, ExcelProcessor, ImageProcessor, PDFProcessor
from app.services.processors.base import DocumentProcessor, ProcessingOutput, ProgressCallback
from app.services.processors.ocr import OCRService


class DocumentProcessingService:
    """Select and run a format processor without coupling callers to a format."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.ocr = OCRService(self.settings)
        self.processors: list[DocumentProcessor] = [
            PDFProcessor(self.settings, self.ocr),
            ImageProcessor(self.settings, self.ocr),
            ExcelProcessor(self.settings),
            CSVProcessor(self.settings),
        ]

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(SUPPORTED_EXTENSIONS)

    def validate(self, path: Path, filename: str, size_bytes: int) -> str:
        return validate_file(path, filename, size_bytes, self.settings.max_upload_bytes,
                             self.settings.max_archive_uncompressed_bytes, self.settings.max_image_pixels)

    def process(self, path: Path, *, document_id: int, filename: str,
                progress: ProgressCallback | None = None) -> tuple[ProcessingOutput, datetime, datetime]:
        extension = Path(filename).suffix.lower()
        processor = next((item for item in self.processors if extension in item.extensions), None)
        if processor is None:
            raise FileValidationError("unsupported_format", f"No processor is registered for {extension}")
        started_at = datetime.now(timezone.utc)
        deadline = time.monotonic() + max(1, self.settings.processing_timeout_seconds)
        output = processor.process(path, document_id=document_id, filename=filename, started_at=started_at,
                                   deadline=deadline, progress=progress)
        completed_at = datetime.now(timezone.utc)
        if output.errors and output.status == "completed":
            output.status = "completed_with_errors"
        return output, started_at, completed_at
