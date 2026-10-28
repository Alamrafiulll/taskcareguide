"""Explainable invoice checks. No tax-rate or jurisdiction is presumed."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any


def normalize_identifier(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def normalize_supplier(value: str | None) -> str:
    value = (value or "").casefold()
    value = re.sub(r"\b(incorporated|inc|limited|ltd|llc|corp|corporation|company|co)\b", "", value)
    return re.sub(r"[^a-z0-9]", "", value)


def is_duplicate_invoice(invoice_a: dict[str, Any], invoice_b: dict[str, Any]) -> bool:
    number_a, number_b = normalize_identifier(invoice_a.get("invoice_number")), normalize_identifier(invoice_b.get("invoice_number"))
    supplier_a, supplier_b = normalize_supplier(invoice_a.get("supplier_name") or invoice_a.get("vendor_name")), normalize_supplier(invoice_b.get("supplier_name") or invoice_b.get("vendor_name"))
    return bool(number_a and number_b and supplier_a and supplier_b and number_a == number_b and supplier_a == supplier_b)


def validate_invoice(invoice: dict[str, Any], *, rounding_tolerance: Decimal = Decimal("0.02"),
                     allow_negative_amounts: bool = False) -> list[dict[str, Any]]:
    """Return structured findings with values serialized as exact decimal strings."""
    errors: list[dict[str, Any]] = []

    def add(code: str, field: str, message: str, severity: str = "error", expected=None, observed=None, explanation=None):
        errors.append({"code": code, "field": field, "message": message, "severity": severity,
                       "expected_value": _json_value(expected), "observed_value": _json_value(observed),
                       "explanation": explanation or message})

    for field in ("invoice_number", "invoice_date", "supplier_name", "currency", "total_amount"):
        if invoice.get(field) in (None, ""):
            add("required_field_missing", field, f"Required field {field.replace('_', ' ')} is missing.", "error")
    for field in ("subtotal", "tax_amount"):
        if invoice.get(field) in (None, ""):
            add("field_missing", field, f"{field.replace('_', ' ').capitalize()} was not found; verify whether it applies.", "warning")

    invoice_date = invoice.get("invoice_date")
    if invoice_date:
        try:
            date.fromisoformat(str(invoice_date)[:10])
        except ValueError:
            add("invalid_invoice_date", "invoice_date", "Invoice date is not a valid ISO calendar date.", observed=invoice_date)

    amount_fields = ("subtotal", "tax_amount", "total_amount")
    for field in amount_fields:
        amount = _to_decimal(invoice.get(field))
        if invoice.get(field) is not None and amount is None:
            add("invalid_amount", field, f"{field.replace('_', ' ').capitalize()} is not a valid decimal amount.", observed=invoice.get(field))
        elif amount is not None and amount < 0 and not allow_negative_amounts:
            add("negative_amount", field, "Negative amount is not permitted by the configured invoice rule.", observed=amount)

    subtotal, tax, total = (_to_decimal(invoice.get(field)) for field in amount_fields)
    if subtotal is not None and tax is not None and total is not None:
        expected = subtotal + tax
        if abs(expected - total) > rounding_tolerance:
            add("subtotal_tax_total_mismatch", "total_amount", "Subtotal plus stated tax does not reconcile to total within tolerance.",
                expected=expected, observed=total,
                explanation=f"Compared subtotal + tax to total using tolerance {rounding_tolerance}.")

    line_items = invoice.get("line_items") or []
    line_amounts = []
    for index, item in enumerate(line_items):
        quantity = _to_decimal(item.get("quantity"))
        unit = _to_decimal(item.get("unit_price"))
        amount = _to_decimal(item.get("line_total", item.get("amount")))
        field = f"line_items[{index}]"
        for key, raw in (("quantity", item.get("quantity")), ("unit_price", item.get("unit_price")),
                         ("line_total", item.get("line_total", item.get("amount")))):
            parsed = _to_decimal(raw)
            if raw not in (None, "") and parsed is None:
                add("invalid_amount", f"{field}.{key}", f"Line item {key.replace('_', ' ')} is not a valid decimal.", observed=raw)
            elif parsed is not None and parsed < 0 and not allow_negative_amounts:
                add("negative_amount", f"{field}.{key}", "Negative line item value is not permitted by the configured invoice rule.", observed=parsed)
        if quantity is not None and unit is not None and amount is not None:
            expected = quantity * unit
            if abs(expected - amount) > rounding_tolerance:
                add("line_item_multiplication_mismatch", field, "Quantity multiplied by unit price does not match line total within tolerance.",
                    expected=expected, observed=amount,
                    explanation=f"Compared quantity × unit price using tolerance {rounding_tolerance}.")
        if amount is not None:
            line_amounts.append(amount)
    if subtotal is not None and line_amounts:
        expected = sum(line_amounts, Decimal("0"))
        if abs(expected - subtotal) > rounding_tolerance:
            add("line_items_subtotal_mismatch", "subtotal", "Sum of extracted line totals does not match subtotal within tolerance.",
                expected=expected, observed=subtotal,
                explanation=f"Compared sum of available line totals using tolerance {rounding_tolerance}.")

    for field, values in (invoice.get("field_candidates") or {}).items():
        normalized = {str(value).strip().casefold() for value in values if value is not None}
        if len(normalized) > 1:
            add("conflicting_source_values", field, f"Source contains conflicting candidates for {field.replace('_', ' ')}.",
                "warning", observed=values, explanation="Review the cited page or worksheet snippets before accepting this value.")
    for warning in invoice.get("warnings", []):
        if warning.get("code") == "field_missing":
            field = warning.get("field", "unknown")
            if not any(e["field"] == field for e in errors):
                add("field_missing", field, warning.get("message", "Field missing."), "warning")
        elif warning.get("code") in {"field_conflicting_evidence", "field_uncertain", "field_low_quality", "ocr_source"}:
            add(warning["code"], warning.get("field", "source"), warning.get("message", "Source requires review."), "warning")
    return errors


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
