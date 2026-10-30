# AI Prompts and Development Disclosure

This document explains two separate uses of prompts:

1. **Development prompts** describe the instructions used with an AI coding assistant to build and revise this repository.
2. **Runtime prompts** are sent by the running backend to the optional Ollama or Gemini model when a reviewer requests AI-assisted extraction or correction suggestions.

AI-assisted development is not the same as runtime AI. The application’s deterministic extraction and validation do not require a model, an API key, or an Internet connection.

## AI-assisted development disclosure

This repository was developed with AI coding assistance (OpenAI Codex) under the project owner's requirements and review. The owner supplied the assessment brief and follow-up implementation requests. The coding assistant inspected the existing repository, made changes in its modules, and ran the listed automated checks. Runtime invoice processing does not use the coding assistant.

The prompt descriptions below are **detailed summaries reconstructed from the assessment requests and implementation stages**, not a verbatim transcript of every message exchanged during development.

### Development prompt 1: application foundation

Representative prompt:

```text
Act as a senior full-stack engineer. Inspect the existing repository and preserve
useful working code. Implement a Windows-friendly, local-first document
intelligence application using FastAPI, React with Vite, SQLite with SQLAlchemy,
Pydantic schemas, and pytest. Configure settings through environment variables,
safe database sessions, UTC timestamps, exact Decimal money values, CORS, health
and readiness checks, structured errors/logging, and a basic frontend health
check. Keep optional AI out of the required startup path. Add documentation and
tests, run the checks, fix reproducible failures, and report only work that was
actually verified. Do not commit secrets, databases, uploads, or environments.
```

The specific repository instructions also required separate modules, PowerShell setup commands, and keeping originals separate from processed text and JSON.

### Development prompt 2: ingestion and text extraction

Representative prompt:

```text
Continue the existing project without replacing working parsers. Implement a
reusable ingestion pipeline for digital and scanned PDFs, JPG/JPEG, PNG, TIFF,
XLSX, XLS, and CSV using free local libraries. Extract PDFs incrementally by
page, OCR pages with little text, process every spreadsheet worksheet, preserve
page/sheet/row context, and normalize outputs. Validate extensions, signatures,
size and page limits; generate safe names; hash content; isolate per-file
failures; close temporary resources; and keep OCR independent of LLMs and the
Internet. Add fixtures and tests for valid, scanned, malformed, empty, and
unsupported files, and document required Windows tools.
```

This stage led to processor interfaces, the normalized document output, OCR integration, spreadsheet parsing, resource limits, and local fixtures.

### Development prompt 3: invoice extraction and financial review

Representative prompt:

```text
Build invoice extraction on top of the existing parsed text and tables. Extract
invoice number/date, supplier/customer, subtotal, tax, total, currency, and line
items. Use deterministic patterns and table parsing first; allow optional local
Ollama assistance. Validate model output with Pydantic, use bounded retries,
reject values unsupported by source evidence, and leave missing values null.
Record evidence, source locations, method, field quality and review warnings.
Implement explainable financial checks with Decimal arithmetic, configurable
rounding tolerance, normalized supplier-plus-invoice-number duplicate checks,
and comparisons between explicitly selected stored invoices. Preserve prior
values and correction history.
```

### Development prompt 4: persistent API and worker

Representative prompt:

```text
Connect the existing processors and invoice services to real FastAPI endpoints
and the existing SQLAlchemy models. Accept multiple uploads, persist document
and job records, return immediately, and process queued jobs in a restart-safe
SQLite-backed worker. Add status/results/history, manual corrections, retry,
search, comparison, and safe downloads. Prevent duplicate concurrent claims,
validate inputs, return structured safe errors, and test endpoint behavior with
temporary databases. Do not return hardcoded or fake search/results data.
```

### Development prompt 5: simple review UI and finalization

Representative prompt:

```text
Make the React interface understandable for an invoice reviewer. Let the user
upload files, see processing status and validation findings, inspect extracted
values with evidence, request optional AI proposals, explicitly choose and save
corrections, compare selected related documents, search results, and download
the original file or a clearly labeled reviewed summary. Make corrections
auditable, make destructive removal explicit, use real backend data, and test
the frontend build and workflows. Document setup, architecture, limitations,
evaluation results, and a walkthrough without claiming unmeasured production
scale or accuracy.
```

