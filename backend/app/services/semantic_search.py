"""Optional local sentence-transformers embeddings and cosine ranking."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import logging
import math
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.embedding import DocumentEmbedding
from app.models.search import DocumentSearchIndex

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingUnavailable("Install backend/requirements-embeddings.txt to enable semantic search.") from exc
    try:
        # CPU inference keeps the optional feature within small-VRAM laptop limits.
        return SentenceTransformer(model_name, device="cpu", local_files_only=True, trust_remote_code=False)
    except Exception as exc:
        raise EmbeddingUnavailable(
            f"Embedding model '{model_name}' is not available locally. Run scripts/download_embedding_model.py and set EMBEDDING_MODEL_NAME to its output path."
        ) from exc


def semantic_rank(db: Session, candidates: list[tuple[Any, DocumentSearchIndex]], query: str,
                  settings: Settings | None = None) -> list[tuple[int, float]]:
    settings = settings or get_settings()
    if not query.strip():
        return []
    if len(candidates) > settings.semantic_max_documents:
        candidates = candidates[:settings.semantic_max_documents]
    model = _load_model(settings.embedding_model_name)
    by_id = {document.id: (document, index) for document, index in candidates}
    embeddings = {row.document_id: row for row in db.query(DocumentEmbedding).filter(
        DocumentEmbedding.document_id.in_(list(by_id)), DocumentEmbedding.model_name == settings.embedding_model_name).all()} if by_id else {}
    missing = []
    for document_id, (document, index) in by_id.items():
        digest = hashlib.sha256(index.searchable_text.encode("utf-8", errors="replace")).hexdigest()
        row = embeddings.get(document_id)
        if row is None or row.content_hash != digest:
            missing.append((document_id, index.searchable_text, digest))
    if missing:
        vectors = model.encode([text for _, text, _ in missing], normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False, batch_size=16)
        for (document_id, _text, digest), vector in zip(missing, vectors):
            row = embeddings.get(document_id)
            if row is None:
                row = DocumentEmbedding(document_id=document_id, model_name=settings.embedding_model_name,
                                        content_hash=digest, vector=[])
                db.add(row)
            row.content_hash = digest
            row.vector = [float(value) for value in vector]
        db.commit()
        embeddings.update({row.document_id: row for row in db.query(DocumentEmbedding).filter(
            DocumentEmbedding.document_id.in_(list(by_id)), DocumentEmbedding.model_name == settings.embedding_model_name).all()})
    query_vector = model.encode([query], normalize_embeddings=True, convert_to_numpy=True,
                                show_progress_bar=False, batch_size=1)[0]
    scores = []
    for document_id, row in embeddings.items():
        vector = row.vector or []
        if len(vector) != len(query_vector):
            continue
        score = sum(float(a) * float(b) for a, b in zip(query_vector, vector))
        if math.isfinite(score):
            scores.append((document_id, score))
    return sorted(scores, key=lambda item: item[1], reverse=True)
