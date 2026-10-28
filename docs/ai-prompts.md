# Local AI extraction prompt

Runtime AI is optional. The deterministic parser always runs first and remains available when Ollama is disabled, stopped, or returns malformed output. Ollama is contacted only at the configured local base URL and only when `OLLAMA_ENABLED=true` and `OLLAMA_MODEL` is set.

## Invoice extraction

The application sends a bounded text excerpt (up to 60,000 characters) to the local Ollama `/api/generate` endpoint. The current prompt is:

```text
Extract invoice values only when directly supported by the supplied evidence. Use null for unknown values; never infer missing facts. Return JSON matching this schema: {"invoice_number":null,"invoice_date":null,"supplier_name":null,"customer_name":null,"subtotal":null,"tax_amount":null,"total_amount":null,"currency":null,"line_items":[]}
Each line item has description, quantity, unit_price, line_total, source_page, source_sheet, source_snippet.
Evidence:
<normalized page and worksheet text from this document>
```

The caller requests JSON mode and temperature zero, validates the response with `InvoiceExtraction` (unknown keys and non-ISO currency codes are rejected), and retries at most twice. Non-null model values must also be grounded in the supplied evidence; unsupported line items must include a source snippet present in that evidence. Invalid, ungrounded, unavailable, or timed-out output is discarded and deterministic results remain in use. Deterministic values take precedence over model values.

The extraction result stores `extraction_method`, the configured model when used, retry limit, field evidence, warnings, and heuristic field-quality scores. Those scores describe source quality and are not calibrated probabilities. OCR-derived fields are flagged for human review. Missing values stay null; unreadable markers are recorded as uncertain.

## AI-assisted development versus runtime AI

AI-assisted development means a developer may use a coding assistant while writing or reviewing this repository. It is not part of the deployed application and does not process customer files by itself. Runtime AI is the optional local Ollama call described above. Runtime invoice extraction works without runtime AI, API keys, paid services, or an Internet connection. Enabling Ollama requires the user to install and run Ollama locally and choose a model that fits available memory; the model name and endpoint are environment settings.

## Prompt maintenance

Keep prompt edits evidence-focused, explicitly require null for unsupported values, and update schema and tests whenever the returned JSON shape changes. Never rely on the model's self-reported confidence as a probability. Prompt text and model output are not treated as trusted executable content.
