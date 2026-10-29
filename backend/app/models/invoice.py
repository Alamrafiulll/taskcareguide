from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.types import UTCDateTime


class InvoiceResult(Base):
    __tablename__ = "invoice_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    invoice_number: Mapped[str | None] = mapped_column(String(127))
    invoice_date: Mapped[str | None] = mapped_column(String(32))
    due_date: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    extracted_text_path: Mapped[str | None] = mapped_column(Text)
    raw_extraction: Mapped[dict | None] = mapped_column(JSON)
    extraction_method: Mapped[str | None] = mapped_column(String(32))
    field_confidence: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False)
    document = relationship("Document", back_populates="invoice_result")
    line_items = relationship("LineItem", back_populates="invoice_result", cascade="all, delete-orphan")
    validation_errors = relationship("ValidationError", back_populates="invoice_result", cascade="all, delete-orphan")


class LineItem(Base):
    __tablename__ = "line_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_result_id: Mapped[int] = mapped_column(ForeignKey("invoice_results.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3, asdecimal=True))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2, asdecimal=True))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invoice_result = relationship("InvoiceResult", back_populates="line_items")


class ValidationError(Base):
    __tablename__ = "validation_errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_result_id: Mapped[int] = mapped_column(ForeignKey("invoice_results.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(127), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    expected_value: Mapped[dict | None] = mapped_column(JSON)
    observed_value: Mapped[dict | None] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String(16), default="warning", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    invoice_result = relationship("InvoiceResult", back_populates="validation_errors")


class ManualCorrection(Base):
    __tablename__ = "manual_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(127), nullable=False)
    previous_value: Mapped[dict | None] = mapped_column(JSON)
    corrected_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    corrected_by: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    correction_source: Mapped[str] = mapped_column(String(24), default="human", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    document = relationship("Document", back_populates="corrections")
