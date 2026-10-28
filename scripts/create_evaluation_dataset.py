"""Create deterministic, synthetic local evaluation fixtures (no real invoices)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "dataset"


def invoice_lines(number: str | None, *, subtotal: str = "100.00", tax: str | None = "10.00",
                  total: str = "110.00", label: str = "Care visit", unit_price: str = "50.00",
                  line_total: str = "100.00", include_zero_line: bool = True) -> list[str]:
    lines = [
        f"Invoice Number: {number if number is not None else 'unreadable'}",
        "Invoice Date: 2026-05-10",
        "Supplier: North Clinic",
        "Customer: Care Guide",
        "Currency: USD",
        f"Subtotal: {subtotal}",
        *([f"Tax: {tax}"] if tax is not None else []),
        f"Total: {total}",
        "Description | Quantity | Unit Price | Line Total",
        f"{label} | 2 | {unit_price} | {line_total}",
    ]
    if include_zero_line:
        lines.append("Diagnostic kit | 1 | 0.00 | 0.00")
    return lines


def write_pdf(path: Path, lines: list[str], *, image_path: Path | None = None) -> None:
    import fitz

    document = fitz.open()
    if image_path:
        with Image.open(image_path) as image:
            width, height = image.size
        page = document.new_page(width=612, height=612 * height / width)
    else:
        page = document.new_page(width=612, height=792)
    if image_path:
        page.insert_image(page.rect, filename=str(image_path))
    else:
        for index, line in enumerate(lines):
            page.insert_text((54, 72 + index * 42), line, fontsize=12, fontname="helv")
    document.set_metadata({"title": "Synthetic Care Guide evaluation invoice", "author": "Care Guide test data"})
    document.save(path)
    document.close()


def write_csv(path: Path, pairs: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(pairs)


def create_uncertain_image(path: Path) -> None:
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 52) if font_path.is_file() else ImageFont.load_default()
    rows = invoice_lines(None, label="Diagnostic visit")
    for index, line in enumerate(rows):
        draw.text((90, 90 + index * 92), line, fill="#161616", font=font)
    # The literal marker is deliberate: it tests that extraction keeps this null.
    image.save(path, format="PNG", optimize=True)


def create_scanned_image(path: Path) -> None:
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 52) if font_path.is_file() else ImageFont.load_default()
    for index, line in enumerate(invoice_lines("CG-EVAL-1005", label="Scanned consultation")):
        draw.text((100, 110 + index * 105), line, fill="#151515", font=font)
    image.save(path, format="JPEG", quality=92, optimize=True)


def ground_truth() -> dict:
    fields = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax_amount", "total_amount", "currency", "line_items")
    valid = {"invoice_number": "CG-EVAL-1001", "invoice_date": "2026-05-10", "supplier_name": "North Clinic", "customer_name": "Care Guide", "subtotal": "100.00", "tax_amount": "10.00", "total_amount": "110.00", "currency": "USD", "line_items": [{"description": "Care visit", "quantity": "2", "unit_price": "50.00", "line_total": "100.00"}, {"description": "Diagnostic kit", "quantity": "1", "unit_price": "0.00", "line_total": "0.00"}]}
    missing_number = {**valid, "invoice_number": None}
    missing_tax = {**valid, "invoice_number": "CG-EVAL-1003", "tax_amount": None, "total_amount": "100.00"}
    bad_total = {**valid, "invoice_number": "CG-EVAL-1002", "subtotal": "120.00", "tax_amount": "12.00", "total_amount": "139.00", "line_items": [{"description": "Lab panel", "quantity": "2", "unit_price": "60.00", "line_total": "120.00"}]}
    unreadable = {**valid, "invoice_number": None}
    scanned = {**valid, "invoice_number": "CG-EVAL-1005"}
    return {
        "dataset_name": "Care Guide synthetic invoices v1",
        "fields": list(fields),
        "normalization": {"strings": "trim and casefold for exact-match scoring", "amounts": "compare as Decimal without tolerance", "date": "ISO YYYY-MM-DD"},
        "null_policy": "Explicit expected nulls are included in field accuracy and count as exact only when the pipeline returns null. unreadable_fields and skipped_fields are excluded from field accuracy and reported by name.",
        "documents": [
            {"file": "valid_invoice.pdf", "expected_invoice": valid, "expected_validation_codes": [], "unreadable_fields": [], "skipped_fields": []},
            {"file": "spreadsheet_valid.xlsx", "expected_invoice": valid, "expected_validation_codes": ["duplicate_invoice"], "expected_duplicate_of": "valid_invoice.pdf", "unreadable_fields": [], "skipped_fields": []},
            {"file": "missing_invoice_number.csv", "expected_invoice": missing_number, "expected_validation_codes": ["required_field_missing", "field_uncertain"], "unreadable_fields": [], "skipped_fields": []},
            {"file": "missing_tax.csv", "expected_invoice": missing_tax, "expected_validation_codes": ["field_missing"], "unreadable_fields": [], "skipped_fields": []},
            {"file": "incorrect_total.pdf", "expected_invoice": bad_total, "expected_validation_codes": ["subtotal_tax_total_mismatch"], "unreadable_fields": [], "skipped_fields": []},
            {"file": "duplicate_invoice.csv", "expected_invoice": valid, "expected_validation_codes": ["duplicate_invoice"], "expected_duplicate_of": "valid_invoice.pdf", "unreadable_fields": [], "skipped_fields": []},
            {"file": "uncertain_invoice.png", "expected_invoice": unreadable, "expected_validation_codes": ["required_field_missing", "field_uncertain"], "unreadable_fields": ["invoice_number"], "skipped_fields": [], "requires_ocr": True},
            {"file": "scanned_invoice.jpg", "expected_invoice": scanned, "expected_validation_codes": [], "unreadable_fields": [], "skipped_fields": [], "requires_ocr": True},
            {"file": "scanned_invoice.pdf", "expected_invoice": scanned, "expected_validation_codes": [], "unreadable_fields": [], "skipped_fields": [], "requires_ocr": True},
            {"file": "corrupted.pdf", "expected_invoice": {field: None for field in fields}, "expected_validation_codes": [], "unreadable_fields": [], "skipped_fields": list(fields), "expected_processing_failure": True},
        ],
    }


def main() -> None:
    DATASET.mkdir(parents=True, exist_ok=True)
    write_pdf(DATASET / "valid_invoice.pdf", invoice_lines("CG-EVAL-1001"))
    write_pdf(DATASET / "incorrect_total.pdf", invoice_lines("CG-EVAL-1002", subtotal="120.00", tax="12.00", total="139.00", label="Lab panel", unit_price="60.00", line_total="120.00", include_zero_line=False))
    line_rows = [[], ["Description", "Quantity", "Unit Price", "Line Total"], ["Care visit", "2", "50.00", "100.00"], ["Diagnostic kit", "1", "0.00", "0.00"]]
    write_csv(DATASET / "missing_invoice_number.csv", [("Invoice Number", "unreadable"), ("Invoice Date", "2026-05-10"), ("Supplier", "North Clinic"), ("Customer", "Care Guide"), ("Currency", "USD"), ("Subtotal", "100.00"), ("Tax", "10.00"), ("Total", "110.00"), *line_rows])
    write_csv(DATASET / "duplicate_invoice.csv", [("Invoice Number", "CG-EVAL-1001"), ("Invoice Date", "2026-05-10"), ("Supplier", "North Clinic"), ("Customer", "Care Guide"), ("Currency", "USD"), ("Subtotal", "100.00"), ("Tax", "10.00"), ("Total", "110.00"), ("Fixture note", "A separate CSV export"), *line_rows])
    write_csv(DATASET / "missing_tax.csv", [("Invoice Number", "CG-EVAL-1003"), ("Invoice Date", "2026-05-10"), ("Supplier", "North Clinic"), ("Customer", "Care Guide"), ("Currency", "USD"), ("Subtotal", "100.00"), ("Total", "100.00"), *line_rows])
    image_path = DATASET / "uncertain_invoice.png"
    create_uncertain_image(image_path)
    scanned_image = DATASET / "scanned_invoice.jpg"
    create_scanned_image(scanned_image)
    write_pdf(DATASET / "scanned_invoice.pdf", invoice_lines("CG-EVAL-1005"), image_path=scanned_image)
    (DATASET / "corrupted.pdf").write_bytes(b"%PDF-1.7\nSynthetic corrupt fixture; not a valid PDF object structure.")
    # This XLSX is authored with the local artifact-tool workbook builder and is retained as a static synthetic fixture.
    if not (DATASET / "spreadsheet_valid.xlsx").is_file():
        raise FileNotFoundError("spreadsheet_valid.xlsx fixture is missing")
    with (DATASET / "ground_truth.json").open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(ground_truth(), stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(f"Created synthetic evaluation dataset at {DATASET}")


if __name__ == "__main__":
    main()
