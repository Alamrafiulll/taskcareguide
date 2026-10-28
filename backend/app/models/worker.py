from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import UTCDateTime


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    current_job_id: Mapped[int | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
