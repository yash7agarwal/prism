"""Digest endpoints — preview + send (Telegram).

GET  /api/digest/preview?project_id=X → returns the digest text (no push)
POST /api/digest/send?project_id=X    → generates + pushes to Telegram
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/digest", tags=["digest"])


@router.get("/preview")
def preview(project_id: int = Query(..., ge=1)) -> dict:
    from agent.product_os_orchestrator import get_orchestrator
    try:
        digest = get_orchestrator(project_id).generate_daily_digest()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"digest failed: {exc}")
    return {"project_id": project_id, "digest": digest, "length": len(digest or "")}


@router.post("/send")
def send(project_id: int = Query(..., ge=1)) -> dict:
    from agent.digest_runner import DigestRunner
    from webapp.api.db import SessionLocal
    db = SessionLocal()
    try:
        runner = DigestRunner(project_id, db)
        return runner.run_session()
    finally:
        db.close()
