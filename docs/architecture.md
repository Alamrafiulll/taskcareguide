# Architecture

The system is local-first: a React/Vite client calls FastAPI on localhost. FastAPI receives multipart uploads and hands each file to a format-specific processor selected by extension after checking its size and content signature. The supported processors are PyMuPDF PDF, Pillow/Tesseract image, pandas Excel, and Python CSV.

```text
React/Vite -> FastAPI /documents/ingest -> safe staging + hash/signature validation
                                           |-> PDF pages -> PyMuPDF text -> OCR only sparse pages
                                           |-> images -> Pillow preprocessing -> Tesseract OCR
                                           |-> XLS/XLSX/CSV -> sheet/row and cell-aware extraction
                                           +-> SQLite status/history/result via SQLAlchemy
                                           +-> uploads/ (original bytes)
                                           +-> processed/ (normalized JSON and text)
```

Each batch item is isolated. Files are copied to generated UUID-based names in bounded chunks; submitted names are retained only as display metadata. Hashes are stored for duplicate detection. Originals remain separate from derived text and JSON. Extraction output records source ID/name, format, status, page and sheet counts, segments, table cells, OCR use/confidence, warnings/errors, timestamps and hash.

PDF processing is page-by-page and renders only a sparse-text page to an in-memory image before releasing it. A configured page cap, upload cap, row caps, whole-document deadline and per-OCR call timeout bound resource use. Periodic progress events are persisted in `processing_history`. Tesseract is optional for app startup; scanned pages produce page-scoped errors if it is unavailable. No network or LLM call is part of processing.

SQLAlchemy transactions commit state transitions and roll back failures. The existing `documents`, `processing_jobs`, `invoice_results`, and `processing_history` tables store ingestion status/results. Spreadsheet data remains in the normalized `invoice_results.raw_extraction` JSON rather than introducing parallel document tables. Monetary database columns continue to use `Decimal`/`NUMERIC`.

The synchronous ingestion endpoint currently returns only after the batch finishes. The next stage is a review workflow and later, if required, a persistent background worker for long-running batches.
