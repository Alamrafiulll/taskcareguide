"""Render a reviewed invoice result as a downloadable PDF summary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def render_reviewed_invoice_pdf(data: dict[str, Any]) -> bytes:
    """Create a new PDF from persisted invoice values; never alters the upload."""
    import fitz

    pdf = fitz.open()
    page = pdf.new_page(width=612, height=792)
    y = 56.0
    left, right = 54.0, 558.0

    def new_page() -> None:
        nonlocal page, y
        page = pdf.new_page(width=612, height=792)
        y = 56.0

    def pdf_text(value: Any) -> str:
        text = "" if value is None else str(value)
        text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        return text.encode("cp1252", errors="replace").decode("cp1252")

    def paragraph(value: Any, *, size: float = 11, bold: bool = False, gap: float = 7) -> None:
        nonlocal y
        text = pdf_text(value)
        if not text:
            text = "Not found"
        font = "hebo" if bold else "helv"
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and fitz.get_text_length(candidate, fontname=font, fontsize=size) > right - left:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        for line in lines:
            if y > 735:
                new_page()
            page.insert_text((left, y), line, fontname=font, fontsize=size, color=(0.12, 0.25, 0.19))
            y += size * 1.55
        y += gap

    paragraph("Care Guide - Reviewed invoice", size=18, bold=True, gap=3)
    paragraph(f"Source document: {data.get('filename') or 'Unknown'}", size=9)
    paragraph(f"Generated: {datetime.now(timezone.utc).isoformat()}", size=9, gap=15)

    for label, key in (
        ("Invoice number", "invoice_number"), ("Invoice date", "invoice_date"),
        ("Supplier", "supplier_name"), ("Customer", "customer_name"),
        ("Currency", "currency"), ("Subtotal", "subtotal"),
        ("Tax amount", "tax_amount"), ("Total amount", "total_amount"),
    ):
        value = data.get(key)
        if key in {"subtotal", "tax_amount", "total_amount"} and value not in (None, ""):
            value = f"{data.get('currency') or ''} {value}".strip()
        paragraph(f"{label}: {value if value not in (None, '') else 'Not found'}")

    paragraph("Line items", size=13, bold=True, gap=4)
    items = data.get("line_items") or []
    if not items:
        paragraph("No line items were extracted.")
    for index, item in enumerate(items, start=1):
        quantity = item.get("quantity")
        unit_price = item.get("unit_price")
        line_total = item.get("line_total", item.get("amount"))
        paragraph(f"{index}. {item.get('description') or 'Unspecified item'}")
        paragraph(f"   Quantity: {quantity if quantity is not None else 'Not found'}"
                  f" | Unit price: {unit_price if unit_price is not None else 'Not found'}"
                  f" | Line total: {line_total if line_total is not None else 'Not found'}", size=10)

    findings = data.get("validation_errors") or []
    paragraph("Validation review", size=13, bold=True, gap=4)
    if not findings:
        paragraph("No stored validation findings.")
    for finding in findings:
        paragraph(f"{finding.get('severity', 'info').upper()} - {finding.get('field_name', 'invoice')}: "
                  f"{finding.get('message', finding.get('code', 'Review finding'))}", size=10)

    for page_number, pdf_page in enumerate(pdf, start=1):
        pdf_page.insert_text((left, 770), f"Generated review summary | Page {page_number} of {len(pdf)}",
                             fontname="helv", fontsize=8, color=(0.4, 0.45, 0.42))
    result = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    return result
