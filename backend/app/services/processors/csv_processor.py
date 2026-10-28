import csv
import io
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.services.processors.base import ProcessingOutput, ProgressCallback, check_deadline, notify_progress
from app.services.processors.spreadsheet_helpers import tabular_segment


class CSVProcessor:
    extensions = frozenset({".csv"})
    encodings = ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1")

    def __init__(self, settings: Settings):
        self.settings = settings

    def process(self, path: Path, *, document_id: int, filename: str, started_at: datetime,
                deadline: float, progress: ProgressCallback | None = None) -> ProcessingOutput:
        output = ProcessingOutput(format="csv")
        decoded = None
        used_encoding = None
        for encoding in self.encodings:
            try:
                decoded = path.read_text(encoding=encoding)
                used_encoding = encoding
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            raise ValueError("CSV encoding could not be decoded")
        if "\x00" in decoded:
            raise ValueError("CSV contains binary null bytes")
        if not decoded.strip():
            raise ValueError("CSV contains no data")
        sample = decoded[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
            output.warnings.append({"code": "csv_dialect_defaulted", "message": "Could not infer delimiter; treated as comma separated."})
        rows = []
        expected_width = None
        csv.field_size_limit(self.settings.max_upload_bytes)
        try:
            reader = csv.reader(io.StringIO(decoded, newline=""), dialect=dialect, strict=True)
            for row_number, row in enumerate(reader, start=1):
                check_deadline(deadline)
                if row_number > self.settings.max_csv_rows:
                    output.warnings.append({"code": "csv_row_limit_reached", "limit": self.settings.max_csv_rows})
                    break
                if expected_width is None and row:
                    expected_width = len(row)
                if row and expected_width is not None and len(row) != expected_width:
                    output.warnings.append({"code": "inconsistent_column_count", "row": row_number,
                                            "expected": expected_width, "actual": len(row)})
                # Blank rows are kept as empty arrays so following row numbers remain aligned.
                rows.append(row)
                if row_number % 1000 == 0:
                    notify_progress(progress, row_number, None, f"Row {row_number}")
        except csv.Error as exc:
            raise ValueError(f"Malformed CSV near line {reader.line_num}: {exc}") from exc
        segment = tabular_segment("CSV", rows, truncated=len(rows) >= self.settings.max_csv_rows)
        output.segments.append(segment)
        output.worksheet_metadata.append({"name": "CSV", "row_count": len(rows),
                                          "column_count": segment["table"]["column_count"],
                                          "encoding": used_encoding, "delimiter": getattr(dialect, "delimiter", ",")})
        output.metadata = {"encoding": used_encoding, "delimiter": getattr(dialect, "delimiter", ",")}
        notify_progress(progress, len(rows), len(rows), "CSV rows")
        return output
