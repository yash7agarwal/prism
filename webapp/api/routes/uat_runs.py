"""UAT run routes — the primary execution + report endpoints.

Unlike plans, which are just generated lists of test cases, UAT runs actually
drive the app via VisionNavigator and produce a per-frame comparison against
the Figma spec. The output of a run is the deliverable.

Endpoints:
- POST   /api/projects/{id}/uat/runs            start a run (sync, 60-120s)
- GET    /api/projects/{id}/uat/runs            list runs for a project
- GET    /api/uat/runs/{run_id}                 detail with frame_results
- GET    /api/uat/runs/{run_id}/report.md       markdown report file
- DELETE /api/uat/runs/{run_id}
- GET    /api/uat/runs/{run_id}/frames/{frame_id}/figma_image
- GET    /api/uat/runs/{run_id}/frames/{frame_id}/app_screenshot
- GET    /api/uat/runs/{run_id}/frames/{frame_id}/diff_image
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import get_db
from webapp.api.services.uat_runner import run_uat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uat_runs"])


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/api/projects/{project_id}/uat/runs",
    response_model=list[schemas.UatRunSummary],
)
def list_runs(project_id: int, db: Session = Depends(get_db)):
    return (
        db.query(models.UatRun)
        .filter(models.UatRun.project_id == project_id)
        .order_by(models.UatRun.started_at.desc())
        .all()
    )


@router.get("/api/uat/runs/{run_id}", response_model=schemas.UatRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.UatRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/api/uat/runs/{run_id}/report.md", response_class=PlainTextResponse)
def get_run_report_md(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.UatRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.report_md_path or not Path(run.report_md_path).exists():
        raise HTTPException(status_code=404, detail="Report file not on disk")
    return Path(run.report_md_path).read_text()


# ---------------------------------------------------------------------------
# Image serving
# ---------------------------------------------------------------------------


def _serve_image(path: str | None) -> FileResponse:
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Image not found on disk")
    return FileResponse(path, media_type="image/png")


@router.get("/api/uat/runs/{run_id}/frames/{frame_id}/figma_image")
def get_figma_image(run_id: int, frame_id: int, db: Session = Depends(get_db)):
    fr = db.get(models.UatFrameResult, frame_id)
    if not fr or fr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Frame not found")
    return _serve_image(fr.figma_image_path)


@router.get("/api/uat/runs/{run_id}/frames/{frame_id}/app_screenshot")
def get_app_screenshot(run_id: int, frame_id: int, db: Session = Depends(get_db)):
    fr = db.get(models.UatFrameResult, frame_id)
    if not fr or fr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Frame not found")
    return _serve_image(fr.app_screenshot_path)


@router.get("/api/uat/runs/{run_id}/frames/{frame_id}/diff_image")
def get_diff_image(run_id: int, frame_id: int, db: Session = Depends(get_db)):
    fr = db.get(models.UatFrameResult, frame_id)
    if not fr or fr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Frame not found")
    return _serve_image(fr.diff_image_path)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/uat/runs",
    response_model=schemas.UatRunOut,
    status_code=201,
)
def create_run(
    project_id: int,
    payload: schemas.UatRunCreate,
    db: Session = Depends(get_db),
):
    """Start a synchronous UAT run.

    Takes 60-120s for a typical 4-frame project. Returns the completed run
    with frame_results populated. Frontend should use a longer timeout
    (at least 300s) when calling this endpoint.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    run = run_uat(
        project_id=project_id,
        apk_path=payload.apk_path,
        figma_file_id=payload.figma_file_id,
        feature_description=payload.feature_description,
        db=db,
        skip_install=payload.skip_install,
    )
    return run


@router.delete("/api/uat/runs/{run_id}", status_code=204)
def delete_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.UatRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
