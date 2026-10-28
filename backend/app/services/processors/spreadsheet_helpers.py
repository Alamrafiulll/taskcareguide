import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def normalized_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        # Keep numeric spreadsheet values JSON-safe without binary-float amounts.
        return format(Decimal(str(value)), "f")
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def column_name(zero_based: int) -> str:
    name = ""
    number = zero_based + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def tabular_segment(sheet_name: str, rows: list[list[Any]], *, truncated: bool = False) -> dict[str, Any]:
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = []
    text_lines = []
    for row_number, row in enumerate(rows, start=1):
        values = [normalized_cell(value) for value in row]
        values.extend([None] * (max_columns - len(values)))
        cells = []
        for column_index, value in enumerate(values):
            if value is not None and str(value).strip():
                coordinate = f"{column_name(column_index)}{row_number}"
                cells.append({"coordinate": coordinate, "value": value})
                text_lines.append(f"{coordinate}: {value}")
        normalized_rows.append({"row_number": row_number, "values": values, "cells": cells})
    return {
        "type": "sheet", "sheet_name": sheet_name, "text": "\n".join(text_lines),
        "table": {"row_count": len(rows), "column_count": max_columns, "rows": normalized_rows},
        "truncated": truncated,
    }
