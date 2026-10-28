# Assessment walkthrough script (about 6 minutes)

Run the API, worker, and frontend in three PowerShell windows using the commands in the root README. Keep all documents in `evaluation/dataset`; they are synthetic. Start with `/api/ready` and mention that OCR is optional and unavailable on this assessment machine unless the response reports it as ready.

## 0:00–0:45 — Start-up and readiness

Open `http://127.0.0.1:5173`, show the API and worker indicators, and open `http://127.0.0.1:8000/docs`. Explain that upload acceptance is committed to SQLite before the HTTP response and that the separate worker handles extraction.

## 0:45–1:30 — Batch upload

In the frontend, upload `valid_invoice.pdf`, `spreadsheet_valid.xlsx`, `missing_tax.csv`, and `incorrect_total.pdf` together. Show each per-file status. Explain that PDF, spreadsheet, CSV, image, and OCR-capable inputs use the same processing/result shape, while corrupt files fail individually.

Optional API request from PowerShell:

```powershell
$uploadJson = curl.exe -X POST http://127.0.0.1:8000/api/documents/upload `
  -F "files=@evaluation/dataset/valid_invoice.pdf" `
  -F "files=@evaluation/dataset/spreadsheet_valid.xlsx" `
  -F "files=@evaluation/dataset/missing_tax.csv"
$upload = $uploadJson | ConvertFrom-Json
$upload | ConvertTo-Json -Depth 8
```

The response includes accepted document IDs, initial `queued` status, and per-file errors or duplicate indications. Use the returned IDs in the following examples; do not assume IDs from a prior run.

## 1:30–2:30 — Status, extraction, and evidence

Select a queued document and watch its status update. Open the details panel to show invoice fields, line items, page/sheet evidence, validation findings, and processing history. Point out that progress is shown from stored worker events; no percentage is fabricated. The spreadsheet fixture includes two line items on one worksheet. The parser preserves the worksheet name and coordinates.

With a real `$documentId` from the upload response:

```powershell
$documentId = $upload.items[0].document_id
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId" | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/invoice" | ConvertTo-Json -Depth 12
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/history" | ConvertTo-Json -Depth 8
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/$documentId/validation-errors" | ConvertTo-Json -Depth 8
```

## 2:30–3:20 — Search and validation

Use the keyword search to find the supplier or invoice ID. Optionally show semantic/hybrid mode only if the local embedding model is installed; otherwise show the intentional unavailable response. Open `incorrect_total.pdf` and explain the stored subtotal/tax/total reconciliation finding. `missing_tax.csv` demonstrates that missing tax is reported without assuming a specific tax regime.

## 3:20–4:15 — Manual correction audit

Correct a value on a processed invoice, enter a short reason, and save. Show the changed invoice, rerun validation, and correction audit with previous/corrected values and time. Explain that the original upload remains unchanged and the original extraction snapshot is retained.

## 4:15–5:00 — Compare selected sources

Open Compare and select the completed PDF and XLSX representations of the same synthetic invoice. Submit the comparison and inspect matched/missing/mismatched fields and the identity-based review recommendation. Then compare a different invoice and show the deliberate mismatch. Comparison uses persisted extraction results and requires selecting both document IDs.

## 5:00–5:40 — Failures and retry boundary

Upload `corrupted.pdf` with a valid invoice. Show that the valid document is processed while the corrupted PDF receives a per-file failed state and safe error. A permanently invalid source is not retryable. A transient failed job can be retried from the details view; attempts and history remain visible.

## 5:40–6:00 — Evaluation and limitations

Run `python scripts/evaluate.py` and show the generated timestamped JSON report. The report includes field exact-match results, validation outcomes, failure counts, document timing, OCR dependency status, and optional long-PDF measurements. Report machine-specific dependencies honestly. The included 1,000-page result is a digital-text extraction throughput sample from one local run, not a scanned-document or concurrent-load benchmark.

## Reset between demonstrations

Stop all three processes and remove only the local assessment data if you want a fresh queue. From the repository root:

```powershell
Remove-Item .\backend\data\careguide.db* -Force -ErrorAction SilentlyContinue
Remove-Item .\backend\data\uploads\* -Force -ErrorAction SilentlyContinue
Remove-Item .\backend\data\processed\* -Force -ErrorAction SilentlyContinue
```

The evaluation dataset and report are synthetic outputs and are not deleted by those commands.

## Manual recording checklist

- [ ] Start API, worker, and frontend; show `/api/ready` and the open UI.
- [ ] Upload at least a PDF, XLSX, and CSV together and show the per-file statuses.
- [ ] Show a queued-to-completed status update from the worker.
- [ ] Inspect extracted header fields, line items, and source evidence.
- [ ] Open the uncertain identifier fixture and state whether OCR is actually available on the recording machine.
- [ ] Show the missing-tax warning and the inconsistent-total finding.
- [ ] Correct one field and show the validation result plus correction audit.
- [ ] Compare the matching PDF and spreadsheet, then show a deliberate mismatch.
- [ ] Search by invoice number or supplier.
- [ ] Run backend/frontend tests and the evaluator; show actual output and say plainly that no video was recorded as part of this implementation.
