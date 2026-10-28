# Care Guide — Document Intelligence

A local-first document ingestion and text-extraction foundation. It processes PDFs, images, Excel workbooks and CSV files locally. The API does not require an LLM, paid service, API key, or Internet connection during document processing.

## Requirements

- Windows 11, Python 3.10+, Node.js 20+
- Tesseract OCR executable for scanned PDFs and image OCR (free local Windows installer; instructions below)
- About 50 GB free disk is recommended for future local models and documents
- PyMuPDF, Pillow, pandas, openpyxl and xlrd are installed from the pinned Python requirements

## Setup (Windows PowerShell)

From the repository root:

```powershell
Copy-Item .env.example .env
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements-test.txt
```

The test requirements include `xlwt` only to generate temporary legacy `.xls` fixtures. Production `.xls` reading uses `xlrd`.

### Install Tesseract OCR on Windows

The Tesseract project documentation points Windows users to the UB Mannheim builds because it does not distribute a current official Windows installer. Download and run a current Windows installer from the [UB Mannheim Tesseract project](https://github.com/UB-Mannheim/tesseract/wiki), and include English language data (`eng.traineddata`). The [Tesseract downloads documentation](https://tesseract-ocr.github.io/tessdoc/Downloads.html) describes the Windows installer source.

After installation, open a new PowerShell window and verify the executable and language data:

```powershell
tesseract --version
tesseract --list-langs
```

If `tesseract` is not on `PATH`, set the full executable path in `.env`:

```dotenv
TESSERACT_CMD="C:/Program Files/Tesseract-OCR/tesseract.exe"
```

Verify the configured executable from the repository root:

```powershell
Push-Location backend
python -m app.services.verify_ocr
Pop-Location
```

This check verifies the real Tesseract binary and reports its version. The OCR integration tests create a scanned PDF and image and run real OCR when Tesseract is available. If it is not installed/configured, pytest reports those cases as skipped; installing the Python `pytesseract` wrapper alone does not enable OCR.

## Start the application

Start the API from the repository root:

```powershell
\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

In another PowerShell window, start the React/Vite client:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open <http://127.0.0.1:5173>. The API is at <http://127.0.0.1:8000>; interactive docs are at <http://127.0.0.1:8000/docs>. Health and readiness are `/health` and `/ready`.

## Ingest documents

`POST /documents/ingest` accepts a multipart batch in the `files` field. Supported extensions are PDF, JPG/JPEG, PNG, TIFF, XLSX, XLS, and CSV. Each file receives its own result; a bad file does not stop later files in the same request.

```powershell
curl.exe -F "files=@C:\path\to\invoice.pdf" -F "files=@C:\path\to\claims.xlsx" http://127.0.0.1:8000/documents/ingest
```

The response contains normalized per-document output: page or sheet text segments, cell coordinates/table values, page and worksheet counts, OCR usage/confidence, warnings, errors, processing timestamps and duplicate hash information. Accepted original files are stored under `backend/data/uploads`; extracted text and JSON are stored separately under `backend/data/processed`. SQLite records the document/job status, hash, normalized extraction result, errors and progress history. Amount-like spreadsheet cells remain their source values (for example, decimal text is not converted to binary floating-point by the CSV parser).

The endpoint processes files synchronously and limits a batch to 20 files by default. It streams each upload to disk in chunks, caps a file at 50 MiB, caps PDF/TIFF pages at 1,000, image frames at 50 million pixels, XLSX expansion at 250 MiB, spreadsheet rows at 100,000 per sheet and CSV rows at 250,000. These limits and processing/OCR timeouts are configurable in `.env`; see `.env.example`.

## Tests

```powershell
python -m pytest backend\tests -q -rs
```

Tests create fixtures locally for PDFs, images, TIFFs, XLSX, XLS and CSV. PDF and legacy XLS fixture tests skip with an explicit reason if PyMuPDF or the fixture/reader dependency is absent. Scanned-PDF and image OCR integration tests skip unless the configured Tesseract executable can be run; they do not mock OCR success.

## Configuration, storage and scope

Copy `.env.example` to `.env`. SQLite defaults to `backend/data/careguide.db` when started from the repository root. Original files and derived outputs use separate directories. Local databases, uploaded files, generated processing output, `.env`, virtual environments and build artifacts are excluded by `.gitignore`.

Ollama remains optional and disabled. The current implementation covers ingestion, local text extraction/OCR, spreadsheet context, persisted status/results and health/readiness. It does not implement an asynchronous worker queue, document review UI, invoice-field extraction/validation, authentication, or correction endpoints. The next stage is a review workflow that presents extracted pages/tables and lets users validate and correct invoice fields while recording correction history.
