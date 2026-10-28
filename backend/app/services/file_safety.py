import zipfile
from pathlib import Path


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"}
SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".tif": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".tiff": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}


class FileValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_file(path: Path, filename: str, size_bytes: int, max_bytes: int,
                  max_archive_uncompressed_bytes: int = 262_144_000,
                  max_image_pixels: int = 50_000_000) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise FileValidationError("unsupported_format", f"Unsupported file extension: {extension or '(none)'}")
    if size_bytes <= 0:
        raise FileValidationError("empty_file", "The uploaded file is empty")
    if size_bytes > max_bytes:
        raise FileValidationError("file_too_large", f"File exceeds the configured {max_bytes}-byte limit")
    with path.open("rb") as source:
        prefix = source.read(1024)
    expected = SIGNATURES.get(extension)
    if extension == ".pdf":
        if b"%PDF-" not in prefix:
            raise FileValidationError("invalid_signature", "File does not have a valid PDF signature")
    elif extension in {".xlsx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if sum(member.file_size for member in members) > max_archive_uncompressed_bytes:
                    raise FileValidationError("archive_too_large", "XLSX expands beyond the configured uncompressed-size limit")
                if any(member.file_size > 0 and member.compress_size == 0 for member in members):
                    raise FileValidationError("invalid_archive", "XLSX contains an invalid compressed member")
                names = {member.filename for member in members}
                if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                    raise FileValidationError("invalid_signature", "XLSX container is missing workbook entries")
        except zipfile.BadZipFile as exc:
            raise FileValidationError("invalid_signature", "XLSX file is not a valid ZIP workbook") from exc
    elif expected and not any(prefix.startswith(signature) for signature in expected):
        raise FileValidationError("invalid_signature", f"File contents do not match {extension}")
    elif extension == ".csv":
        # CSV has no magic number. Reject binary payloads but allow UTF-16 BOMs.
        if b"\x00" in prefix and not prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise FileValidationError("invalid_signature", "CSV content appears to be binary")
        if not prefix.strip():
            raise FileValidationError("empty_file", "CSV contains no data")
    if extension in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        try:
            from PIL import Image
            with Image.open(path) as image:
                if image.width * image.height > max_image_pixels:
                    raise FileValidationError("image_too_large", f"Image exceeds the configured {max_image_pixels}-pixel limit")
                actual = (image.format or "").lower()
                expected_formats = {
                    ".jpg": {"jpeg"}, ".jpeg": {"jpeg"}, ".png": {"png"},
                    ".tif": {"tiff"}, ".tiff": {"tiff"},
                }
                if actual not in expected_formats[extension]:
                    raise FileValidationError("invalid_signature", f"Image contents are {actual or 'unknown'}, not {extension}")
                image.verify()
        except FileValidationError:
            raise
        except Exception as exc:
            raise FileValidationError("corrupt_image", f"Image could not be opened: {exc}") from exc
    return extension