These development prompts express the requirements and desired outcomes. They are not prompts that the application sends to an invoice model.

## Runtime AI behavior

Runtime AI is off unless configured. `AI_PROVIDER=none` uses deterministic extraction only. `AI_PROVIDER=ollama` selects the local Ollama endpoint when Ollama is enabled and a model is configured. `AI_PROVIDER=gemini` selects the Gemini Developer API when a key is configured. Only the selected provider is used.

The backend sends normalized text and table evidence, capped at 60,000 characters. It does not upload the original PDF, image, or spreadsheet bytes to the model provider. The model name, provider, retry count, extraction method, and field evidence are recorded with the result. The model does not run code and output is treated as untrusted data.

### Runtime invoice extraction prompt

Both provider paths use these extraction instructions. The code adds the document's normalized evidence after the `Evidence:` marker:

```text
Extract invoice values only when directly supported by the supplied evidence.
Use null for unknown scalar values and an empty array for unknown line items.
Never infer or invent facts. Return only JSON with exactly these keys:
{"invoice_number":null,"invoice_date":null,"supplier_name":null,"customer_name":null,"subtotal":null,"tax_amount":null,"total_amount":null,"currency":null,"line_items":[]}
Each line item has description, quantity, unit_price, line_total, source_page,
source_sheet, source_snippet. Return monetary and quantity values as decimal
strings when possible.
Evidence:
<normalized page and worksheet text from this document>
```

### Runtime safeguards

- **Evidence first:** deterministic extraction always runs. Deterministic non-null values take precedence over model suggestions.
- **Schema validation:** the complete response is parsed as the `InvoiceExtraction` Pydantic schema. Unknown keys, invalid currency codes, invalid types, and malformed JSON are rejected.
- **Bounded retries:** each provider is tried at most twice. Invalid, ungrounded, unavailable, or timed-out output is discarded; deterministic results remain available.
- **Grounding:** non-null scalar values must appear in the normalized evidence. Line-item values must be grounded too. Unsupported values do not overwrite extracted values.
- **Uncertainty:** the model's self-reported confidence is not trusted. Missing values stay null, and the app uses separate heuristic source-quality indicators that are explicitly not calibrated probabilities.
- **Human approval:** the suggestions endpoint returns proposals only for fields already flagged for review. It does not save or apply them. A reviewer must select suggestions and explicitly save the correction; the old value and correction history are retained.

### Ollama versus Gemini

**Ollama** runs on the local machine at `http://localhost:11434`. The code requests JSON mode and temperature zero. No API key is used, and after the model is downloaded inference can run offline.

**Gemini** makes an HTTPS request to Google's API. Its API key comes from the backend-only `GEMINI_API_KEY` environment variable and is sent in a request header, never in the URL or frontend. This provider is optional and changes the original assessment's local-only/no-key constraint. Google documents model-specific free quotas; its terms say content submitted under unpaid usage may be used to improve services and may be reviewed. Use only synthetic, non-sensitive assessment documents with unpaid Gemini quota. See Google's [pricing](https://ai.google.dev/gemini-api/docs/pricing), [terms](https://ai.google.dev/gemini-api/terms), and [API key guidance](https://ai.google.dev/gemini-api/docs/api-key).

## Runtime correction suggestions

The AI suggestions endpoint uses the same evidence-first invoice extraction prompt. After receiving a validated candidate, the backend compares it with stored values and the fields already marked for review. It returns only changed, non-null proposals with supporting snippets. It does not modify the database. The normal correction endpoint records accepted values, reviewer/reason, model provenance, and before/after history.

## Prompt maintenance and limitations

Keep the prompt explicit about nulls, source evidence, JSON shape, and no inference. Update `InvoiceExtraction`, output validation, tests, and this document together when changing the response contract. A small local model can misunderstand or omit invoice fields; grounding checks reduce unsupported values but do not prove a value is correct. OCR mistakes can also affect any model's evidence. Review all flagged results against the original document.
