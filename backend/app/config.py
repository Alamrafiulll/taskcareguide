from functools import lru_cache
from decimal import Decimal
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
    ollama_timeout_seconds: int = Field(default=20, gt=0, le=120)
    invoice_rounding_tolerance: Decimal = Field(default=Decimal("0.02"), ge=0)
    allow_negative_invoice_amounts: bool = False
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=900, gt=0)
    worker_stale_seconds: int = Field(default=60, gt=0)
    worker_max_attempts: int = Field(default=3, gt=0, le=10)
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    semantic_max_documents: int = Field(default=5000, gt=0, le=50000)
    search_index_max_chars: int = Field(default=1_000_000, gt=1000)

    model_config = SettingsConfigDict(env_file=BACKEND_DIR.parent / ".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
