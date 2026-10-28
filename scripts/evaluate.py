"""Evaluate the persisted local ingestion pipeline against synthetic ground truth."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import Settings, get_settings  # noqa: E402
from app.database import Base, build_engine  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.invoice import InvoiceResult  # noqa: E402
from app.models.processing import ProcessingJob  # noqa: E402
from app.services.ingestion import DocumentIngestionService  # noqa: E402
from app.services.processors.ocr import OCRService, OCRUnavailableError  # noqa: E402
from app.services.worker import DocumentWorker  # noqa: E402


def canonical(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field in {"subtotal", "tax_amount", "total_amount"}:
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return str(value).strip().casefold()
    if field == "line_items":
        result = []
        for item in value or []:
            normalized = {"description": " ".join(str(item.get("description") or "").casefold().split())}
            for key in ("quantity", "unit_price", "line_total"):
                raw = item.get(key, item.get("amount") if key == "line_total" else None)
                try:
                    normalized[key] = Decimal(str(raw)).normalize() if raw not in (None, "") else None
                except InvalidOperation:
                    normalized[key] = str(raw).strip().casefold() if raw is not None else None
            result.append(normalized)
        return result
    return " ".join(str(value).strip().casefold().split())


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _dataset_manifest() -> dict[str, Any]:
    path = ROOT / "evaluation" / "dataset" / "ground_truth.json"
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_benchmark(settings: Settings, page_count: int) -> dict[str, Any]:
    import fitz
    from app.services.document_processing import DocumentProcessingService

    with tempfile.TemporaryDirectory(prefix="careguide-long-pdf-") as temp_dir:
        path = Path(temp_dir) / f"synthetic-{page_count}-pages.pdf"
        pdf = fitz.open()
        for index in range(page_count):
            page = pdf.new_page(width=612, height=792)
            page.insert_text((55, 75), f"Care Guide synthetic benchmark page {index + 1:04d}; digital text extraction.", fontsize=12)
        pdf.save(path, garbage=3, deflate=True)
        pdf.close()
        settings.max_pdf_pages = page_count
        started = time.perf_counter()
        result, _, _ = DocumentProcessingService(settings).process(path, document_id=0, filename=path.name)
        duration = time.perf_counter() - started
        return {"pages_requested": page_count, "pages_reported": result.page_count,
                "pages_processed": result.pages_processed, "status": result.status,
                "seconds": round(duration, 4),
                "seconds_per_processed_page": round(duration / result.pages_processed, 6) if result.pages_processed else None,
                "ocr_used": result.ocr_used,
                "measurement_note": "This run generates and processes a local digital-text PDF. It measures this environment only; it is not a concurrency or scanned-PDF benchmark."}


def run_evaluation(*, benchmark_pages: int = 0) -> dict[str, Any]:
    manifest = _dataset_manifest()
    try:
        tesseract_status = OCRService(get_settings()).verify()
        tesseract_available = True
    except OCRUnavailableError as exc:
        tesseract_status = f"unavailable: {exc}"
        tesseract_available = False
    dataset_dir = ROOT / "evaluation" / "dataset"
    documents_gt = manifest["documents"]
    extracted_records: list[dict[str, Any]] = []
    timing_total: list[float] = []
    page_timings: list[float] = []

    with tempfile.TemporaryDirectory(prefix="careguide-evaluation-") as temp_dir:
        temp_root = Path(temp_dir)
        upload_dir, processed_dir = temp_root / "uploads", temp_root / "processed"
        database_url = f"sqlite:///{(temp_root / 'evaluation.db').as_posix()}"
        settings = Settings(_env_file=None, database_url=database_url, upload_dir=upload_dir,
                            processed_dir=processed_dir, worker_max_attempts=1,
                            max_pdf_pages=max(1000, benchmark_pages or 0), processing_timeout_seconds=1800,
                            ollama_enabled=False)
        engine = build_engine(database_url)
        Base.metadata.create_all(engine)
        from sqlalchemy.orm import sessionmaker
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        service = DocumentIngestionService(settings)
        worker = DocumentWorker(settings, factory, service, worker_id="careguide-evaluation-worker")
        document_ids: dict[str, int] = {}
        enqueue_errors: dict[str, str] = {}
        for item in documents_gt:
            name = item["file"]
            source = dataset_dir / name
            if not source.is_file():
                enqueue_errors[name] = "Fixture file is absent."
                continue
            with source.open("rb") as stream, factory() as db:
                upload = SimpleNamespace(filename=name, content_type=None, file=stream)
                accepted = service.enqueue(upload, db)
            if accepted.get("document_id") is not None and accepted.get("status") == "queued":
                document_ids[name] = accepted["document_id"]
            else:
                enqueue_errors[name] = (accepted.get("error") or {}).get("message", accepted.get("status", "Upload was not queued."))

        max_jobs = len(document_ids) + 5
        jobs_run = 0
        while jobs_run < max_jobs and worker.run_once():
            jobs_run += 1

        for item in documents_gt:
            name = item["file"]
            doc_id = document_ids.get(name)
            if doc_id is None:
                extracted_records.append({"file": name, "worker_status": "not_queued", "error": enqueue_errors.get(name), "invoice": None, "validation_codes": [], "status": "failed", "page_count": None, "pages_processed": 0, "duration_seconds": None})
                continue
            with factory() as db:
                document = db.get(Document, doc_id)
                job = db.query(ProcessingJob).filter_by(document_id=doc_id).one()
                result = db.query(InvoiceResult).filter_by(document_id=doc_id).one_or_none()
                raw = (result.raw_extraction if result else None) or {}
                invoice = raw.get("invoice") or {}
                validation = raw.get("invoice_validation") or []
                elapsed = None
                if job.started_at and job.completed_at:
                    elapsed = max(0, (job.completed_at - job.started_at).total_seconds())
                status = raw.get("status", document.status.value if hasattr(document.status, "value") else str(document.status))
                page_count = raw.get("page_count")
                pages_processed = raw.get("pages_processed") or 0
                error = document.error_details or job.error_details
                if error:
                    error = str(error)
                    for temp_prefix in {str(temp_root), str(temp_root).replace("\\", "\\\\")}:
                        error = error.replace(temp_prefix, "[evaluation temp]")
                extracted_records.append({"file": name, "document_id": doc_id,
                    "worker_status": document.status.value if hasattr(document.status, "value") else str(document.status),
                    "job_status": job.status.value if hasattr(job.status, "value") else str(job.status),
                    "error": error, "status": status, "invoice": invoice,
                    "validation_codes": sorted({entry.get("code") for entry in validation if entry.get("code")}),
                    "ocr_used": bool(raw.get("ocr_used")), "warnings": raw.get("warnings", []),
                    "processing_errors": raw.get("errors", []), "page_count": page_count,
                    "pages_processed": pages_processed, "duration_seconds": round(elapsed, 6) if elapsed is not None else None,
                    "duplicate_document_ids": raw.get("duplicate_invoice_document_ids", [])})
                if elapsed is not None:
                    timing_total.append(elapsed)
                    if Path(name).suffix.casefold() == ".pdf" and pages_processed:
                        page_timings.append(elapsed / pages_processed)
        engine.dispose()

    record_by_name = {row["file"]: row for row in extracted_records}
    field_counts: dict[str, dict[str, Any]] = {field: {"exact_matches": 0, "denominator": 0, "mismatches": []}
                                               for field in manifest["fields"]}
    total_matches = total_denominator = 0
    for item in documents_gt:
        row = record_by_name[item["file"]]
        expected, actual = item["expected_invoice"], row.get("invoice") or {}
        excluded = set(item.get("unreadable_fields", [])) | set(item.get("skipped_fields", []))
        if item.get("requires_ocr") and not tesseract_available:
            excluded.update(manifest["fields"])
        for field in manifest["fields"]:
            if field in excluded:
                continue
            counts = field_counts.setdefault(field, {"exact_matches": 0, "denominator": 0, "mismatches": []})
            counts["denominator"] += 1
            total_denominator += 1
            actual_value = actual.get(field)
            matches = canonical(field, expected.get(field)) == canonical(field, actual_value)
            if matches:
                counts["exact_matches"] += 1
                total_matches += 1
            else:
                counts["mismatches"].append({"file": item["file"], "expected": expected.get(field), "observed": actual_value})

    validation_checks = []
    validation_expected = validation_passed = 0
    known_inconsistent = []
    for item in documents_gt:
        row = record_by_name[item["file"]]
        actual_codes = set(row.get("validation_codes", []))
        expected_codes = set(item.get("expected_validation_codes", []))
        if item.get("requires_ocr") and not tesseract_available:
            validation_checks.append({"file": item["file"], "expected_codes": sorted(expected_codes),
                                      "actual_codes": sorted(actual_codes), "skipped": True,
                                      "skip_reason": "Local Tesseract executable is unavailable."})
            continue
        missing = sorted(expected_codes - actual_codes)
        validation_expected += len(expected_codes)
        validation_passed += len(expected_codes - set(missing))
        validation_checks.append({"file": item["file"], "expected_codes": sorted(expected_codes),
                                  "actual_codes": sorted(actual_codes), "missing_expected_codes": missing,
                                  "expected_codes_present": not missing})
        if item.get("expected_duplicate_of"):
            expected_prior_id = record_by_name[item["expected_duplicate_of"]].get("document_id")
            duplicate_found = expected_prior_id in row.get("duplicate_document_ids", [])
            validation_checks[-1]["expected_duplicate_of"] = item["expected_duplicate_of"]
            validation_checks[-1]["duplicate_detected"] = duplicate_found
            validation_expected += 1
            validation_passed += int(duplicate_found)
        if "subtotal_tax_total_mismatch" in expected_codes:
            known_inconsistent.append({"file": item["file"], "expected_mismatch": "subtotal_tax_total_mismatch",
                                       "detected": "subtotal_tax_total_mismatch" in actual_codes,
                                       "actual_codes": sorted(actual_codes)})

    expected_failures = sum(bool(item.get("expected_processing_failure")) for item in documents_gt)
    observed_failures = sum(row.get("worker_status") in {"failed", "not_queued"} for row in extracted_records)
    completed = sum(row.get("status") in {"completed", "completed_with_errors"} and row.get("worker_status") == "completed" for row in extracted_records)
    ocr_results = []
    for row in extracted_records:
        if row.get("ocr_used") or Path(row["file"]).suffix.casefold() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"} or row.get("page_count"):
            ocr_results.append({"file": row["file"], "ocr_used": row.get("ocr_used", False),
                                "processing_errors": row.get("processing_errors", [])})

    report = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {"name": manifest["dataset_name"], "size": len(documents_gt), "path": "evaluation/dataset", "ground_truth": "evaluation/dataset/ground_truth.json"},
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "pymupdf": package_version("PyMuPDF"),
                        "pillow": package_version("Pillow"), "openpyxl": package_version("openpyxl"),
                        "tesseract": tesseract_status, "ollama_runtime_enabled": False,
                        "sentence_transformers_installed": package_version("sentence-transformers") is not None},
        "methodology": {"field_accuracy": "Exact match after documented normalization. Explicit null labels count in the denominator and match only a null extraction. unreadable_fields and skipped_fields are excluded from field accuracy and separately identified by the manifest. Processing failures remain in the denominator unless fields are explicitly skipped.",
                        "processing_success": "Stored worker document status completed and processor status completed or completed_with_errors. A captured OCR page error still counts as a completed document pipeline, but its missing invoice fields count as extraction misses.",
                        "failure_rate": "Failed or not-queued documents divided by all manifest documents.",
                        "timing": "Worker started_at to completed_at in the isolated SQLite run; PDF page time divides by pages actually processed. Includes extraction persistence pipeline duration as recorded by job timestamps.",
                        "validation": "Known validation codes must appear in persisted structured validation findings. Duplicate checks compare normalized supplier plus invoice identifier; equal amount alone is not considered a duplicate."},
        "metrics": {"field_exact_match_accuracy": {"exact_matches": total_matches, "denominator": total_denominator,
                      "accuracy": total_matches / total_denominator if total_denominator else None},
                    "per_field": {field: {"exact_matches": values["exact_matches"], "denominator": values["denominator"],
                                 "accuracy": values["exact_matches"] / values["denominator"] if values["denominator"] else None,
                                 "mismatches": values["mismatches"]} for field, values in field_counts.items()},
                    "document_processing_success": {"count": completed, "denominator": len(documents_gt), "rate": completed / len(documents_gt) if documents_gt else None},
                    "document_failure": {"count": observed_failures, "denominator": len(documents_gt), "rate": observed_failures / len(documents_gt) if documents_gt else None,
                                         "expected_failures": expected_failures},
                    "processing_time_seconds": {"measured_documents": len(timing_total), "mean": statistics.mean(timing_total) if timing_total else None,
                                                 "median": statistics.median(timing_total) if timing_total else None,
                                                 "per_document": {row["file"]: row.get("duration_seconds") for row in extracted_records}},
                    "pdf_seconds_per_processed_page_mean": statistics.mean(page_timings) if page_timings else None,
                    "validation_expected_checks": {"passed": validation_passed, "denominator": validation_expected,
                                                    "rate": validation_passed / validation_expected if validation_expected else None},
                    "known_inconsistent_invoice_outcomes": known_inconsistent,
                    "ocr_cases": ocr_results},
        "validation_checks": validation_checks,
        "dependency_skips": {"ocr_required_documents": [item["file"] for item in documents_gt
                                                           if item.get("requires_ocr") and not tesseract_available]},
        "documents": extracted_records,
        "limitations": ["Synthetic fixtures are small and are not representative of vendor diversity or production prevalence.",
                        "Real OCR was measured only if the configured Tesseract executable was available.",
                        "Ollama and sentence-transformers are not used for this extraction evaluation.",
                        "A 1,000-page performance result is reported only when --benchmark-pages 1000 is run."],
    }
    if benchmark_pages:
        benchmark_settings = Settings(_env_file=None, max_pdf_pages=benchmark_pages, processing_timeout_seconds=1800)
        report["long_pdf_benchmark"] = _run_benchmark(benchmark_settings, benchmark_pages)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional output JSON path; default uses a UTC timestamp under evaluation/results/")
    parser.add_argument("--benchmark-pages", type=int, default=0, help="Generate and actually process a local digital PDF with this many pages (for example 1000).")
    args = parser.parse_args()
    if args.benchmark_pages < 0 or args.benchmark_pages > 5000:
        parser.error("--benchmark-pages must be between 0 and 5000")
    result = run_evaluation(benchmark_pages=args.benchmark_pages)
    output_path = args.output or (ROOT / "evaluation" / "results" / f"evaluation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output_path.relative_to(ROOT)) if output_path.is_relative_to(ROOT) else str(output_path),
                      "dataset_size": result["dataset"]["size"], "field_accuracy": result["metrics"]["field_exact_match_accuracy"],
                      "processing_success": result["metrics"]["document_processing_success"],
                      "failure_rate": result["metrics"]["document_failure"],
                      "validation": result["metrics"]["validation_expected_checks"],
                      "long_pdf_benchmark": result.get("long_pdf_benchmark")}, indent=2, default=str))


if __name__ == "__main__":
    main()
