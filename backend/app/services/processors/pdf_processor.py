from datetime import datetime
from pathlib import Path
import io

from app.config import Settings
from app.services.processors.base import ProcessingOutput, ProgressCallback, check_deadline, notify_progress
from app.services.processors.ocr import OCRService


class PDFProcessor:
    extensions = frozenset({".pdf"})

    def __init__(self, settings: Settings, ocr: OCRService):
        self.settings = settings
        self.ocr = ocr

    def process(self, path: Path, *, document_id: int, filename: str, started_at: datetime,
                deadline: float, progress: ProgressCallback | None = None) -> ProcessingOutput:
        try:
            import fitz
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is not installed. Install the pinned backend requirements.") from exc

        output = ProcessingOutput(format="pdf")
        document = fitz.open(path)
        try:
            if document.needs_pass and not document.authenticate(""):
                raise ValueError("PDF is encrypted and requires a password")
            total = document.page_count
            if total < 1:
                raise ValueError("PDF contains no pages")
            output.page_count = total
            output.metadata = {key: value for key, value in (document.metadata or {}).items() if value}
            limit = max(1, self.settings.max_pdf_pages)
            process_count = min(total, limit)
            if total > limit:
                output.status = "partial"
                output.warnings.append({"code": "page_limit_reached", "message": f"Processed {limit} of {total} pages (configured limit).", "limit": limit, "total_pages": total})

            for index in range(process_count):
                check_deadline(deadline)
                page_number = index + 1
                page = document.load_page(index)
                text = page.get_text("text") or ""
                used_ocr = len("".join(text.split())) < max(0, self.settings.min_pdf_text_chars)
                confidence = None
                page_errors = []
                if used_ocr:
                    output.ocr_used = True
                    try:
                        dpi = max(72, min(self.settings.ocr_dpi, 400))
                        page_rect = page.rect
                        target_scale = dpi / 72
                        source_pixels = max(1.0, page_rect.width * page_rect.height)
                        max_scale = (self.settings.max_image_pixels / source_pixels) ** 0.5
                        scale = min(target_scale, max_scale)
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        with Image.open(io.BytesIO(pixmap.tobytes("png"))) as rendered:
                            ocr_result = self.ocr.image_to_text(rendered)
                        text = ocr_result.text
                        confidence = ocr_result.confidence
                    except Exception as exc:
                        detail = {"code": "page_ocr_failed", "page": page_number, "message": str(exc)}
                        page_errors.append(detail)
                        output.errors.append(detail)
                output.segments.append({
                    "type": "page", "page_number": page_number, "text": text,
                    "ocr_used": used_ocr, "ocr_confidence": confidence,
                    "errors": page_errors,
                })
                output.pages_processed += 1
                notify_progress(progress, output.pages_processed, total, f"Page {page_number}")
        finally:
            document.close()
        return output


