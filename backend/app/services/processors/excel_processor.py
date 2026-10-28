from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.services.processors.base import ProcessingOutput, ProgressCallback, check_deadline, notify_progress
from app.services.processors.spreadsheet_helpers import tabular_segment


class ExcelProcessor:
    extensions = frozenset({".xlsx", ".xls"})

    def __init__(self, settings: Settings):
        self.settings = settings

    def process(self, path: Path, *, document_id: int, filename: str, started_at: datetime,
                deadline: float, progress: ProgressCallback | None = None) -> ProcessingOutput:
        import pandas as pd

        output = ProcessingOutput(format=path.suffix.lower().lstrip("."))
        engine = "openpyxl" if path.suffix.lower() == ".xlsx" else "xlrd"
        engine_kwargs = {"read_only": True, "data_only": False} if engine == "openpyxl" else None
        with pd.ExcelFile(path, engine=engine, engine_kwargs=engine_kwargs) as workbook:
            output.metadata = {"sheet_names": workbook.sheet_names}
            sheet_count = len(workbook.sheet_names)
            for sheet_index, sheet_name in enumerate(workbook.sheet_names, start=1):
                check_deadline(deadline)
                table = workbook.parse(sheet_name=sheet_name, header=None, dtype=object,
                                        nrows=self.settings.max_spreadsheet_rows + 1)
                truncated = len(table.index) > self.settings.max_spreadsheet_rows
                if truncated:
                    table = table.iloc[:self.settings.max_spreadsheet_rows]
                    output.warnings.append({"code": "sheet_row_limit_reached", "sheet": sheet_name,
                                            "limit": self.settings.max_spreadsheet_rows})
                rows = table.values.tolist()
                segment = tabular_segment(sheet_name, rows, truncated=truncated)
                output.segments.append(segment)
                output.worksheet_metadata.append({"name": sheet_name, "row_count": len(rows),
                                                  "column_count": segment["table"]["column_count"],
                                                  "truncated": truncated})
                notify_progress(progress, sheet_index, sheet_count, f"Sheet {sheet_name}")
        return output
