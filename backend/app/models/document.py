from datetime import datetime

from sqlalchemy import Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import DocumentStatus
from app.models.types import UTCDateTime


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(127))
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus, native_enum=False, length=32), default=DocumentStatus.UPLOADED, nullable=False, index=True)
    error_details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False)
    jobs = relationship("ProcessingJob", back_populates="document", cascade="all, delete-orphan")
    invoice_result = relationship("InvoiceResult", back_populates="document", cascade="all, delete-orphan", uselist=False)
    history = relationship("ProcessingHistory", back_populates="document", cascade="all, delete-orphan")
    corrections = relationship("ManualCorrection", back_populates="document", cascade="all, delete-orphan")
