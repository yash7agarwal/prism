"""Embedding-based dedupe layer, one notch deeper than trigram-normalized names.

Flow inside knowledge_store.upsert_entity:
  1. exact canonical_name match (existing, DB index)
  2. trigram-normalized match ≥0.9 Jaccard (existing)
  3. **embedding cosine match ≥0.90 → merge; band 0.78..0.90 → LLM tie-breaker**  (this module)
  4. insert fresh

Everything here is graceful: if Gemini embeddings are unavailable or the
entity has no prior embedding to compare against, the caller falls through
to insert as if this layer didn't exist. No crashes on provider exhaustion.

Design constraints (from LESSONS ch.8):
- Embeddings stored as np.float32 bytes in existing KnowledgeEmbedding table.
- Cosine top-K in Python — no pgvector until >100k entities/project.
- LLM tie-breaker ONLY in ambiguous band so cost stays minimal.
"""
from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.models import KnowledgeEmbedding, KnowledgeEntity

logger = logging.getLogger(__name__)

AUTO_MERGE_THRESHOLD = 0.90
AMBIGUOUS_LO = 0.78
AMBIGUOUS_HI = 0.90
TIE_BREAKER_MODEL_HINT = "haiku"


@dataclass
class DedupeCandidate:
    entity_id: int
    name: str
    score: float


def _vec_to_bytes(vec: list[float]) -> bytes:
    """Pack a float list as little-endian float32 bytes. Raw, no numpy dep."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _bytes_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom > 0 else 0.0


def _embedding_for_entity(db: Session, entity_id: int) -> list[float] | None:
    """Return the stored float32 embedding for an entity, or None."""
    row = (
        db.query(KnowledgeEmbedding)
        .filter(KnowledgeEmbedding.entity_id == entity_id)
        .order_by(KnowledgeEmbedding.id.desc())
        .first()
    )
    if row is None or not row.embedding_blob:
        return None
    try:
        return _bytes_to_vec(row.embedding_blob)
    except Exception:
        return None


def _store_embedding(
    db: Session, entity_id: int, text_chunk: str, vec: list[float], model: str,
) -> None:
    row = KnowledgeEmbedding(
        entity_id=entity_id,
        text_chunk=text_chunk[:1000],
        embedding_blob=_vec_to_bytes(vec),
        embedding_model=model,
    )
    db.add(row)
    db.commit()


def find_best_match(
    db: Session,
    project_id: int,
    entity_type: str,
    text: str,
) -> DedupeCandidate | None:
    """Compute `text`'s embedding and return the closest same-type entity in the
    same project with its cosine score, or None if no candidates exist / no
    provider available / only weak matches (score < AMBIGUOUS_LO).

    The caller decides what to DO with the result:
      - score ≥ AUTO_MERGE_THRESHOLD → merge without asking
      - AMBIGUOUS_LO ≤ score < AUTO_MERGE_THRESHOLD → run LLM tie-breaker
      - score < AMBIGUOUS_LO → fall through to insert
    """
    from utils import gemini_embeddings

    if not gemini_embeddings.is_available():
        return None
    vec = gemini_embeddings.embed(text)
    if vec is None:
        return None

    # Fetch candidate entities of the same (project, type). Join on the most
    # recent embedding for each; skip those without one.
    rows = (
        db.query(KnowledgeEntity, KnowledgeEmbedding)
        .join(KnowledgeEmbedding, KnowledgeEmbedding.entity_id == KnowledgeEntity.id)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == entity_type,
            KnowledgeEmbedding.embedding_blob.is_not(None),
        )
        .all()
    )

    best: DedupeCandidate | None = None
    seen: set[int] = set()
    for entity, emb in rows:
        if entity.id in seen:
            continue
        seen.add(entity.id)
        try:
            other = _bytes_to_vec(emb.embedding_blob)
        except Exception:
            continue
        score = _cosine(vec, other)
        if best is None or score > best.score:
            best = DedupeCandidate(entity_id=entity.id, name=entity.name, score=score)

    # Side-effect: remember the new embedding on a well so callers can persist
    # it after they decide insert vs merge. Returned via `None` when no match;
    # the caller reads the module-level `_last_computed` to store it on the
    # fresh entity. (Simpler API: callers that merge discard the new vec;
    # callers that insert pass the entity id to store_new_embedding below.)
    global _LAST_COMPUTED_VEC, _LAST_COMPUTED_TEXT
    _LAST_COMPUTED_VEC = vec
    _LAST_COMPUTED_TEXT = text

    return best


_LAST_COMPUTED_VEC: list[float] | None = None
_LAST_COMPUTED_TEXT: str = ""


def store_new_embedding(db: Session, entity_id: int) -> bool:
    """Persist the most-recently computed embedding onto a freshly inserted entity.

    Call right after a successful insert when find_best_match returned None or
    score < AMBIGUOUS_LO. Returns True if a vector was stored.
    """
    if _LAST_COMPUTED_VEC is None:
        return False
    _store_embedding(
        db, entity_id, _LAST_COMPUTED_TEXT, _LAST_COMPUTED_VEC, model="gemini-text-embedding-004",
    )
    return True


def llm_tie_breaker(name_a: str, desc_a: str, name_b: str, desc_b: str) -> bool:
    """LLM yes/no on whether two entities are the same concept.

    Returns True if the same concept, False otherwise. Defaults to False
    (safer to insert than merge) on any failure so an outage never auto-merges
    distinct concepts.
    """
    from utils import claude_client

    prompt = (
        "Two knowledge-graph entities are up for merge. Decide if they refer "
        "to the SAME concept (same underlying phenomenon / trend / company / "
        "product), not just an overlapping topic.\n\n"
        f"Entity A: {name_a}\n"
        f"Description: {desc_a or '(none)'}\n\n"
        f"Entity B: {name_b}\n"
        f"Description: {desc_b or '(none)'}\n\n"
        "Reply with EXACTLY one token: SAME or DIFFERENT."
    )
    try:
        resp = claude_client.ask_fast(prompt, max_tokens=5)
        decision = (resp or "").strip().upper().split()[0] if resp else ""
        return decision == "SAME"
    except Exception as exc:
        logger.debug("[semantic_dedupe] tie-breaker LLM failed: %s — defaulting DIFFERENT", exc)
        return False
