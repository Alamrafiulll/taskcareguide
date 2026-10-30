# Architecture

## Runtime components

The local application has three independent processes: a React/Vite frontend, a FastAPI API process, and a persistent SQLite-backed worker. The API stores upload bytes and creates a document plus queued job in one request; it does not wait for OCR or extraction. The worker claims work transactionally, runs the existing format-specific processors, extracts invoice data, applies validation, and persists results. The worker commits progress and lease heartbeats so a restart does not silently discard queued work.

```text
React/Vite -> FastAPI -> SQLite (documents, jobs, audit/history, results)
                         |                           ^
                         +-> uploads/ (original)      |
                         +-> processed/ (JSON/text)   |
                                                     |
                                   SQLite worker <---+
                                     |-> PyMuPDF page processing / OCR with Pillow + local Tesseract
                                     |-> pandas/openpyxl/xlrd/CSV parsers
                                     |-> deterministic invoice extraction, optional Ollama or Gemini
                                     +-> optional local sentence-transformers vectors for search
```

## Upload and processing

`POST /api/documents/upload` accepts multiple files. Each upload is copied in bounded chunks to a generated UUID-based name; the original submitted name is display metadata only. File size, extension, and practical content signatures are validated before a `documents` row, a `processing_jobs` row, and upload history are committed. The batch isolates each file: invalid items have per-file errors while valid items are queued. SHA-256 supports exact-byte duplicate identification.

Jobs transition through `queued -> running -> completed|failed`. Claim uses a conditional database update so concurrent worker processes cannot claim the same queued row. A lease expiry allows a later worker to recover abandoned work. Transient errors use bounded attempts and backoff; permanent file validation failures are not retried. An operator can explicitly requeue a failed nonpermanent job through the retry endpoint, subject to the configured retry limit. State, errors, progress and retries are persisted in SQLite. A queued job survives API or worker restart because it is not in-memory task state.

The worker processes a single job at a time per process. SQLite uses WAL, a busy timeout, and short committed state transitions. These choices are suitable for a local assessment, not thousands of concurrent documents. Higher throughput needs a server database, external durable queue, scalable object storage and separately scaled workers.

## Processors and output

The shared processing service dispatches PDF, image, Excel, and CSV implementations. PyMuPDF handles digital text extraction by page and renders only pages that need OCR. Pillow preprocessing and local Tesseract handle scanned PDF pages and images. Pandas plus openpyxl/xlrd extract each worksheet with source coordinates; CSV parsing tolerates encoding and row-shape variations while reporting warnings. Resource caps and timeouts are configured through environment settings.

Every completed processing job writes a normalized result JSON and text file outside the original upload directory. It records source document ID/name, format/status, page or worksheet context, segments/table information, OCR use, warnings/errors, timestamps and processing metadata. Invoice extraction then adds invoice fields, line items, source evidence, method/status/confidence metadata, and structured financial findings. Financial values persist as SQL `NUMERIC`/`Decimal`. An extraction failure is stored on the file/job/history and does not stop sibling upload items.

## REST and review

The `/api/documents` routes provide upload, paginated/filterable list, metadata/status, invoice data, original-format source download, generated reviewed-summary PDF, original/corrected JSON downloads, processing history, validation findings, manual corrections, failed job retry, safe removal, and two-document comparison. Comparison is presented inline on the frontend Documents page. Removal refuses active processing and deletes the source and derived files plus cascaded database records. `/api/search` exposes keyword, semantic and hybrid modes. Manual corrections append previous/corrected values, reason, reviewer, source, and timestamp; they update the current result but retain the original extraction JSON and trigger validation again.

When enabled, `POST /api/documents/{id}/invoice/ai-suggestions` sends stored extracted evidence to the single provider selected by `AI_PROVIDER`. Ollama runs locally. Gemini sends up to 60,000 characters of extracted text to Google's HTTPS API and uses a key read from `GEMINI_API_KEY`; the key is sent in a header and is not logged. Pydantic validation, bounded retries, and evidence grounding apply; only fields already flagged for review are returned. The endpoint does not persist or apply suggestions. The user chooses suggestions in the frontend and saves them through the regular invoice correction request. The correction row and history event identify provider/model. `correction_status` filters and the frontend AI corrected view are derived from those persisted rows. The result download endpoint composes current corrected invoice fields, validation findings, and audit rows from SQLite; the original view returns the generated extraction JSON unchanged.

Comparison loads stored invoice fields and line items for the selected IDs. It recommends review and does not claim a match if invoice identity evidence is insufficient. Search returns document metadata and scores only; internal indexed source text and filesystem paths are not returned.

## Browser client

The Vite client talks to the versioned `/api` routes through one fetch/error adapter. Its document view combines a multi-file picker/drop target, per-file response statuses, server-filtered pagination, selected-document details and invoice correction form. The selected detail loads the invoice, validation findings, processing history, and correction audit from separate endpoints. A timer refreshes persisted state while preserving in-progress form edits. Search and comparison views call the corresponding API operations; they do not parse documents or synthesize results in the browser. The frontend test suite mocks HTTP responses and checks interactions/error handling, while backend API tests exercise the real routes with a temporary database.

## Evaluation workflow

`scripts/create_evaluation_dataset.py` creates the small synthetic PDF/image/CSV fixtures and ground-truth manifest; the XLSX fixture is a static, locally authored workbook. `scripts/evaluate.py` queues those files through the same ingestion service and SQLite worker used by the application, then scores stored extraction and validation results against the manifest. It writes a timestamped JSON report with environment/dependency status, per-field exact matches, processing failures, timing, validation checks and explicit OCR-dependent skips. `--benchmark-pages 1000` generates and processes a digital-text PDF incrementally; its timing applies only to the current machine and does not represent scanned OCR or concurrent-load capacity.

## Search

Keyword search uses a private truncated text index plus invoice metadata filters. Semantic search is implemented using local sentence-transformers embeddings (all-MiniLM-L6-v2 by default) and cosine similarity. Model weights load lazily and use CPU by default. Each document vector is persisted with a model identifier and source-text hash, then regenerated when text changes. Semantic search returns `503` if the optional package/weights are unavailable; keyword search remains usable. Hybrid mode ranks by semantic similarity and adds a bounded bonus for keyword matches. The API caps the candidate corpus and result pagination.

## Errors and readiness

HTTP validation and application errors use a JSON `error` object with a stable code and safe message. Unhandled exceptions are logged with stack details server-side and return a generic message. Processing failures are saved to `processing_jobs`, `documents`, and `processing_history`, with permanent file-validation failures distinguished from transient errors. Readiness reports database availability, most recent worker heartbeat, OCR executable availability, and whether the selected AI provider is configured; optional dependency absence degrades readiness rather than preventing startup.

## Invoice extraction and privacy

The existing deterministic extraction and financial validation modules remain the source of invoice behavior. Ollama is optional and local. Gemini is separately opt-in and transmits extracted evidence to Google's API; unpaid Gemini usage has different data handling terms and should only be used with synthetic documents for this assessment. Both providers have Pydantic validation, bounded retries, and evidence grounding. Runtime prompts are in [ai-prompts.md](ai-prompts.md). AI-assisted development means a human uses an AI coding tool to write code; runtime AI is the application's optional model call at execution time. These are separate activities.

Original bytes remain under `uploads/`; processed text/JSON and search indexes are separate. The API does not expose unrestricted raw contents, credentials, or stored paths. `.env`, local database files, uploads, model weights, and generated outputs are ignored by Git.
