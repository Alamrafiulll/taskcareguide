from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.schemas.common import HealthResponse, ReadinessResponse
from app.models.worker import WorkerHeartbeat

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get("/ready", response_model=ReadinessResponse)
def readiness(db: Session = Depends(get_db)) -> ReadinessResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "database_unavailable", "message": "Database is unavailable"}) from exc
    settings = get_settings()
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=settings.worker_stale_seconds)
    active_worker = db.scalar(select(WorkerHeartbeat.worker_id).where(
        WorkerHeartbeat.state.in_(["idle", "running"]), WorkerHeartbeat.last_seen_at >= stale_before).limit(1))
    worker_status = "ok" if active_worker else "not_running"
    tesseract_path = settings.tesseract_cmd
    ocr_status = "available" if ((tesseract_path and tesseract_path.is_file()) or shutil.which("tesseract")) else "optional_not_installed"
    ollama_status = "enabled" if settings.ollama_enabled and settings.ollama_model else "disabled"
    return ReadinessResponse(status="ready" if active_worker else "degraded",
                             checks={"database": "ok", "worker": worker_status,
                                     "ocr": ocr_status, "local_ai": ollama_status})
