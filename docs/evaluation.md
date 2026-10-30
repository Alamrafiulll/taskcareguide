# Synthetic evaluation

The evaluation is an executable check of the stored pipeline, not a hand-coded result table. `scripts/evaluate.py` creates an isolated SQLite database, enqueues each manifest fixture with the application ingestion service, runs the application worker, and scores the persisted invoice, validations, duplicate references, job outcomes, and timings. The runner does not modify the development database.

## Reproduce

From the repository root in PowerShell:

```powershell
python scripts\create_evaluation_dataset.py
python scripts\evaluate.py
python scripts\evaluate.py --benchmark-pages 1000
```

Each run writes a UTC timestamped JSON report under `evaluation/results/`. The optional benchmark supports 1–5,000 pages. It uses local PyMuPDF-generated digital text, so it measures page iteration/text extraction and not OCR, network use, GPU inference, or parallel workers.

## Scoring

- Header values use exact match after whitespace/case normalization. Monetary values use exact `Decimal` equality; the scorer intentionally does not apply the financial validation tolerance.
- Line items compare ordered description, quantity, unit price, and line total. Provenance fields are not part of the comparison.
- Expected nulls count as matches only when extraction returns null. Explicitly unreadable values and file-format fields that do not apply are excluded as named in `ground_truth.json`.
- If Tesseract is not configured, all field and validation checks on fixtures marked `requires_ocr` are recorded as skipped. The report still retains those job outcomes and OCR-specific errors. It does not claim those documents passed OCR.
- Processing success means a completed worker job and a processor status of `completed` or `completed_with_errors`. A deliberately corrupt source is expected to fail and is counted in the denominator and expected-failure count.
- Validation checks require each expected finding code to be present. Duplicate checks also require the expected earlier document ID to appear in the persisted duplicate reference.
- Timing comes from the persisted worker start/completion timestamps. It includes processor and result persistence overhead and is not a controlled benchmark.

## Workspace run

The latest run is [evaluation-20261030T194423Z.json](../evaluation/results/evaluation-20261030T194423Z.json). It used Tesseract 5.5.3 from the configured local executable and processed all 10 fixtures, including scanned image and PDF paths. It recorded 77/80 exact field matches (96.25%); seven header fields matched all eligible values, while line items matched 6/9 because three OCR descriptions differed from ground truth. Nine documents completed and the deliberately corrupt PDF failed. All 10 expected financial and duplicate checks passed.

The same run processed 1,000 digital-text PDF pages in 0.0776 seconds on this machine. This is a local parser measurement, not an OCR or throughput benchmark, SLA, or concurrency result. The synthetic dataset is small and does not estimate real-world vendor or document distributions. A real semantic-model and live Ollama smoke test are separate from this extraction evaluation.
