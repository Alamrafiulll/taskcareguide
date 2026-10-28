from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.services.processors.base import ProcessingOutput, ProgressCallback, check_deadline, notify_progress
from app.services.processors.ocr import OCRService


class ImageProcessor:
    extensions = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff"})

    def __init__(self, settings: Settings, ocr: OCRService):
        self.settings = settings
        self.ocr = ocr

    def process(self, path: Path, *, document_id: int, filename: str, started_at: datetime,
                deadline: float, progress: ProgressCallback | None = None) -> ProcessingOutput:
        from PIL import Image

        output = ProcessingOutput(format=path.suffix.lower().lstrip("."))
        with Image.open(path) as image:
            total = getattr(image, "n_frames", 1)
            if image.width * image.height > self.settings.max_image_pixels:
                raise ValueError(f"Image exceeds the configured {self.settings.max_image_pixels}-pixel limit")
            output.page_count = total
            output.metadata = {key: str(value) for key, value in image.info.items() if isinstance(value, (str, int, float))}
            limit = max(1, self.settings.max_pdf_pages)
            frame_count = min(total, limit)
            if total > limit:
                output.status = "partial"
                output.warnings.append({"code": "page_limit_reached", "message": f"Processed {limit} of {total} image frames.", "limit": limit, "total_pages": total})
            for frame_index in range(frame_count):
                check_deadline(deadline)
                image.seek(frame_index)
                frame_number = frame_index + 1
                try:
                    with image.copy() as frame:
                        ocr_result = self.ocr.image_to_text(frame)
                    output.ocr_used = True
                    output.segments.append({"type": "page", "page_number": frame_number, "text": ocr_result.text,
                                            "ocr_used": True, "ocr_confidence": ocr_result.confidence, "errors": []})
                except Exception as exc:
                    detail = {"code": "image_ocr_failed", "page": frame_number, "message": str(exc)}
                    output.errors.append(detail)
                    output.segments.append({"type": "page", "page_number": frame_number, "text": "",
                                            "ocr_used": True, "ocr_confidence": None, "errors": [detail]})
                output.pages_processed += 1
                notify_progress(progress, output.pages_processed, total, f"Frame {frame_number}")
        return output
