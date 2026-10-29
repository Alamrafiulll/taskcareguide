"""Evidence-first invoice extraction layered over normalized ingestion output."""
from __future__ import annotations

import json
import ipaddress
import logging
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.schemas.invoice import InvoiceExtraction

logger = logging.getLogger(__name__)
RETRY_LIMIT = 2
FIELD_LABELS = {
    "invoice_number": r"invoice\s*(?:no\.?|number|#|id)",
    "invoice_date": r"invoice\s*date|date\s*of\s*issue|issued\s*on",
    "supplier_name": r"supplier|vendor|seller|from",
    "customer_name": r"customer|bill\s*to|sold\s*to",
    "subtotal": r"sub\s*total|subtotal|net\s*(?:amount|total)",
    "tax_amount": r"tax(?:\s*(?:amount|total))?|vat|gst|sales\s*tax",
    "total_amount": r"grand\s*total|amount\s*due|total\s*due|invoice\s*total|total",
}
MONEY_RE = re.compile(r"(?<![\w])(?:[$€£¥]\s*)?\(?-?\d[\d,]*(?:\.\d{1,4})?\)?")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().replace(" ", "")
    if not raw:
        return None
    raw = raw.replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    # Common invoice grouping. Ambiguous comma-decimal values remain conservative.
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", raw):
        raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:
        if re.fullmatch(r"-?\d+,\d{1,2}", raw):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        result = Decimal(raw)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _segments_evidence(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for seg in normalized.get("segments", []):
        location = {"page": seg.get("page_number"), "sheet": seg.get("sheet_name")}
        text = str(seg.get("text") or "")
        if text.strip():
            evidence.append({**location, "text": text})
        table = seg.get("table") or {}
        for row in table.get("rows", []):
            cells = row.get("cells", [])
            cell_text = " | ".join(f"{cell.get('coordinate', '')}: {cell.get('value')}" for cell in cells if cell.get("value") is not None)
            if cell_text:
                evidence.append({**location, "text": cell_text, "row": row.get("row_number")})
            values = row.get("values", [])
            if len(values) >= 2 and values[0] is not None and values[1] is not None:
                label = str(values[0]).strip()
                if any(re.search(rf"\b(?:{pattern})\b", label, re.I) for pattern in FIELD_LABELS.values()):
                    coordinates = [cell.get("coordinate") for cell in cells[:2] if cell.get("coordinate")]
                    evidence.append({**location, "text": f"{label}: {values[1]}", "row": row.get("row_number"),
                                     "coordinates": coordinates})
    return evidence


def _evidence_for(text: str, *, page: int | None = None, sheet: str | None = None,
                  coordinates: list[str] | None = None) -> dict[str, Any]:
    snippet = text.strip()[:500]
    result: dict[str, Any] = {"snippet": snippet}
    if page is not None:
        result["page"] = page
    if sheet is not None:
        result["sheet"] = sheet
    if coordinates:
        result["cell_coordinates"] = coordinates
    return result


def _labeled_value(line: str, label_pattern: str) -> str | None:
    match = re.search(rf"(?:^|\b)(?:{label_pattern})(?=\s|[:#\-]|$)\s*[:#\-]?\s*(.+)$", line, re.I)
    if not match:
        return None
    value = re.split(r"\s*\|\s*|\s{2,}", match.group(1).strip())[0].strip(" :#\t")
    value = re.sub(r"^[A-Z]{1,3}\d+\s*:\s*", "", value)
    return value or None


def _find_field(texts: list[dict[str, Any]], field: str) -> tuple[Any, list[dict[str, Any]], list[Any]]:
    pattern = FIELD_LABELS[field]
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for source in texts:
        for line in source["text"].splitlines():
            value = _labeled_value(line, pattern)
            if value is None:
                continue
            if re.fullmatch(r"(?i)(?:unknown|unreadable|illegible|n/?a|none|\?+|[-_]+|\[?(?:illegible|unreadable)\]?)", value.strip()):
                continue
            if field in {"subtotal", "tax_amount", "total_amount"}:
                values = MONEY_RE.findall(value)
                parsed = _decimal(values[-1]) if values else None
                if parsed is None:
                    continue
                value = parsed
            elif field == "invoice_date":
                value = re.split(r"\s{2,}|\s+[|]\s+", value)[0].strip()
                value = _normalize_date(value)
            else:
                value = value.split("\t")[0].strip()
            candidates.append((value, _evidence_for(line, page=source.get("page"), sheet=source.get("sheet"),
                                                     coordinates=source.get("coordinates"))))
    if not candidates:
        return None, [], []
    # Keep first occurrence while retaining alternatives for conflict validation.
    unique = []
    for value, ev in candidates:
        key = str(value).strip().casefold()
        if not any(str(existing).strip().casefold() == key for existing, _ in unique):
            unique.append((value, ev))
    return unique[0][0], [ev for _, ev in unique], [value for value, _ in unique]


def _normalize_date(value: str) -> str:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d-%b-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return value


def _table_line_items(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for seg in normalized.get("segments", []):
        table = seg.get("table") or {}
        rows = table.get("rows", [])
        header_row = None
        mapping: dict[str, int] = {}
        for row_index, row in enumerate(rows[:20]):
            values = [str(v or "").strip().casefold() for v in row.get("values", [])]
            candidate = {}
            for idx, value in enumerate(values):
                if any(token in value for token in ("description", "item", "service", "product")):
                    candidate["description"] = idx
                elif value in {"qty", "quantity", "units"}:
                    candidate["quantity"] = idx
                elif "unit price" in value or value in {"price", "rate"}:
                    candidate["unit_price"] = idx
                elif any(token in value for token in ("line total", "amount", "extended", "total")):
                    candidate["line_total"] = idx
            if "description" in candidate and len(candidate) >= 2:
                header_row, mapping = row_index, candidate
                break
        if header_row is not None:
            for row in rows[header_row + 1:]:
                vals = row.get("values", [])
                get = lambda key: vals[mapping[key]] if key in mapping and mapping[key] < len(vals) else None
                description = get("description")
                if description is None or not str(description).strip() or _is_summary_description(description):
                    continue
                quantity = _decimal(get("quantity"))
                unit_price = _decimal(get("unit_price"))
                line_total = _decimal(get("line_total"))
                source = " | ".join(str(value) for value in vals if value is not None)
                items.append({"description": str(description).strip(), "quantity": quantity,
                              "unit_price": unit_price, "line_total": line_total,
                              "source_page": seg.get("page_number"), "source_sheet": seg.get("sheet_name"),
                              "source_snippet": source[:500]})
        # Digital PDFs often expose table rows as pipe or tab separated lines rather
        # than a structured table. Preserve and parse those rows as a second source.
        lines = str(seg.get("text") or "").splitlines()
        for index, line in enumerate(lines):
            cells = [cell.strip() for cell in re.split(r"\s*\|\s*|\t+", line.strip())]
            labels = [cell.casefold() for cell in cells]
            mapping = {}
            for idx, value in enumerate(labels):
                if any(token in value for token in ("description", "item", "service", "product")):
                    mapping["description"] = idx
                elif value in {"qty", "quantity", "units"}:
                    mapping["quantity"] = idx
                elif "unit price" in value or value in {"price", "rate"}:
                    mapping["unit_price"] = idx
                elif any(token in value for token in ("line total", "amount", "extended", "total")):
                    mapping["line_total"] = idx
            if "description" not in mapping or len(mapping) < 2:
                continue
            for row_line in lines[index + 1:]:
                values = [cell.strip() for cell in re.split(r"\s*\|\s*|\t+", row_line.strip())]
                if len(values) <= mapping["description"]:
                    continue
                description = values[mapping["description"]]
                if not description or _is_summary_description(description):
                    continue

                def text_cell(key):
                    position = mapping.get(key)
                    return values[position] if position is not None and position < len(values) else None

                quantity, unit_price, line_total = (_decimal(text_cell(key)) for key in ("quantity", "unit_price", "line_total"))
                if not any(value is not None for value in (quantity, unit_price, line_total)):
                    continue
                if not any(item.get("source_snippet") == row_line.strip() for item in items):
                    items.append({"description": description, "quantity": quantity,
                                  "unit_price": unit_price, "line_total": line_total,
                                  "source_page": seg.get("page_number"), "source_sheet": seg.get("sheet_name"),
                                  "source_snippet": row_line.strip()[:500]})
    return items


def _is_summary_description(value: Any) -> bool:
    return str(value).strip().casefold().rstrip(":") in {
        "subtotal", "sub total", "tax", "vat", "gst", "total", "grand total", "amount due", "balance due"
    }


def deterministic_extraction(normalized: dict[str, Any]) -> tuple[InvoiceExtraction, dict[str, list[dict[str, Any]]], dict[str, list[Any]]]:
    sources = _segments_evidence(normalized)
    text = "\n".join(source["text"] for source in sources)
    values: dict[str, Any] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    candidates: dict[str, list[Any]] = {}
    for field in FIELD_LABELS:
        value, refs, distinct_values = _find_field(sources, field)
        values[field] = value
        evidence[field] = refs
        if refs:
            candidates[field] = distinct_values

    # A code or currency is taken only from explicit ISO currency text, not an ambiguous symbol.
    currency_matches = list(re.finditer(r"\b(?:currency|ccy)\s*[:=]?\s*([A-Z]{3})\b", text, re.I))
    if not currency_matches:
        currency_matches = list(re.finditer(r"\b(AUD|CAD|CHF|EUR|GBP|INR|JPY|NZD|SGD|USD|CNY|MYR|AED|SAR|HKD|THB|IDR|PHP|KRW|TWD|ZAR|SEK|NOK|DKK)\b", text, re.I))
    currency_match = currency_matches[0] if currency_matches else None
    values["currency"] = (currency_match.group(1) if currency_match.lastindex else currency_match.group(0)).upper() if currency_match else None
    currency_refs = []
    if currency_match:
        currency_values = []
        for match in currency_matches:
            candidate = (match.group(1) if match.lastindex else match.group(0)).upper()
            if candidate not in currency_values:
                currency_values.append(candidate)
        candidates["currency"] = currency_values
        source = next((s for s in sources if currency_match.group(0).casefold() in s["text"].casefold()), None)
        if source:
            currency_refs.append(_evidence_for(source["text"], page=source.get("page"), sheet=source.get("sheet")))
    evidence["currency"] = currency_refs
    values["line_items"] = _table_line_items(normalized)
    evidence["line_items"] = [{"snippet": item.get("source_snippet"),
                                **({"page": item.get("source_page")} if item.get("source_page") is not None else {}),
                                **({"sheet": item.get("source_sheet")} if item.get("source_sheet") is not None else {})}
                               for item in values["line_items"]]

    # Restrict dates to values explicitly following a date label; preserve the source spelling.
    model = InvoiceExtraction.model_validate(values)
    return model, evidence, candidates


def _grounded(value: Any, evidence_text: str) -> bool:
    if value is None:
        return True
    normalized_text = re.sub(r"\s+", " ", evidence_text).casefold()
    if isinstance(value, Decimal):
        return any(_decimal(token) == value for token in MONEY_RE.findall(evidence_text))
    return str(value).strip().casefold() in normalized_text


def _ollama_extract(settings: Settings, evidence_text: str) -> InvoiceExtraction | None:
    if not settings.ollama_enabled or not settings.ollama_model:
        return None
    parsed_endpoint = urlsplit(settings.ollama_base_url)
    host = (parsed_endpoint.hostname or "").casefold()
    is_loopback = host == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parsed_endpoint.scheme not in {"http", "https"} or not is_loopback:
        logger.error("Ollama is enabled with a non-local endpoint; refusing the request")
        return None
    prompt = (
        "Extract invoice values only when directly supported by the supplied evidence. "
        "Use null for unknown values; never infer missing facts. Return JSON matching this schema: "
        '{"invoice_number":null,"invoice_date":null,"supplier_name":null,"customer_name":null,'
        '"subtotal":null,"tax_amount":null,"total_amount":null,"currency":null,"line_items":[]}\n'
        "Each line item has description, quantity, unit_price, line_total, source_page, source_sheet, source_snippet.\n"
        "Evidence:\n" + evidence_text[:60000]
    )
    endpoint = settings.ollama_base_url.rstrip("/") + "/api/generate"
    for attempt in range(RETRY_LIMIT):
        request = urllib.request.Request(endpoint, data=json.dumps({
            "model": settings.ollama_model, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0},
        }).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
                outer = json.loads(response.read().decode("utf-8"))
            raw = outer.get("response", "")
            if not isinstance(raw, str):
                raise ValueError("Ollama response field was not text")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            model = InvoiceExtraction.model_validate(payload)
            # Reject the entire response when any scalar is not grounded in provided text.
            for field in FIELD_LABELS:
                if not _grounded(getattr(model, field), evidence_text):
                    raise ValueError(f"Ungrounded value for {field}")
            if not _grounded(model.currency, evidence_text):
                raise ValueError("Ungrounded currency")
            for item in model.line_items:
                for value in (item.description, item.quantity, item.unit_price, item.line_total):
                    if value is not None and not _grounded(value, evidence_text):
                        raise ValueError("Ungrounded line item")
            return model
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            logger.warning("Ollama extraction attempt %s/%s failed: %s", attempt + 1, RETRY_LIMIT, exc)
    return None


def extract_invoice(normalized: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    deterministic, evidence, candidates = deterministic_extraction(normalized)
    evidence_text = "\n".join(source["text"] for source in _segments_evidence(normalized))
    method = "deterministic"
    extracted = deterministic
    llm = _ollama_extract(settings, evidence_text)
    if llm is not None:
        merged = deterministic.model_dump()
        llm_fields = []
        for field in FIELD_LABELS.keys() | {"currency"}:
            local = getattr(deterministic, field)
            proposed = getattr(llm, field)
            if local is None and proposed is not None:
                merged[field] = proposed
                llm_fields.append(field)
        # Deterministic table parsing retains cell locations when present.
        if not deterministic.line_items and llm.line_items:
            supported_items = [item for item in llm.line_items if item.source_snippet and
                               re.sub(r"\s+", " ", item.source_snippet).casefold() in re.sub(r"\s+", " ", evidence_text).casefold()]
            if len(supported_items) == len(llm.line_items):
                merged["line_items"] = [item.model_dump() for item in supported_items]
                llm_fields.append("line_items")
        extracted = InvoiceExtraction.model_validate(merged)
        method = "deterministic+ollama"
        segments = normalized.get("segments", [])
        for field in llm_fields:
            value = getattr(extracted, field)
            if field == "line_items":
                evidence[field] = [{"snippet": item.source_snippet, **({"page": item.source_page} if item.source_page else {}),
                                    **({"sheet": item.source_sheet} if item.source_sheet else {})}
                                   for item in extracted.line_items]
            else:
                for segment in segments:
                    text = str(segment.get("text") or "")
                    if str(value).casefold() in text.casefold():
                        evidence[field] = [_evidence_for(text, page=segment.get("page_number"), sheet=segment.get("sheet_name"))]
                        break

    statuses = {}
    confidence = {}
    warnings = []
    ocr_pages = {segment.get("page_number") for segment in normalized.get("segments", []) if segment.get("ocr_used")}
    for field in [*FIELD_LABELS, "currency", "line_items"]:
        value = getattr(extracted, field)
        if value is None or value == []:
            unresolved = bool(re.search(rf"(?:{FIELD_LABELS.get(field, r'currency')})\s*[:#-]?\s*(?:unknown|unreadable|illegible|\?+|\[illegible\])", evidence_text, re.I))
            statuses[field] = "uncertain" if unresolved else "missing"
            confidence[field] = None
        else:
            statuses[field] = "present"
            # This is a rule-based quality indicator, not a calibrated probability.
            confidence[field] = 0.9 if evidence.get(field) else 0.65
            cited_pages = {ref.get("page") for ref in evidence.get(field, []) if ref.get("page") is not None}
            if confidence[field] < 0.7 or cited_pages.intersection(ocr_pages):
                statuses[field] = "low_confidence"
                confidence[field] = min(confidence[field], 0.65)
                warnings.append({"code": "field_low_quality", "field": field,
                                 "message": f"The extracted {field.replace('_', ' ')} has limited or OCR-derived evidence and needs review."})
    for field, status in statuses.items():
        if status == "missing" and field != "line_items":
            warnings.append({"code": "field_missing", "field": field, "message": f"No supported {field.replace('_', ' ')} was found."})
        elif status == "uncertain":
            warnings.append({"code": "field_uncertain", "field": field,
                             "message": f"Source marks {field.replace('_', ' ')} as unreadable or uncertain; no value was invented."})
    for field, values in candidates.items():
        if len(values) > 1:
            warnings.append({"code": "field_conflicting_evidence", "field": field,
                             "message": f"Multiple source snippets contain a possible {field.replace('_', ' ')}."})
            statuses[field] = "uncertain"
    if normalized.get("ocr_used"):
        warnings.append({"code": "ocr_source", "message": "Some extraction evidence came from OCR and should be reviewed."})
    result = extracted.model_dump(mode="json")
    result.update({"extraction_method": method, "field_status": statuses,
                   "field_confidence": confidence, "field_evidence": evidence,
                   "field_candidates": candidates,
                   "field_confidence_note": "Heuristic source-quality scores only; not statistically calibrated probabilities.",
                   "warnings": warnings, "extraction_metadata": {"ollama_enabled": settings.ollama_enabled,
                   "ollama_model": settings.ollama_model if method.endswith("ollama") else None,
                   "ollama_retry_limit": RETRY_LIMIT if settings.ollama_enabled else 0}})
    return result


def suggest_invoice_corrections(normalized: dict[str, Any], current_invoice: dict[str, Any],
                                settings: Settings | None = None) -> dict[str, Any] | None:
    """Return evidence-grounded local-model proposals for fields already flagged for review.

    This function never persists or applies suggestions. A reviewer must approve them
    through the ordinary invoice correction endpoint.
    """
    settings = settings or get_settings()
    evidence_sources = _segments_evidence(normalized)
    evidence_lines = [line.strip() for source in evidence_sources
                      for line in source["text"].splitlines() if line.strip()]
    evidence_text = "\n".join(source["text"] for source in evidence_sources)
    candidate = _ollama_extract(settings, evidence_text)
    if candidate is None:
        return None

    review_fields = set()
    for warning in current_invoice.get("warnings") or []:
        field = warning.get("field")
        if field:
            review_fields.add(field)
    for finding in normalized.get("invoice_validation") or []:
        field = finding.get("field")
        if field:
            review_fields.add(field.split(".", 1)[0].split("[", 1)[0])
    statuses = current_invoice.get("field_status") or {}
    review_fields.update(field for field, state in statuses.items()
                         if state in {"missing", "uncertain", "low_confidence"})

    def as_json(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

    def same_value(field: str, left: Any, right: Any) -> bool:
        if field in {"subtotal", "tax_amount", "total_amount"}:
            return _decimal(left) == _decimal(right)
        if field == "line_items":
            def signature(items):
                return [(" ".join(str(item.get("description") or "").casefold().split()),
                         _decimal(item.get("quantity")), _decimal(item.get("unit_price")),
                         _decimal(item.get("line_total", item.get("amount")))) for item in items or []]
            return signature(left) == signature(right)
        return " ".join(str(left or "").strip().casefold().split()) == " ".join(str(right or "").strip().casefold().split())

    proposals = []
    for field in [*FIELD_LABELS, "currency"]:
        old = current_invoice.get(field)
        new = as_json(getattr(candidate, field))
        if new is None or field not in review_fields or same_value(field, old, new):
            continue
        refs = []
        for line in evidence_lines:
            if _grounded(getattr(candidate, field), line):
                refs.append(line[:500])
            if len(refs) == 3:
                break
        if refs:
            proposals.append({"field": field, "current_value": old,
                              "suggested_value": new, "evidence": refs})

    current_items = current_invoice.get("line_items") or []
    proposed_items = [item.model_dump(mode="json") for item in candidate.line_items]
    if "line_items" in review_fields and proposed_items and not same_value("line_items", current_items, proposed_items):
        snippets = [item.get("source_snippet") for item in proposed_items if item.get("source_snippet")]
        if len(snippets) == len(proposed_items) and all(_grounded(item.get("description"), evidence_text)
                                                         for item in proposed_items):
            proposals.append({"field": "line_items", "current_value": current_items,
                              "suggested_value": proposed_items, "evidence": snippets[:6]})
    return {"model": settings.ollama_model, "suggestions": proposals}
