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

The committed [report](../evaluation/results/evaluation-20261028T203950Z.json) records 54/54 exact field matches (1.0) across cases whose required local dependencies were available, 8/8 expected validation and duplicate checks, and 9/10 completed documents. The corrupt PDF is the one expected processing failure. Tesseract was not executable in this environment, so the uncertain-image, scanned-JPG, and scanned-PDF extraction checks are dependency-skipped. Do not interpret the field score as OCR accuracy or as a production/generalization estimate.

The report's optional long-PDF run processed 1,000 digital-text pages in 0.0852 seconds on this machine. That is one local observation; it is not an SLA, memory bound for all inputs, OCR measurement, or concurrency result. Synthetic fixtures are intentionally small and do not estimate real-world vendor or document distributions.
