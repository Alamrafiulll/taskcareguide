from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.financial_validation import normalize_identifier, normalize_supplier

COMPARE_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "currency", "subtotal", "tax_amount", "total_amount")
NUMERIC_FIELDS = {"subtotal", "tax_amount", "total_amount"}


def compare_invoices(invoice_a: dict[str, Any], invoice_b: dict[str, Any]) -> dict[str, Any]:
    number_a = normalize_identifier(invoice_a.get("invoice_number"))
    number_b = normalize_identifier(invoice_b.get("invoice_number"))
    supplier_a = normalize_supplier(invoice_a.get("supplier_name") or invoice_a.get("vendor_name"))
    supplier_b = normalize_supplier(invoice_b.get("supplier_name") or invoice_b.get("vendor_name"))
    id_match = bool(number_a and number_b and number_a == number_b and (not supplier_a or not supplier_b or supplier_a == supplier_b))
    corroborating_match = bool(supplier_a and supplier_a == supplier_b and invoice_a.get("invoice_date") and invoice_a.get("invoice_date") == invoice_b.get("invoice_date"))
    if not id_match and not corroborating_match:
        return {"comparable": False, "review_recommended": True, "matched_fields": [], "mismatched_fields": [],
                "missing_fields": [], "reasons": ["The selected records do not share a matching invoice identifier or supplier and issue date; comparison stopped to avoid cross-invoice matching."]}
    matched, mismatched, missing = [], [], []
    for field in COMPARE_FIELDS:
        a, b = invoice_a.get(field), invoice_b.get(field)
        if a in (None, "") or b in (None, ""):
            missing.append({"field": field, "source_a": a, "source_b": b,
                            "missing_from": "both" if a in (None, "") and b in (None, "") else ("a" if a in (None, "") else "b")})
            continue
        if field in NUMERIC_FIELDS:
            try:
                dec_a, dec_b = Decimal(str(a)), Decimal(str(b))
                difference = dec_a - dec_b
                if difference == 0:
                    matched.append({"field": field, "source_a": str(dec_a), "source_b": str(dec_b)})
                else:
                    mismatched.append({"field": field, "source_a": str(dec_a), "source_b": str(dec_b), "difference_a_minus_b": str(difference)})
            except InvalidOperation:
                mismatched.append({"field": field, "source_a": a, "source_b": b, "difference_a_minus_b": None})
        else:
            norm_a = normalize_identifier(a) if field == "invoice_number" else " ".join(str(a).casefold().split())
            norm_b = normalize_identifier(b) if field == "invoice_number" else " ".join(str(b).casefold().split())
            target = matched if norm_a == norm_b else mismatched
            target.append({"field": field, "source_a": a, "source_b": b})
    line_a, line_b = invoice_a.get("line_items") or [], invoice_b.get("line_items") or []
    if line_a or line_b:
        map_a = _line_map(line_a)
        map_b = _line_map(line_b)
        for key in sorted(map_a.keys() | map_b.keys()):
            a, b = map_a.get(key), map_b.get(key)
            if a is None or b is None:
                missing.append({"field": "line_items", "source_a": a, "source_b": b, "missing_from": "a" if a is None else "b"})
            elif not _same_line_item(a, b):
                mismatched.append({"field": "line_items", "source_a": a, "source_b": b,
                                   "numeric_differences": _line_differences(a, b)})
            else:
                matched.append({"field": "line_items", "source_a": a, "source_b": b})
    reasons = []
    if mismatched:
        reasons.append(f"{len(mismatched)} field or line-item value(s) differ.")
    if missing:
        reasons.append(f"{len(missing)} field or line-item value(s) are missing from one or both sources.")
    return {"comparable": True, "review_recommended": bool(mismatched or missing), "matched_fields": matched,
            "mismatched_fields": mismatched, "missing_fields": missing, "reasons": reasons}


def _line_key(item: dict[str, Any]) -> str:
    return " ".join(str(item.get("description") or "").casefold().split())


def _line_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    occurrences: dict[str, int] = {}
    result = {}
    for item in items:
        description = _line_key(item)
        occurrence = occurrences.get(description, 0)
        occurrences[description] = occurrence + 1
        result[f"{description}#{occurrence}"] = item
    return result


def _same_line_item(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if _line_key(a) != _line_key(b):
        return False
    for field in ("quantity", "unit_price", "line_total", "amount"):
        value_a, value_b = a.get(field), b.get(field)
        if value_a in (None, "") and value_b in (None, ""):
            continue
        if value_a in (None, "") or value_b in (None, ""):
            return False
        try:
            if Decimal(str(value_a)) != Decimal(str(value_b)):
                return False
        except InvalidOperation:
            if str(value_a) != str(value_b):
                return False
    return True


def _line_differences(a: dict[str, Any], b: dict[str, Any]) -> dict[str, str]:
    differences = {}
    for field in ("quantity", "unit_price", "line_total", "amount"):
        if a.get(field) in (None, "") or b.get(field) in (None, ""):
            continue
        try:
            difference = Decimal(str(a[field])) - Decimal(str(b[field]))
        except InvalidOperation:
            continue
        if difference:
            differences[field] = str(difference)
    return differences
