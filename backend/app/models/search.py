from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentSearchIndex(Base):
    """Private local text index; never returned in API search responses."""
    __tablename__ = "document_search_index"
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
