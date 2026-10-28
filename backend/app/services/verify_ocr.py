from app.config import get_settings
import sys

from app.services.processors.ocr import OCRService, OCRUnavailableError


def main() -> None:
    try:
        version = OCRService(get_settings()).verify()
    except OCRUnavailableError as exc:
        print(f"Tesseract is unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Tesseract is available: {version}")


if __name__ == "__main__":
    main()
