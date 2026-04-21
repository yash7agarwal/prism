"""PRD synthesis endpoints.

POST /api/prd/generate → runs `agent.prd_synthesizer.generate` synchronously
(it's a single LLM call, ~30-60s). Returns the artifact_id + preview stats.
Clients that want to render the Markdown then fetch
`/api/knowledge/artifacts/{id}`.

GET /api/prd/recent → list recent PRD artifacts for a project.

Design note: we run the synthesizer **synchronously** in the request handler
rather than spawning a background thread. At one LLM call per request, the
worst case is ~60s — within FastAPI/uvicorn's default request timeout. A
future refactor can swap to BackgroundTasks if latency budgets tighten.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from agent.prd_synthesizer import PRD_ARTIFACT_TYPE, generate as _generate
from webapp.api.db import get_db
from webapp.api.models import KnowledgeArtifact
from webapp.api.schemas import (
    KnowledgeArtifactOut,
    PRDGenerateIn,
    PRDGenerateOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prd", tags=["prd"])


@router.post("/generate", response_model=PRDGenerateOut)
def generate_prd(body: PRDGenerateIn, db: Session = Depends(get_db)):
    """Synthesize a PRD for a feature. Returns the saved artifact id + stats."""
    try:
        result = _generate(db, body.project_id, body.feature_description)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("[prd] synthesis failed")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}")
    return PRDGenerateOut(
        artifact_id=result["artifact_id"],
        status="done",
        prism_evidence_count=result["prism_evidence_count"],
        loupe_evidence_available=result["loupe_evidence_available"],
        loupe_runs_matched=result["loupe_runs_matched"],
    )


@router.get("/recent", response_model=list[KnowledgeArtifactOut])
def list_recent_prds(
    project_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Most recent PRD docs for a project."""
    return (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == PRD_ARTIFACT_TYPE,
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/feature-candidates")
def feature_candidates(
    project_id: int = Query(...),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Recent TestPlans + starred Prism entities for a project — feeds the
    Telegram `/prd` (no-arg) feature picker (F1 from UX friction plan).

    Returns a single flat list so the bot can render one keyboard without
    merging logic. Each item: {source, id, label} — `label` becomes the
    feature_description passed to /generate.
    """
    from webapp.api.models import KnowledgeEntity, TestPlan

    plans = (
        db.query(TestPlan)
        .filter(TestPlan.project_id == project_id)
        .order_by(TestPlan.created_at.desc())
        .limit(limit)
        .all()
    )
    starred = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.user_signal == "starred",
        )
        .order_by(KnowledgeEntity.last_updated_at.desc())
        .limit(limit)
        .all()
    )

    items: list[dict] = []
    for p in plans:
        label = (p.feature_description or "").strip() or f"plan #{p.id}"
        items.append({"source": "plan", "id": p.id, "label": label[:80]})
    for e in starred:
        items.append({"source": "entity", "id": e.id, "label": (e.name or "")[:80]})
    # Dedupe by label (case-insensitive), preserving order
    seen: set[str] = set()
    deduped: list[dict] = []
    for it in items:
        key = it["label"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return {"candidates": deduped[:limit]}
