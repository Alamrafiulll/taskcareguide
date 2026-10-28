from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


class OCRUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float | None


class OCRService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pytesseract_module = None

    def _pytesseract(self):
        if self._pytesseract_module is not None:
            return self._pytesseract_module
        try:
            import pytesseract
        except ImportError as exc:
            raise OCRUnavailableError("pytesseract is not installed") from exc
        if self.settings.tesseract_cmd:
            executable = Path(self.settings.tesseract_cmd)
            if not executable.is_file():
                raise OCRUnavailableError(f"Configured Tesseract executable was not found: {executable}")
            pytesseract.pytesseract.tesseract_cmd = str(executable)
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:
            raise OCRUnavailableError(
                "Tesseract OCR executable is unavailable. Install Tesseract and configure TESSERACT_CMD if needed."
            ) from exc
        self._pytesseract_module = pytesseract
        return pytesseract

    def verify(self) -> str:
        pytesseract = self._pytesseract()
        return str(pytesseract.get_tesseract_version())

    def image_to_text(self, image) -> OCRResult:
        pytesseract = self._pytesseract()
        from PIL import Image, ImageOps

        gray = ImageOps.grayscale(image)
        thresholded = None
        try:
            # Bound OCR memory/latency while keeping enough resolution for document text.
            max_edge = 4000
            if max(gray.size) > max_edge:
                scale = max_edge / max(gray.size)
                resized = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.Resampling.LANCZOS)
                gray.close()
                gray = resized
            contrasted = ImageOps.autocontrast(gray)
            gray.close()
            gray = contrasted
            # Threshold clean scans; retain grayscale when thresholding would discard fine detail.
            thresholded = gray.point(lambda pixel: 255 if pixel > 180 else 0)
            image_for_ocr = thresholded if gray.width * gray.height < 30_000_000 else gray
            data = pytesseract.image_to_data(
                image_for_ocr,
                lang=self.settings.ocr_language,
                output_type=pytesseract.Output.DICT,
                timeout=self.settings.ocr_timeout_seconds,
                config="--psm 6",
            )
            confidences = []
            for value in data.get("conf", []):
                try:
                    score = float(value)
                except (TypeError, ValueError):
                    continue
                if score >= 0:
                    confidences.append(score)
            lines = {}
            for index, word in enumerate(data.get("text", [])):
                if not word or not word.strip():
                    continue
                key = tuple(data.get(field, [index] * len(data.get("text", [])))[index]
                            for field in ("block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(word.strip())
            text = "\n".join(" ".join(line_words) for line_words in lines.values()).strip()
            confidence = sum(confidences) / len(confidences) if confidences else None
            return OCRResult(text=text, confidence=confidence)
        finally:
            if thresholded is not None:
                thresholded.close()
            gray.close()
