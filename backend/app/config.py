from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "Care Guide Document Intelligence API"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./backend/data/careguide.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    upload_dir: Path = BACKEND_DIR / "data" / "uploads"
    processed_dir: Path = BACKEND_DIR / "data" / "processed"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_batch_files: int = Field(default=20, gt=0)
    max_pdf_pages: int = Field(default=1000, gt=0)
    min_pdf_text_chars: int = Field(default=24, ge=0)
    max_spreadsheet_rows: int = Field(default=100_000, gt=0)
    max_csv_rows: int = Field(default=250_000, gt=0)
    max_archive_uncompressed_bytes: int = Field(default=262_144_000, gt=0)
    max_image_pixels: int = Field(default=50_000_000, gt=0)
    processing_timeout_seconds: int = Field(default=600, gt=0)
    ocr_timeout_seconds: int = Field(default=30, gt=0)
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    ocr_language: str = "eng"
    tesseract_cmd: Path | None = None
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR.parent / ".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
