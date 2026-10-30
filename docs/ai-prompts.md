# AI-Assisted Development Disclosure and Runtime Prompts

This document discloses how AI-assisted development was used and records the prompts used by the application at runtime. These are separate activities: a coding assistant helped build the repository, while the running app can optionally call an invoice-extraction model.

## AI-Assisted Development Disclosure

### Tools Used

OpenAI Codex and ChatGPT were used to assist with development of this AI-powered document intelligence system.

### Scope of AI Assistance

AI assistance supported implementation planning, code generation, debugging guidance, test-case development, documentation, and review of the application architecture.

The implementation was organized into five main stages:

1. Project foundation and local application setup.
2. Document ingestion, PDF/image OCR, and spreadsheet/CSV processing.
3. Invoice extraction, financial validation, duplicate detection, and comparison.
4. Persistent REST API, SQLite-backed background worker, and search.
5. Frontend integration, automated tests, and synthetic evaluation.

### Verification and Responsibility

AI-generated code and suggestions were reviewed and tested to the extent documented in the repository. Actual implementation status, test outcomes, performance measurements, and known limitations are reported in the source, tests, and [evaluation report](evaluation.md).

The repository represents the implementation and verification completed for this assessment. Features that have not been implemented or tested are identified as limitations rather than presented as complete.

### Prompt Records

The records below cover the principal development prompts and relevant follow-up changes or debugging requests. They are concise summaries, not a verbatim transcript of every conversation. Routine questions about how to run or use the app are not included as development prompts.

#### 1. Project foundation

> Inspect the existing repository and preserve useful code. Build a local-first application with FastAPI, React/Vite, SQLite/SQLAlchemy, Pydantic, and pytest. Configure settings, safe transactions, UTC timestamps, exact-decimal money values, CORS, health/readiness endpoints, structured errors/logging, a basic frontend health check, Windows PowerShell setup, and documentation. Keep optional AI out of the required startup path. Run the relevant tests and frontend build, fix reproducible failures, and do not commit secrets, local databases, uploads, or environments.

#### 2. Document ingestion and extraction

> Integrate reusable processors for digital and scanned PDFs, JPG/JPEG, PNG, TIFF, XLSX, XLS, and CSV. Use PyMuPDF, local Tesseract OCR, Pillow, pandas/openpyxl, and xlrd. Process PDFs incrementally, OCR pages with little selectable text, preserve page/sheet/row context, enforce size and page limits, generate safe storage names, hash content, isolate per-file failures, and persist normalized results. Add synthetic fixtures for valid, empty, corrupt, malformed, and unsupported documents.

#### 3. Invoice extraction and financial review

> Extract invoice number/date, supplier/customer, subtotal, tax, total, currency, and line items from parsed text and tables. Prefer deterministic patterns; optionally use local Ollama. Validate model output with Pydantic, retry a bounded number of times, reject unsupported values, preserve missing/uncertain values as null, and retain evidence and provenance. Implement explainable financial rules with Decimal arithmetic, configurable tolerance, supplier-plus-invoice-number duplicate checks, review flags, correction history, and comparison of explicitly selected stored documents.

#### 4. Persistent API, worker, and search

> Connect the existing processors and SQLAlchemy models to real REST endpoints. Accept multiple uploads and persist document/job rows before returning. Process queued documents in a restart-safe SQLite-backed worker. Add status/results/history, corrections, retries, filters/search, comparison, and safe downloads. Prevent conflicting job claims, return structured errors, test endpoints with a temporary database, and do not return hardcoded results.

#### 5. Frontend, tests, and evaluation

> Build a usable React interface connected to the real API for upload, processing status, invoice review, validation findings, corrections, comparison, search, retries, and downloads. Use synthetic labeled fixtures to measure field extraction, processing success/failure, validation outcomes, and timing. Report measured results and limitations; do not claim production-scale concurrency or accuracy from a small local dataset.

#### Follow-up implementation and debugging requests

Follow-up prompts refined the existing implementation rather than creating disconnected examples. They included requests to verify local Tesseract and OCR behavior on Windows; fix reproducible backend and frontend test failures; add provider selection for local Ollama and optional Gemini while retaining deterministic fallback; serve the original file separately from a generated reviewed PDF; add confirmed document removal; simplify navigation and search descriptions; and make automated tests independent of runtime AI settings in `.env`.

## Runtime AI Behavior

Runtime AI is optional. With `AI_PROVIDER=none`, the app uses deterministic extraction only. `AI_PROVIDER=ollama` selects a local Ollama model; `AI_PROVIDER=gemini` selects the Gemini Developer API. The backend calls only the selected, configured provider. Core parsing and validation do not require AI.

The backend sends normalized extracted text and table evidence, capped at 60,000 characters. It does not send the uploaded PDF, image, or spreadsheet bytes to the model provider. Provider/model, extraction method, retry limit, and evidence metadata are recorded with the extraction result. Model output is treated as untrusted structured data and is never executed.

## Runtime Invoice Extraction Prompt

Both provider implementations use the following instructions and append the document's normalized evidence after `Evidence:`:

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

The model response is validated against the `InvoiceExtraction` Pydantic schema. Unknown keys, malformed JSON, invalid currency codes, or unsupported values are rejected. Each provider has at most two attempts. Deterministic extraction runs first, and deterministic non-null values take precedence over model output. If AI is unavailable or invalid, deterministic extraction remains usable.

## Runtime Correction Suggestions

The suggestion endpoint uses the same evidence-first extraction prompt. The backend returns only changed, non-null suggestions for fields already flagged for review, with supporting snippets. Suggestions are not automatically saved or applied. A reviewer must select and save them; the correction audit stores prior and corrected values, reviewer/reason, and model provenance.

## Runtime Safeguards and Provider Notes

- **Grounding:** non-null values must be supported by normalized source evidence. This reduces unsupported output but cannot prove that OCR or the source document is correct.
- **Uncertainty:** missing values remain null. The model's self-reported confidence is not used as a probability; field-quality indicators are heuristic and documented as uncalibrated.
- **Human review:** missing, conflicting, OCR-derived, or low-quality values are flagged. Accepted suggestions are recorded as corrections, not silent replacements.
- **Ollama:** runs locally at the configured loopback address. Once Ollama and its model are installed, inference can run offline without an API key.
- **Gemini:** sends extracted evidence to Google's HTTPS API. Its key is read from backend environment configuration and sent in a request header. The key must never be placed in frontend code or committed files. Gemini is an optional cloud provider and changes the original local-only/no-key constraint. Google's unpaid-tier terms allow submitted content to be used to improve services and reviewed; use synthetic, non-sensitive documents only. See Google's [pricing](https://ai.google.dev/gemini-api/docs/pricing), [terms](https://ai.google.dev/gemini-api/terms), and [API key guidance](https://ai.google.dev/gemini-api/docs/api-key).

## Prompt Maintenance

Keep prompts explicit about the evidence boundary, null handling, and JSON shape. When changing the response contract, update the Pydantic schema, extraction validation, tests, and this document together. Review extracted results against the original document, especially when OCR is involved.
