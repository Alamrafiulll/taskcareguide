"""Persistent SQLite-backed worker for queued document processing."""
from __future__ import annotations

import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import SessionLocal, init_db
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.history import ProcessingHistory
from app.models.processing import ProcessingJob
from app.models.worker import WorkerHeartbeat
from app.services.file_safety import FileValidationError
from app.services.ingestion import DocumentIngestionService

logger = logging.getLogger(__name__)


class DocumentWorker:
    def __init__(self, settings: Settings | None = None, session_factory: sessionmaker = SessionLocal,
                 processor: DocumentIngestionService | None = None, worker_id: str | None = None):
        self.settings = settings or get_settings()
        self.session_factory = session_factory
        self.processor = processor or DocumentIngestionService(self.settings)
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:12]}"

    def _heartbeat(self, state: str, current_job_id: int | None = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as db:
            beat = db.get(WorkerHeartbeat, self.worker_id)
            if beat is None:
                beat = WorkerHeartbeat(worker_id=self.worker_id, state=state, last_seen_at=now)
                db.add(beat)
            beat.state = state
            beat.current_job_id = current_job_id
            beat.last_seen_at = now
            beat.last_error = error[:500] if error else None
            db.commit()

    def _claim(self) -> tuple[int, int] | None:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.settings.worker_stale_seconds)
        with self.session_factory() as db:
            beats = select(WorkerHeartbeat.worker_id).where(
                WorkerHeartbeat.state == "running", WorkerHeartbeat.last_seen_at >= stale_before)
            abandoned = or_(
                ProcessingJob.lease_until.is_(None), ProcessingJob.lease_until < now,
                ~ProcessingJob.worker_id.in_(beats),
            )
            candidate = db.scalar(select(ProcessingJob.id).where(
                or_(and_(ProcessingJob.status == JobStatus.QUEUED,
                         or_(ProcessingJob.next_attempt_at.is_(None), ProcessingJob.next_attempt_at <= now)),
                    and_(ProcessingJob.status == JobStatus.RUNNING, abandoned)),
                ProcessingJob.attempt_count < self.settings.worker_max_attempts,
            ).order_by(ProcessingJob.created_at, ProcessingJob.id).limit(1))
            if candidate is None:
                self._fail_exhausted(db, now, stale_before)
                db.commit()
                return None
            claim = db.execute(update(ProcessingJob).where(
                ProcessingJob.id == candidate,
                or_(and_(ProcessingJob.status == JobStatus.QUEUED,
                         or_(ProcessingJob.next_attempt_at.is_(None), ProcessingJob.next_attempt_at <= now)),
                    and_(ProcessingJob.status == JobStatus.RUNNING, abandoned)),
            ).values(status=JobStatus.RUNNING, attempt_count=ProcessingJob.attempt_count + 1,
                      worker_id=self.worker_id, lease_until=now + timedelta(seconds=self.settings.worker_lease_seconds),
                      next_attempt_at=None, started_at=now, completed_at=None, error_details=None)
                .execution_options(synchronize_session=False))
            if claim.rowcount != 1:
                db.rollback()
                return None
            job = db.get(ProcessingJob, candidate)
            document = db.get(Document, job.document_id)
            document.status = DocumentStatus.PROCESSING
            document.error_details = None
            document.history.append(ProcessingHistory(document_id=document.id,
                event_type="processing_reclaimed" if job.attempt_count > 1 else "processing_started",
                status=JobStatus.RUNNING.value, event_data={"job_id": job.id, "attempt": job.attempt_count,
                                                             "worker_id": self.worker_id}))
            beat = db.get(WorkerHeartbeat, self.worker_id)
            if beat is None:
                beat = WorkerHeartbeat(worker_id=self.worker_id, state="running", last_seen_at=now)
                db.add(beat)
            beat.state, beat.current_job_id, beat.last_seen_at, beat.last_error = "running", job.id, now, None
            db.commit()
            return document.id, job.id

    def _fail_exhausted(self, db: Session, now: datetime, stale_before: datetime) -> None:
        jobs = db.scalars(select(ProcessingJob).where(
            ProcessingJob.attempt_count >= self.settings.worker_max_attempts,
            or_(ProcessingJob.status == JobStatus.QUEUED,
                and_(ProcessingJob.status == JobStatus.RUNNING,
                     or_(ProcessingJob.lease_until < now, ProcessingJob.lease_until.is_(None)))),
        )).all()
        for job in jobs:
            job.status = JobStatus.FAILED
            job.error_details = "Maximum automatic processing attempts reached."
            job.completed_at = now
            job.lease_until = None
            job.permanent_failure = False
            doc = db.get(Document, job.document_id)
            doc.status = DocumentStatus.FAILED
            doc.error_details = job.error_details
            doc.history.append(ProcessingHistory(document_id=doc.id, event_type="processing_failed",
                status=JobStatus.FAILED.value, details=job.error_details,
                event_data={"job_id": job.id, "attempts": job.attempt_count}))

    def run_once(self) -> bool:
        self._heartbeat("idle")
        claimed = self._claim()
        if claimed is None:
            return False
        document_id, job_id = claimed
        self._heartbeat("running", job_id)
        heartbeat_stop = Event()

        def keep_alive() -> None:
            interval = max(1.0, self.settings.worker_stale_seconds / 3)
            while not heartbeat_stop.wait(interval):
                try:
                    self._heartbeat("running", job_id)
                except Exception:
                    logger.exception("Could not refresh worker heartbeat", extra={"job_id": job_id})

        heartbeat_thread = Thread(target=keep_alive, name=f"heartbeat-{job_id}", daemon=True)
        heartbeat_thread.start()
        try:
            with self.session_factory() as db:
                self.processor.process_existing(document_id, db)
            self._heartbeat("idle")
        except FileValidationError as exc:
            self._finish_failure(job_id, f"{exc.code}: {exc}", permanent=True)
        except Exception as exc:
            logger.exception("Background document processing failed", extra={"document_id": document_id, "job_id": job_id})
            message = f"{type(exc).__name__}: {exc}"
            for sensitive_path in (str(self.settings.upload_dir), str(self.settings.processed_dir)):
                message = message.replace(sensitive_path, "[local storage]")
            self._finish_failure(job_id, message, permanent=False)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
        return True

    def _finish_failure(self, job_id: int, message: str, *, permanent: bool) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as db:
            job = db.get(ProcessingJob, job_id)
            if job is None:
                return
            doc = db.get(Document, job.document_id)
            job.error_details = message[:4000]
            job.lease_until = None
            job.permanent_failure = permanent
            if permanent or job.attempt_count >= self.settings.worker_max_attempts:
                job.status = JobStatus.FAILED
                job.completed_at = now
                doc.status = DocumentStatus.FAILED
                doc.error_details = job.error_details
                event_type = "processing_failed"
            else:
                delay = min(2 ** max(job.attempt_count, 1), 60)
                job.status = JobStatus.QUEUED
                job.next_attempt_at = now + timedelta(seconds=delay)
                doc.status = DocumentStatus.QUEUED
                doc.error_details = job.error_details
                event_type = "processing_retry_scheduled"
            job.worker_id = None
            doc.history.append(ProcessingHistory(document_id=doc.id, event_type=event_type,
                status=job.status.value, details=job.error_details,
                event_data={"job_id": job.id, "attempt": job.attempt_count, "permanent": permanent}))
            db.commit()
        self._heartbeat("idle", error=message)

    def run_forever(self, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        init_db()
        self._heartbeat("idle")
        logger.info("Document worker started", extra={"worker_id": self.worker_id})
        try:
            while not stop_event.is_set():
                did_work = self.run_once()
                if not did_work:
                    stop_event.wait(self.settings.worker_poll_interval_seconds)
        finally:
            self._heartbeat("stopped")
            logger.info("Document worker stopped", extra={"worker_id": self.worker_id})
