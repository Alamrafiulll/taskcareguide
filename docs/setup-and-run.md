# Invoice Insight: Windows Setup and Run Guide

This guide is for Windows 11 and PowerShell. It covers what to install, one-time setup, starting the three app processes, and a first end-to-end test.

## What the system runs

- **API:** FastAPI on `http://127.0.0.1:8000`.
- **Worker:** A separate process that reads queued documents from SQLite and performs extraction.
- **Web app:** React + Vite on `http://127.0.0.1:5173`.
- **Database:** Local SQLite file under `backend/data/` by default.
- **OCR:** Local Tesseract executable. Optional for digital PDFs and spreadsheets; required to OCR scans and image-only files.
- **AI:** Optional. Deterministic extraction works without an AI model. Ollama runs locally; Gemini is a cloud option and sends extracted text to Google.

The API and worker must both be running for uploads to finish processing. Keep three PowerShell windows open, one for each process.

## 1. Install prerequisites

Install these before setting up the repository:

1. **Python 3.10 or newer, 64-bit.** The project was developed and verified with Python 3.10. Enable the Python launcher during installation. [Download Python for Windows](https://www.python.org/downloads/windows/).
2. **Node.js 20 or newer.** This includes npm. Node 22 LTS is suitable. [Download Node.js](https://nodejs.org/en/download).
3. **Git for Windows** if you need to clone the Git repository. [Download Git](https://git-scm.com/download/win).
4. **Tesseract OCR** if you need scanned-PDF and image OCR. Install a Windows build with English language data from the [UB Mannheim Tesseract project](https://github.com/UB-Mannheim/tesseract/wiki).
5. **Ollama** only if you want optional local AI suggestions. [Download Ollama for Windows](https://ollama.com/download/windows). The model is a separate download; see Step 5.

Allow disk space for Python and Node packages, uploaded files, generated data, and any model weights. A working Internet connection is needed during package/model installation; the core document parsers and Ollama inference work locally after setup.

Open a **new** PowerShell window after installing software and check Python and Node:

```powershell
py -0p
node --version
npm.cmd --version
git --version
```

If `py`, `node`, `npm.cmd`, or `git` is not recognized, finish that installer, reopen PowerShell, and try again.

## 2. Open or clone the repository

If the repository is already on your computer, change to its root folder. For this machine:

```powershell
Set-Location C:\Users\rafiu\OneDrive\Desktop\careguide
```

For a fresh checkout, use the repository URL and then enter its folder:

```powershell
git clone https://github.com/Alamrafiulll/taskcareguide.git careguide
Set-Location .\careguide
```

The root folder should contain `backend`, `frontend`, `scripts`, `.env.example`, and `README.md`.

## 3. Create local configuration and the Python environment

Create `.env` from the example only if you do not already have one. This protects existing local settings:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Create and activate the virtual environment, then install the pinned backend and test dependencies:

```powershell
if (-not (Test-Path .venv)) { py -3.10 -m venv .venv }
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements-test.txt
```

The `requirements-test.txt` file includes test-only fixture support; the base runtime dependencies are pinned in `backend/requirements.txt`. Do not remove `.venv` from `.gitignore` or commit it.

## 4. Install frontend packages

From the repository root:

```powershell
Set-Location frontend
npm.cmd ci
Set-Location ..
```

Use `npm.cmd` in PowerShell to avoid script execution-policy issues with `npm.ps1`.

## 5. Configure optional OCR and AI

### Tesseract OCR (scanned files)

Check that Tesseract is installed and visible to PowerShell:

```powershell
Get-Command tesseract -ErrorAction SilentlyContinue
tesseract --version
tesseract --list-langs
```

The language list should include `eng`. If Tesseract is installed but not on `PATH`, edit `.env` and set `TESSERACT_CMD` to the actual full path of `tesseract.exe`, for example:

```dotenv
TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
```

Check that the app can invoke the executable:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m app.services.verify_ocr
```

If the path does not exist, find where the installer placed Tesseract and correct `.env`. Installing the Python `pytesseract` package alone does not install the Tesseract program.

### Ollama (optional local AI)

Skip this section to use deterministic extraction only. If Ollama is installed, download a small model once:

```powershell
ollama --version
ollama pull qwen2.5:1.5b
ollama list
```

When `ollama list` shows `qwen2.5:1.5b`, edit `.env` and set:

```dotenv
AI_PROVIDER=ollama
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT_SECONDS=60
```

Ollama normally starts its local service on Windows. If `ollama list` says it cannot connect, start the Ollama application from the Start menu. If it is not already running as a background service, run `ollama serve` in its own PowerShell window and leave it open. The application sends invoice text to this local service; no Gemini key is needed.

The app asks Ollama for JSON using the API's JSON mode. A basic `ollama run` prompt may answer conversationally, which does not mean app extraction is broken.

### Gemini (optional cloud AI)

Choose this only if you intentionally want cloud inference. Create a key in [Google AI Studio](https://aistudio.google.com/app/apikey), keep it in the ignored local `.env` file, and configure:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_TIMEOUT_SECONDS=30
```

The key must never be added to source code, frontend files, screenshots, documentation, or Git. The Gemini provider sends extracted document text to Google. Google states that content on unpaid API usage may be used to improve products and may be reviewed, so use only synthetic invoices with its free tier. See [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) and [Gemini API terms](https://ai.google.dev/gemini-api/terms). `AI_PROVIDER` selects one provider; `none` keeps AI off, `ollama` selects local Ollama, and `gemini` selects cloud Gemini.

## 6. Start the system

Start these processes in **three separate PowerShell windows**, each opened at the repository root. Activate `.venv` in both Python windows.

### Window 1: API

```powershell
Set-Location C:\Users\rafiu\OneDrive\Desktop\careguide
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

The API initializes the SQLite tables if needed. Open API documentation at <http://127.0.0.1:8000/docs>.

### Window 2: Background worker

```powershell
Set-Location C:\Users\rafiu\OneDrive\Desktop\careguide
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m app.worker
```

Leave this window open. It claims queued jobs from SQLite and processes uploads after the API has returned the upload response.

### Window 3: Frontend

```powershell
Set-Location C:\Users\rafiu\OneDrive\Desktop\careguide\frontend
npm.cmd run dev
```

Open <http://127.0.0.1:5173> in your browser.

## 7. Check that the app is ready

In a fourth PowerShell window, from the repository root, run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready | ConvertTo-Json -Depth 5
```

The worker check should say `ok`. `ai_assistant` says `enabled` when the selected provider is configured. OCR may report optional/unavailable when Tesseract is not installed; digital PDFs and spreadsheets can still be processed.

## 8. Try the synthetic invoice samples

Use the included sample files; they contain synthetic data, not real vendor or customer records. In the UI:

1. Upload `test sample/01_valid_invoice.pdf` and `test sample/02_matching_invoice.xlsx`.
2. Keep the API, worker, and frontend windows open until both show **Completed**.
3. Select a document to inspect extracted fields, line items, validation findings, and evidence.
4. Compare the PDF and spreadsheet by selecting both completed documents in the comparison section.
5. For an invoice flagged for review, inspect the source evidence. Optionally request AI suggestions, choose the ones you agree with, then explicitly save your correction.

AI suggestions are proposals. They do not silently modify or replace the stored invoice. Original uploaded files are unchanged. Use the original download to retrieve the source, or the reviewed PDF download to export a PDF summary of the currently saved invoice fields.

## Optional checks

With the virtual environment active and working directory at the repository root:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m pytest backend\tests -q -rs
```

Frontend tests and production build:

```powershell
Set-Location frontend
npm.cmd test -- --reporter=dot
npm.cmd run build
Set-Location ..
```

## Stop the system

Press **Ctrl+C** in each of the API, worker, and frontend windows. Ollama can remain open or be closed separately.

## Common problems

- **PowerShell says running scripts is disabled:** run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that window, then activate the virtual environment again.
- **`python` imports cannot find `app`:** use the API command above, which includes `--app-dir backend`; set `PYTHONPATH` before running the worker.
- **Upload remains queued:** confirm the worker window is running and check `/ready` for its heartbeat.
- **Scanned document has an OCR error:** run `tesseract --version`; then set `TESSERACT_CMD` to the actual executable path in `.env` and restart the API/worker.
- **AI suggestions are disabled:** check the provider/model settings in `.env`, confirm `ollama list` contains the configured model (or Gemini key is configured), and restart the API and worker after editing `.env`.
- **Frontend cannot reach API:** check that the API window is running on port 8000 and open <http://127.0.0.1:8000/health>.
- **A sample file is rejected:** check the per-file error in the UI. A deliberately corrupt sample is expected to fail; other files in the batch should still process.

## Local data and security

Uploads, processed text/JSON, the SQLite database, and downloaded local models are stored under `backend/data/` by default. `.env`, databases, uploads, model weights, `.venv`, and frontend build output are ignored by Git. Do not commit keys, real invoices, uploaded documents, or local database files.
