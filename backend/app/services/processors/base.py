from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol


ProgressCallback = Callable[[int, int | None, str | None], None]


@dataclass
class ProcessingOutput:
    format: str
    status: str = "completed"
    page_count: int | None = None
    pages_processed: int = 0
    worksheet_metadata: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ocr_used: bool = False
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class DocumentProcessor(Protocol):
    extensions: frozenset[str]

    def process(
        self,
        path: Path,
        *,
        document_id: int,
        filename: str,
        started_at: datetime,
        deadline: float,
        progress: ProgressCallback | None = None,
    ) -> ProcessingOutput: ...


def check_deadline(deadline: float) -> None:
    import time

    if time.monotonic() > deadline:
        raise TimeoutError("Document processing exceeded the configured time limit")


def notify_progress(callback: ProgressCallback | None, done: int, total: int | None, label: str | None = None) -> None:
    if callback:
        callback(done, total, label)
