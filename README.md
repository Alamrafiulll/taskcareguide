# Care Guide — Document Intelligence

A local-first invoice and document processing application. FastAPI accepts multiple uploads into a persistent SQLite queue; a separate worker extracts PDF, image, spreadsheet, and CSV content, extracts invoice fields, and records explainable financial checks. React/Vite provides the local frontend. Core processing uses no paid API, API key, or internet service.

## Requirements

- Windows 11, Python 3.10+, Node.js 20+
- Tesseract OCR executable for scanned PDFs and image OCR (optional for digital PDFs/spreadsheets)
- Optional: Ollama and a local model for assisted invoice extraction
- Optional: sentence-transformers and a downloaded all-MiniLM-L6-v2 model for semantic/hybrid search

Install a 64-bit CPython Windows release from the [official Python downloads](https://www.python.org/downloads/windows/) and enable the Python launcher. The repository was verified with Python 3.10.11. Install Node.js 20 or later from the [official Node.js download page](https://nodejs.org/en/download) (Node 22 LTS is suitable), then open a fresh PowerShell session. Confirm the tools are available:

```powershell
py -0p
node --version
npm.cmd --version
```

Python dependencies are pinned in `backend/requirements.txt` and `backend/requirements-test.txt`; JavaScript dependencies are pinned in `frontend/package.json` and `frontend/package-lock.json`.

## Setup (Windows PowerShell)

Run from the repository root:

```powershell
Copy-Item .env.example .env
py -3.10 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements-test.txt
```

`requirements-test.txt` includes `xlwt` only to create temporary legacy `.xls` fixtures; production `.xls` reading uses `xlrd`.

### Install Tesseract OCR (optional)

Install a current Windows build from the [UB Mannheim Tesseract project](https://github.com/UB-Mannheim/tesseract/wiki) with English language data. Check it in PowerShell:

```powershell
tesseract --version
tesseract --list-langs
```

If it is not on `PATH`, set the full path in `.env`, for example:

```dotenv
TESSERACT_CMD="C:/Program Files/Tesseract-OCR/tesseract.exe"
```

Verify the configured executable with `Push-Location backend; python -m app.services.verify_ocr; Pop-Location`. Installing the Python `pytesseract` package alone is insufficient. OCR uses the local executable and works offline.

### Optional local invoice LLM (Ollama)

The app works with Ollama disabled; deterministic extraction runs on every supported format and never requires a model. To enable an entirely local model, install Ollama for Windows from the [official download page](https://ollama.com/download/windows), open a new PowerShell window, and download a small model:

```powershell
ollama --version
ollama pull qwen2.5:1.5b
ollama run qwen2.5:1.5b "Return JSON with the key ok set to true."
```

The Ollama library lists the 1.5B tag at about 986 MB; it is a reasonable starting point for a 6–8 GB GPU, with CPU fallback. Check the model's license and the [current Ollama model listing](https://ollama.com/library/qwen2.5) before use. Ollama runs its local service at `http://localhost:11434` by default. In `.env`, set:

```dotenv
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT_SECONDS=20
```

Restart both the API and worker after changing `.env`. Keep `OLLAMA_ENABLED=false` and `OLLAMA_MODEL=` to run without Ollama. Runtime model calls remain local; do not configure an Ollama cloud model or cloud endpoint for this zero-budget local assessment.

### Optional local semantic search

Keyword search works without model dependencies. Semantic/hybrid search returns a clear `503` when its optional model is unavailable. Install the pinned CPU-capable dependency and download the model once while online:

```powershell
pip install -r backend\requirements-embeddings.txt
python scripts\download_embedding_model.py
```

The script stores weights under `backend/data/models/all-MiniLM-L6-v2`; set `EMBEDDING_MODEL_NAME=backend/data/models/all-MiniLM-L6-v2` in `.env` (paths are resolved from the repository root). Models and embeddings stay local and are excluded from Git. The model loads lazily on first semantic query and runs on CPU to suit low-VRAM machines. No embedding API is used.

## Run the application

Use three PowerShell windows from the repository root.

**API:**

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

**Persistent background worker:**

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m app.worker
```

**Frontend:**

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Open <http://127.0.0.1:5173>. API docs are at <http://127.0.0.1:8000/docs>; readiness endpoints are `/health` and `/ready` (also `/api/health` and `/api/ready`). The API and worker initialize the same SQLite database. Upload returns after each original file and job have been committed; processing continues in the worker after the response. If the worker stops, queued jobs remain in SQLite and resume when it starts again. A claimed job with an expired lease is reclaimed after worker restart.

## REST API

All assessment endpoints are under `/api`.

- `POST /api/documents/upload` — multipart `files` upload; responds `202` with per-file accepted/rejected/duplicate statuses and IDs.
- `GET /api/documents` — paginated listing with invoice, supplier, customer, currency, date, processing, validation, and keyword filters.
- `GET /api/documents/{id}`, `/status`, `/history`, `/validation-errors`, `/invoice`, `/result` — stored metadata, status, audit history, checks, invoice JSON, or generated JSON download.
- `PUT /api/documents/{id}/invoice` — correct invoice fields and line items with optional reason; stores the original extraction, validates the updated record, and appends correction audit rows.
- `POST /api/documents/{id}/invoice/ai-suggestions` — when local Ollama is enabled, returns schema-validated, source-grounded proposals only for fields flagged for review. It never applies them. The reviewer selects proposals in the UI and explicitly saves them through the correction endpoint; the audit records the local model and acceptance.
- `GET /api/documents?correction_status=needs_review|ai_corrected|human_corrected|not_needed` — persisted views for documents needing attention and documents whose local-model suggestions a reviewer accepted.
- `GET /api/documents/{id}/result?view=original|corrected` — download the untouched extracted result or a JSON view assembled from the current corrected database result and correction audit.
- `POST /api/documents/{id}/retry` — requeues eligible failed jobs; permanently invalid sources and exhausted retry limits are rejected.
- `GET /api/search?query=...&mode=keyword|semantic|hybrid` — keyword search or real persisted-vector cosine ranking; hybrid combines semantic ranking with exact keyword matches. Search returns metadata only, never storage paths or raw source text.
- `POST /api/documents/compare` — compares two explicitly selected persisted invoice extractions when invoice identity evidence supports the pairing.

API errors use an `error` object with a stable code and safe message. Upload limits, supported extensions, signatures, PDF/TIFF page caps, row limits, timeouts, retries, and SQLite settings are configurable in `.env.example`. Originals live beneath `backend/data/uploads`; generated text/JSON are separate under `backend/data/processed`; SQLite is under `backend/data/careguide.db` by default. Do not commit `.env`, databases, uploads, model weights, virtual environments, or build output.

## Extraction, review, and validation

PDF pages are extracted incrementally with PyMuPDF; pages with little selectable text go to Pillow preprocessing and Tesseract OCR. Image OCR, all workbook sheets, legacy XLS and safe CSV parsing preserve source context. A per-file failure does not interrupt other files in a batch. The invoice extractor uses document text/tables and deterministic patterns, with optional local Ollama assistance. Prompts, schema checks, bounded retries, evidence grounding, and the difference between AI-assisted development and runtime AI are described in [docs/ai-prompts.md](docs/ai-prompts.md).

Invoice financial values use `Decimal`; `INVOICE_ROUNDING_TOLERANCE` defaults to an absolute `0.02`. Rules do not assume a tax system. Confidence values are heuristic quality indicators, not calibrated probabilities. Missing, conflicting, or low quality fields are surfaced for review. Duplicate invoice checks normalize the supplier and invoice number; identical totals alone do not trigger a match. See [docs/architecture.md](docs/architecture.md) and [docs/database-schema.md](docs/database-schema.md).

## Frontend workflow

The React workspace is a real API client. It supports multi-file upload with per-file feedback, paginated/filterable queue review, stored invoice/evidence/validation/history details, manual invoice and line-item corrections, optional evidence-grounded Ollama suggestions with reviewer approval, before/after comparison, original/corrected JSON downloads, eligible failed-job retries, metadata-only keyword/semantic/hybrid search, and comparison of two selected completed documents. Its Needs correction and AI corrected views are backed by persisted validation and correction history. It polls saved document/history state for updates and does not display invented progress percentages. Semantic and hybrid search need the optional local embedding model; model correction suggestions need Ollama; the rest of the frontend remains available without either. Vitest and Testing Library exercise upload, API error feedback, invoice review, and file-type validation.

## API request examples (PowerShell)

The API schema is available at <http://127.0.0.1:8000/docs>. These commands use the real local service and the synthetic fixtures. `curl.exe` is used for multipart uploads because it supports repeated `files` fields directly in Windows PowerShell.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/documents/upload `
  -F "files=@evaluation/dataset/valid_invoice.pdf" `
  -F "files=@evaluation/dataset/spreadsheet_valid.xlsx" `
  -F "files=@evaluation/dataset/missing_tax.csv"
```

The response shape is `{"accepted_count":3,"rejected_count":0,"items":[...]}`. Each accepted item includes a server-generated `document_id`, `job_id`, the original display `filename`, `status:"queued"`, SHA-256, and null error/duplicate fields. IDs and hashes are assigned at runtime. Rejected files have a per-item error code and safe message.

Use a returned ID (the following example assumes `$documentId` contains one):

```powershell
$documentId = 1 # replace with an ID returned by upload
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId" | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/invoice" | ConvertTo-Json -Depth 12
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/history" | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/validation-errors" | ConvertTo-Json -Depth 8
Invoke-WebRequest "http://127.0.0.1:8000/api/documents/$documentId/result?view=original" -OutFile .\original-result.json
Invoke-WebRequest "http://127.0.0.1:8000/api/documents/$documentId/result?view=corrected" -OutFile .\corrected-result.json
```

List with filters and keyword search:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/documents?page=1&page_size=25&supplier=North%20Clinic&status=completed&validation_status=valid"
Invoke-RestMethod "http://127.0.0.1:8000/api/search?query=CG-EVAL-1001&mode=keyword&page=1&page_size=25" | ConvertTo-Json -Depth 8
```

Correct a header field or a line item. The response is the persisted `InvoiceResultRead`; the prior and corrected values are also visible at `/invoice/corrections` and in processing history.

```powershell
$body = @{
  invoice_number = "CG-EVAL-1001"
  reason = "Verified against the source document"
  corrected_by = "Local reviewer"
} | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:8000/api/documents/$documentId/invoice" -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 12
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/invoice/corrections" | ConvertTo-Json -Depth 8
```

Optional local Ollama review returns suggestions without changing the saved invoice. The frontend displays each proposal with its source snippet; stage selected values in the form and explicitly save them to record accepted corrections.

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/documents/$documentId/invoice/ai-suggestions" | ConvertTo-Json -Depth 10
```

Compare two selected stored results, retry eligible failures, or check readiness:

```powershell
$compare = @{ document_a_id = 1; document_b_id = 2 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/documents/compare -ContentType "application/json" -Body $compare | ConvertTo-Json -Depth 12
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/documents/$documentId/retry"
Invoke-RestMethod http://127.0.0.1:8000/api/ready | ConvertTo-Json -Depth 8
```

The comparison response contains `comparable`, `review_recommended`, `matched_fields`, `mismatched_fields`, `missing_fields`, and `reasons`. When invoice identity cannot support a pairing, it explicitly returns that the selected documents are not comparable.

Representative completed invoice response (abbreviated; the API emits additional provenance/validation fields and runtime IDs/timestamps vary):

```json
{
  "document_id": 1,
  "vendor_name": "North Clinic",
  "customer_name": "Care Guide",
  "invoice_number": "CG-EVAL-1001",
  "invoice_date": "2026-05-10",
  "currency": "USD",
  "subtotal": "100.00",
  "tax_amount": "10.00",
  "total_amount": "110.00",
  "extraction_method": "deterministic",
  "line_items": [
    {"description": "Care visit", "quantity": "2", "unit_price": "50.00", "amount": "100.00", "position": 0},
    {"description": "Diagnostic kit", "quantity": "1", "unit_price": "0.00", "amount": "0.00", "position": 1}
  ],
  "validation_errors": []
}
```

## Synthetic evaluation

Run the real parsers, SQLite-backed ingestion service and worker against the labeled fixtures:

```powershell
python scripts\create_evaluation_dataset.py
python scripts\evaluate.py
python scripts\evaluate.py --benchmark-pages 1000
```

Each run saves a timestamped JSON file in `evaluation/results/`. The committed report [evaluation-20261028T203950Z.json](evaluation/results/evaluation-20261028T203950Z.json) was generated in this workspace. It records 54/54 exact matches (1.0) across fields with available local dependencies, 8/8 expected validation/duplicate checks, 9/10 successful processing outcomes (the tenth is the deliberately corrupt PDF), and one expected file failure. Tesseract is unavailable in this environment; the three OCR-dependent fixtures are explicitly listed as dependency-skipped and do not inflate those field or validation denominators. This is not an OCR accuracy result. The optional 1,000-page local digital-text PDF benchmark reported 1,000 pages processed in 0.0852 seconds in that run; this timing is machine-specific and is neither an OCR benchmark nor a concurrency claim. Inspect the report for detailed per-field/case results.

The dataset contains synthetic ground-truth labels for digital PDF and XLSX invoices, CSV extraction/validation cases, a deliberate duplicate, an uncertain invoice identifier, a scanned JPG/PDF pair, and a corrupted PDF. `ground_truth.json` documents null, unreadable, dependency-skip, and expected-validation handling. No real customer or vendor data is included.

Run backend and frontend checks:

```powershell
python -m pytest backend\tests -q -rs
Set-Location frontend
npm.cmd test -- --reporter=dot
npm.cmd run build
Set-Location ..
```

The real Tesseract integration test is skipped only when the configured local executable is unavailable; install it and rerun to measure OCR. The local model integration test for semantic search is similarly conditional on model dependencies/weights. The long PDF measurement is opt-in so regular tests stay quick.

## Walkthrough

See [docs/walkthrough-script.md](docs/walkthrough-script.md) for a chronological six-minute assessment demo, example API calls, manual correction review, comparison, failure handling, evaluation, and reset steps.

## Local demonstration limits

This assessment implementation has a persistent single-machine SQLite queue and is intended for local demonstration and modest serial workloads. Multiple worker processes can claim distinct jobs transactionally, but SQLite write contention, one local embedding model, per-process inference, local disk, and no distributed queue limit throughput. It is not a claim of thousands of concurrent documents. A production scale-out would use a server database, dedicated queue/broker, horizontally scaled workers, object storage, centralized observability, and a managed/vector search service selected for deployment needs.

## Tests

```powershell
python -m pytest backend\tests -q -rs
```

Tests generate local fixtures for digital and scanned PDF, image, TIFF, XLSX, XLS and CSV; API tests use temporary SQLite databases. Real OCR integration tests run only when the configured Tesseract executable is available and otherwise show an explicit skip. Keyword API tests do not need model weights. Semantic unit tests use deterministic mocked embeddings to verify actual vector ranking and persistence; the optional local-model integration path is skipped if sentence-transformers/model weights are absent.
