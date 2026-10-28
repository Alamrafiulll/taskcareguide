# Database schema

The ingestion pipeline reuses the first-stage SQLAlchemy tables; it does not add a duplicate document or extraction model.

| Table | Ingestion use |
| --- | --- |
| `documents` | Original filename, generated stored name/path, MIME type, byte size, SHA-256, lifecycle status and useful error details |
| `processing_jobs` | Queued/running/completed/failed processing state, retry counter and start/completion timestamps |
| `invoice_results` | Extracted-text path and normalized JSON output, including pages/sheets, tables, metadata, OCR results, warnings and errors |
| `processing_history` | Upload, processing-started, periodic progress, completion and failure events |
| `line_items`, `validation_errors`, `manual_corrections` | Reserved existing invoice review structures for a later review stage |

Document and job state changes are committed at upload, start, progress checkpoints and completion/failure. Original bytes are held beneath the configured upload directory; output JSON/text are held under a separate processed directory. `sha256` supports duplicate identification without storing a second original. Spreadsheet cell values and coordinates are kept in extraction JSON; no binary floats are used for the invoice model's money columns.
