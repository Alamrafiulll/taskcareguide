# Care Guide — Document Intelligence

A local-first invoice and document processing application. FastAPI accepts multiple uploads into a persistent SQLite queue; a separate worker extracts PDF, image, spreadsheet, and CSV content, extracts invoice fields, and records explainable financial checks. React/Vite provides the local frontend. Core processing uses no paid API, API key, or internet service.

## Requirements

- Windows 11, Python 3.10+, Node.js 20+
- Tesseract OCR executable for scanned PDFs and image OCR (optional for digital PDFs/spreadsheets)
- Optional: Ollama and a local model for assisted invoice extraction
- Optional: sentence-transformers and a downloaded all-MiniLM-L6-v2 model for semantic/hybrid search

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
- `POST /api/documents/{id}/retry` — requeues eligible failed jobs; permanently invalid sources and exhausted retry limits are rejected.
- `GET /api/search?query=...&mode=keyword|semantic|hybrid` — keyword search or real persisted-vector cosine ranking; hybrid combines semantic ranking with exact keyword matches. Search returns metadata only, never storage paths or raw source text.
- `POST /api/documents/compare` — compares two explicitly selected persisted invoice extractions when invoice identity evidence supports the pairing.

API errors use an `error` object with a stable code and safe message. Upload limits, supported extensions, signatures, PDF/TIFF page caps, row limits, timeouts, retries, and SQLite settings are configurable in `.env.example`. Originals live beneath `backend/data/uploads`; generated text/JSON are separate under `backend/data/processed`; SQLite is under `backend/data/careguide.db` by default. Do not commit `.env`, databases, uploads, model weights, virtual environments, or build output.

## Extraction, review, and validation

PDF pages are extracted incrementally with PyMuPDF; pages with little selectable text go to Pillow preprocessing and Tesseract OCR. Image OCR, all workbook sheets, legacy XLS and safe CSV parsing preserve source context. A per-file failure does not interrupt other files in a batch. The invoice extractor uses document text/tables and deterministic patterns, with optional local Ollama assistance. Prompts, schema checks, bounded retries, evidence grounding, and the difference between AI-assisted development and runtime AI are described in [docs/ai-prompts.md](docs/ai-prompts.md).

Invoice financial values use `Decimal`; `INVOICE_ROUNDING_TOLERANCE` defaults to an absolute `0.02`. Rules do not assume a tax system. Confidence values are heuristic quality indicators, not calibrated probabilities. Missing, conflicting, or low quality fields are surfaced for review. Duplicate invoice checks normalize the supplier and invoice number; identical totals alone do not trigger a match. See [docs/architecture.md](docs/architecture.md) and [docs/database-schema.md](docs/database-schema.md).

## Local demonstration limits

This assessment implementation has a persistent single-machine SQLite queue and is intended for local demonstration and modest serial workloads. Multiple worker processes can claim distinct jobs transactionally, but SQLite write contention, one local embedding model, per-process inference, local disk, and no distributed queue limit throughput. It is not a claim of thousands of concurrent documents. A production scale-out would use a server database, dedicated queue/broker, horizontally scaled workers, object storage, centralized observability, and a managed/vector search service selected for deployment needs.

## Tests

```powershell
python -m pytest backend\tests -q -rs
```

Tests generate local fixtures for digital and scanned PDF, image, TIFF, XLSX, XLS and CSV; API tests use temporary SQLite databases. Real OCR integration tests run only when the configured Tesseract executable is available and otherwise show an explicit skip. Keyword API tests do not need model weights. Semantic unit tests use deterministic mocked embeddings to verify actual vector ranking and persistence; the optional local-model integration path is skipped if sentence-transformers/model weights are absent.
