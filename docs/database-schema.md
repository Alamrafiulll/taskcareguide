# Database schema

SQLite is the default local store. SQLAlchemy initializes the tables and applies additive column migrations needed by earlier assessment versions. SQLite connections enable foreign keys, WAL journaling, `synchronous=NORMAL`, and a configured busy timeout. Transactions for upload acceptance, claims, progress, completion/failure, corrections, and retry state are committed explicitly.

## Tables and relationships

| Table | Purpose and key relationships |
| --- | --- |
| `documents` | One row per accepted upload; generated stored filename, display filename, MIME type, byte count, SHA-256, lifecycle status and safe error details. One-to-many with jobs/history/corrections; one-to-one with invoice/search index. |
| `processing_jobs` | Persistent queue row attached to a document. Tracks queued/running/completed/failed state, retry and attempt counts, worker ID, lease expiry, next retry, permanent failure, start/completion times, and error details. |
| `invoice_results` | One extracted invoice result per document: header fields, exact-decimal monetary columns, extraction method, field confidence, and normalized extraction JSON. |
| `line_items` | Ordered child rows of invoice results with description, exact-decimal quantity/unit price/amount and source context in the normalized result JSON. |
| `validation_errors` | Explainable findings attached to an invoice result: severity, code, field, explanation, expected/observed values and timestamp. Replaced when a correction triggers a fresh full validation pass. |
| `processing_history` | Append-oriented event log for upload, claim/start, progress, completion, error, retry request and manual review/correction, with timestamps and event metadata. |
| `manual_corrections` | Audit entries tied to documents, including field, prior value, corrected value, reviewer/reason, correction source (`human`, legacy `local_model`, or reviewer-accepted `ai_model`), and creation time. Current values are updated, but original extracted JSON is retained. |
| `worker_heartbeats` | Latest worker liveness and currently claimed job, used by readiness reporting and abandoned lease recovery. |
| `document_search_index` | Private bounded text index used for keyword matching. Search endpoints never return this raw text. |
| `document_embeddings` | One local JSON vector per document/model with source-text hash and timestamp. Updated when indexed text changes; used for cosine similarity, not an external service. |

The document owns uploaded bytes by reference through a generated name under the configured upload directory. `original_path` is internal metadata and is not returned by API responses. Processed JSON and text files are stored separately, and the JSON download route resolves only a safe generated result path. Database records do not duplicate raw document contents into public response schemas.

## Job lifecycle and failure handling

| State | Meaning |
| --- | --- |
| `queued` | Durable work is waiting for a worker. |
| `running` | A worker atomically claimed the job; lease and progress are maintained in SQLite. |
| `completed` | Extraction and persistence completed. Invoice findings may still recommend human review. |
| `failed` | The bounded automatic attempts ended or validation found a permanent source-file problem. Error detail and attempt history remain stored. |

Each claim increments `attempt_count`. Transient exceptions are retried with bounded backoff until `WORKER_MAX_ATTEMPTS`; then the job becomes failed. File safety errors set `permanent_failure`, preventing retry of unchanged invalid source data. `POST /api/documents/{id}/retry` explicitly requeues eligible failed jobs and increments `retry_count`; missing/corrupt stored sources are not queued. Worker leases permit recovery after process exit without deleting the queued record. History rows explain transitions.

`DELETE /api/documents/{id}` can cancel a queued job before removing the document, but refuses a job already running. On success it removes the source upload, processed JSON/text files, and database rows through relationship and SQLite foreign-key cascades. A deletion is permanent in this local demonstration; production systems would normally add authorization and a retention/soft-delete policy.

## Monetary precision and correction audit

Amounts use SQL `NUMERIC` and Python `Decimal`. Validation tolerance is a configurable absolute decimal amount and each finding stores its expected/observed values and explanation. Corrections add immutable audit rows and a history entry, mark corrected field status, update the current invoice row, preserve the `original_invoice` JSON snapshot, and rerun all relevant financial rules. This is an assessment audit trail, not a multi-tenant authorization system.
