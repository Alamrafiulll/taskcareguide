from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1_documents import _filtered_query, _summary
from app.config import get_settings
from app.database import get_db
from app.models.document import Document
from app.models.invoice import InvoiceResult
from app.models.processing import ProcessingJob
from app.models.search import DocumentSearchIndex
from app.schemas.documents import SearchResponse, SearchResult
from app.services.semantic_search import EmbeddingUnavailable, semantic_rank

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
def search_documents(query: str = Query(min_length=1, max_length=500), mode: str = Query(default="keyword"),
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                     status_filter: str | None = Query(default=None, alias="status"),
                     invoice_number: str | None = None, supplier: str | None = None,
                     customer: str | None = None, currency: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     validation_status: str | None = None, db: Session = Depends(get_db)) -> SearchResponse:
    if mode not in {"keyword", "semantic", "hybrid"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_search_mode", "message": "Mode must be keyword, semantic, or hybrid."})
    import datetime
    try:
        parsed_from = datetime.date.fromisoformat(date_from) if date_from else None
        parsed_to = datetime.date.fromisoformat(date_to) if date_to else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_date_filter", "message": "Date filters must use YYYY-MM-DD."}) from exc
    query_stmt = _filtered_query(keyword=query if mode == "keyword" else None, status_filter=status_filter,
        invoice_number=invoice_number, supplier=supplier, customer=customer, currency=currency,
        date_from=parsed_from, date_to=parsed_to, validation_status=validation_status)
    if mode == "keyword":
        from sqlalchemy import func, select
        total = db.scalar(select(func.count()).select_from(query_stmt.order_by(None).subquery())) or 0
        rows = db.execute(query_stmt.order_by(Document.created_at.desc(), Document.id.desc())
                          .offset((page - 1) * page_size).limit(page_size)).all()
        results = [SearchResult(document=_summary(doc, invoice, job, count), score=None, match_type="keyword")
                   for doc, invoice, job, count in rows]
        return SearchResponse(query=query, mode=mode, total=total, results=results, semantic_available=False)

    settings = get_settings()
    rows = db.execute(query_stmt.order_by(Document.created_at.desc(), Document.id.desc())
                      .limit(settings.semantic_max_documents + 1)).all()
    if len(rows) > settings.semantic_max_documents:
        raise HTTPException(status_code=413, detail={"code": "semantic_corpus_limit", "message": "Apply filters to reduce the semantic search set to the configured local limit."})
    candidate_pairs = []
    for document, _invoice, _job, _count in rows:
        index = db.get(DocumentSearchIndex, document.id)
        if index is not None:
            candidate_pairs.append((document, index))
    try:
        ranked = semantic_rank(db, candidate_pairs, query, settings)
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "semantic_search_unavailable", "message": str(exc),
                                                     "details": {"keyword_mode_available": True}}) from exc
    by_id = {row[0].id: row for row in rows}
    keyword_ids = set()
    query_folded = query.casefold()
    if mode == "hybrid":
        for document, _invoice, _job, _count in rows:
            index = db.get(DocumentSearchIndex, document.id)
            if query_folded in document.original_filename.casefold() or (index and query_folded in index.searchable_text.casefold()):
                keyword_ids.add(document.id)
        semantic_scores = {document_id: score for document_id, score in ranked}
        ranked = sorted(((document_id, score * 0.7 + (0.3 if document_id in keyword_ids else 0.0))
                         for document_id, score in semantic_scores.items()), key=lambda item: item[1], reverse=True)
    total = len(ranked)
    selected = ranked[(page - 1) * page_size:page * page_size]
    results = []
    for document_id, score in selected:
        doc, invoice, job, count = by_id[document_id]
        hit_type = "hybrid" if mode == "hybrid" and document_id in keyword_ids else "semantic"
        results.append(SearchResult(document=_summary(doc, invoice, job, count), score=score, match_type=hit_type))
    return SearchResponse(query=query, mode=mode, total=total, results=results, semantic_available=True)
