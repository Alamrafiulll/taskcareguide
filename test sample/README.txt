CARE GUIDE SYNTHETIC TEST SAMPLES
===================================

These files are fictional test data. Upload the files to the Care Guide app; do not treat them as real invoices.

Suggested end-to-end demo:
1. Upload 01_valid_invoice.pdf, 02_matching_invoice.xlsx, 05_missing_invoice_number.csv,
   06_missing_tax.csv, 07_incorrect_total.pdf, and 11_scanned_invoice.pdf together.
2. Keep the API and worker running until the rows show completed/failed. Open each row and inspect fields,
   line items, review findings, processing history, and (for the scan) OCR output.
3. Compare 01_valid_invoice.pdf with 02_matching_invoice.xlsx; then compare 01_valid_invoice.pdf
   with 03_mismatch_same_invoice.csv. The first pair should match; the second shares invoice identity
   but has a different total.
4. Search for North Clinic with Keyword mode.
5. Open 07_incorrect_total.pdf, correct Total amount to 132.00 (120.00 subtotal + 12.00 tax), enter a
   reason, and choose Save review. Inspect More details and download the reviewed result.
6. Upload 16_empty.csv, 17_corrupted.pdf, and 18_unsupported.txt to see isolated rejection/failure handling.
7. Upload 14_legacy_invoice.xls and 15_malformed_rows.csv to inspect legacy workbook and malformed CSV handling.
8. Optional: upload optional/optional_1001_page_limit.pdf to demonstrate the configured 1,000-page limit.

Files 01 and 02 are a same-invoice comparison pair. Files 01 and 03 are a same-identity pair with a
deliberate total mismatch. Files 02, 03, and 04 also exercise duplicate invoice detection; the app should
not decide that documents are duplicates based only on equal totals.

Retry behavior: the corrupt PDF is permanently invalid, so it is correctly not retryable. A sample file
cannot reliably create a transient worker error. Use the automated worker retry tests for that state, or
upload a valid file while the worker is stopped and restart the worker to show queued work resumes.

Optional runtime AI: Ollama suggestions require Ollama and a local model. Semantic search requires the
optional sentence-transformers package and model weights. Ordinary deterministic extraction and keyword
search work without either. Do not select semantic mode in a demo unless the model is installed and ready.

Ground-truth expectations are in ground_truth.json. OCR can make character-level mistakes, so review the
extracted values rather than assuming every scanned field will be exact.

To regenerate from the project root, run:
    .venv\Scripts\python.exe scripts\generate_test_samples.py

The optional 1,001-page file is separate so it does not slow down the ordinary multi-file demo.
