"""Cross-project hypothesis queue.

When a trend found in one project looks relevant to another (industry
taxonomy overlap), we register a `CrossProjectHypothesis` rather than
auto-promoting it. The human acks or rejects via the endpoints here.

Design rule (see LESSONS ch.8): cross-project transfer is only safe when
gated on (a) overlap signal, (b) human acknowledgement. Auto-promotion was
the exact failure class that caused the Swiggy travel-trend contamination.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.api.db import get_db
from webapp.api.models import (
    CrossProjectHypothesis,
    KnowledgeEntity,
    Project,
)
from webapp.api.schemas import (
    CrossProjectHypothesisOut,
    CrossProjectSuggestIn,
)

router = APIRouter(prefix="/api/xproj", tags=["xproj"])


@router.post("/suggest", response_model=CrossProjectHypothesisOut)
def suggest(body: CrossProjectSuggestIn, db: Session = Depends(get_db)):
    """Register a cross-project suggestion. Idempotent per (source_entity, target)."""
    src = db.get(KnowledgeEntity, body.source_entity_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source entity not found")
    target = db.get(Project, body.target_project_id)
    if target is None:
        raise HTTPException(status_code=404, detail="target project not found")
    if src.project_id == body.target_project_id:
        raise HTTPException(
            status_code=400, detail="source entity is already on the target project",
        )

    hypothesis = CrossProjectHypothesis(
        source_project_id=src.project_id,
        target_project_id=body.target_project_id,
        source_entity_id=src.id,
        source_entity_name=src.name,
        source_description=src.description,
        rationale=body.rationale,
        similarity_score=body.similarity_score,
        status="suggested",
    )
    db.add(hypothesis)
    try:
        db.commit()
    except IntegrityError:
        # Idempotent — a prior suggestion for the same (source, target) pair exists.
        db.rollback()
        existing = (
            db.query(CrossProjectHypothesis)
            .filter(
                CrossProjectHypothesis.source_entity_id == src.id,
                CrossProjectHypothesis.target_project_id == body.target_project_id,
            )
            .first()
        )
        if existing is not None:
            return existing
        raise
    db.refresh(hypothesis)
    return hypothesis


@router.get("/suggestions", response_model=list[CrossProjectHypothesisOut])
def list_suggestions(
    target_project_id: int = Query(...),
    status: str = Query("suggested"),
    db: Session = Depends(get_db),
):
    """Fetch pending (or any-status) hypotheses for a project's review."""
    return (
        db.query(CrossProjectHypothesis)
        .filter(
            CrossProjectHypothesis.target_project_id == target_project_id,
            CrossProjectHypothesis.status == status,
        )
        .order_by(CrossProjectHypothesis.similarity_score.desc())
        .all()
    )


@router.post("/{hypothesis_id}/accept", response_model=CrossProjectHypothesisOut)
def accept(hypothesis_id: int, db: Session = Depends(get_db)):
    """Accept a suggestion — clones the source entity into the target project's KG.

    Preserves the source name + description as the seed; the target project's
    next research run will grow observations on the new entity through its own
    retrieval. We don't copy observations across because they were cited from
    sources relevant to the source project — forcing evidence to be re-learned
    on the target's retrieval is the point of the human gate.
    """
    h = db.get(CrossProjectHypothesis, hypothesis_id)
    if h is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    if h.status != "suggested":
        raise HTTPException(status_code=400, detail=f"already {h.status}")

    # Clone the entity into the target project — guard against accidental dupes
    # on re-accept via canonical_name match on the target.
    canonical = (h.source_entity_name or "").lower().strip()
    existing = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == h.target_project_id,
            KnowledgeEntity.canonical_name == canonical,
        )
        .first()
    )
    if existing is None:
        new_entity = KnowledgeEntity(
            project_id=h.target_project_id,
            entity_type="trend",
            name=h.source_entity_name,
            canonical_name=canonical,
            description=h.source_description,
            source_agent="xproj_accept",
            confidence=0.4,  # low — target project must re-validate
        )
        db.add(new_entity)

    h.status = "accepted"
    h.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(h)
    return h


@router.post("/{hypothesis_id}/reject", response_model=CrossProjectHypothesisOut)
def reject(hypothesis_id: int, db: Session = Depends(get_db)):
    """Reject a suggestion. The source entity's canonical is remembered so
    future planner passes on the target project treat it as a dismissed pattern."""
    h = db.get(CrossProjectHypothesis, hypothesis_id)
    if h is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    if h.status != "suggested":
        raise HTTPException(status_code=400, detail=f"already {h.status}")
    h.status = "rejected"
    h.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(h)
    return h
