"""Test plan routes — generate, list, edit, approve.

Supports multiple plan types via a planner registry:
- feature_flow: generic happy path + branches + edge cases (LLM)
- design_fidelity: Figma comparison for visual/content/persuasion (vision LLM)
- functional_flow: per-element click verification (deterministic)
- deeplink_utility: graph integrity checks (deterministic)
- edge_cases: empty/error/slow/long content per screen (batched LLM)

The /suite endpoint runs all applicable planners in one call.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import get_db
from webapp.api.services.deeplink_utility_planner import generate_deeplink_utility_plan
from webapp.api.services.edge_cases_planner import generate_edge_cases_plan
from webapp.api.services.figma_test_planner import generate_figma_test_plan
from webapp.api.services.functional_flow_planner import generate_functional_flow_plan
from webapp.api.services.test_planner import generate_test_plan

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plans"])

# Plan types supported by the planner registry
PLAN_TYPES = {
    "feature_flow",
    "design_fidelity",
    "functional_flow",
    "deeplink_utility",
    "edge_cases",
}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/api/projects/{project_id}/plans", response_model=list[schemas.TestPlanOut])
def list_plans(project_id: int, db: Session = Depends(get_db)):
    plans = (
        db.query(models.TestPlan)
        .filter(models.TestPlan.project_id == project_id)
        .order_by(models.TestPlan.created_at.desc())
        .all()
    )
    return plans


@router.get("/api/plans/{plan_id}", response_model=schemas.TestPlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(models.TestPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


# ---------------------------------------------------------------------------
# Writes — single plan
# ---------------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/plans",
    response_model=schemas.TestPlanOut,
    status_code=201,
)
def create_plan(
    project_id: int,
    payload: schemas.TestPlanCreate,
    db: Session = Depends(get_db),
):
    """Generate a single plan of the requested type.

    If `plan_type` is not specified, defaults to `design_fidelity` when a
    figma_file_id is provided, else `feature_flow`.
    """
    plan_type = _resolve_plan_type(payload)
    plan = _generate_and_persist_plan(
        project_id=project_id,
        feature_description=payload.feature_description,
        plan_type=plan_type,
        figma_file_id=payload.figma_file_id,
        voice_transcript=payload.voice_transcript,
        db=db,
    )
    return plan


# ---------------------------------------------------------------------------
# Writes — suite (all plan types in one call)
# ---------------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/plans/suite",
    response_model=list[schemas.TestPlanOut],
    status_code=201,
)
def create_suite(
    project_id: int,
    payload: schemas.SuiteCreate,
    db: Session = Depends(get_db),
):
    """Generate a comprehensive UAT suite — one plan per plan_type.

    Runs the planners sequentially with throttling between them to respect
    Gemini RPM limits. Skips `design_fidelity` if no figma_file_id is provided.
    Returns all generated plans in one list.
    """
    # Determine which plan types to run
    plan_types = ["functional_flow", "deeplink_utility", "edge_cases"]
    if payload.figma_file_id:
        plan_types.insert(0, "design_fidelity")

    plans: list[models.TestPlan] = []
    for i, ptype in enumerate(plan_types):
        try:
            plan = _generate_and_persist_plan(
                project_id=project_id,
                feature_description=payload.feature_description,
                plan_type=ptype,
                figma_file_id=payload.figma_file_id,
                voice_transcript=payload.voice_transcript,
                db=db,
            )
            plans.append(plan)
            logger.info(f"[suite] {ptype}: {len(plan.cases)} cases (plan {plan.id})")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"[suite] {ptype} failed: {exc}")
            # Continue with other planners — partial success is better than all-or-nothing
        # Throttle between planners (skip after the last one).
        # 2s is enough between Gemini calls since most planners are deterministic.
        if i < len(plan_types) - 1:
            time.sleep(2)

    if not plans:
        raise HTTPException(
            status_code=500,
            detail="All planners failed — check backend logs for details.",
        )
    return plans


# ---------------------------------------------------------------------------
# Plan + case updates
# ---------------------------------------------------------------------------


@router.patch("/api/plans/{plan_id}", response_model=schemas.TestPlanOut)
def update_plan(plan_id: int, status: str, db: Session = Depends(get_db)):
    plan = db.get(models.TestPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if status not in {"draft", "approved"}:
        raise HTTPException(status_code=400, detail="status must be 'draft' or 'approved'")
    plan.status = status
    db.commit()
    db.refresh(plan)
    return plan


@router.patch("/api/cases/{case_id}", response_model=schemas.TestCaseOut)
def update_case(
    case_id: int,
    payload: schemas.TestCaseUpdate,
    db: Session = Depends(get_db),
):
    case = db.get(models.TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/api/cases/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    db.delete(case)
    db.commit()


@router.delete("/api/projects/{project_id}/plans", status_code=200)
def bulk_delete_plans(
    project_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Bulk delete plans — useful for cleaning up accumulated noise.

    If `status` is provided, only plans with that status are deleted. Otherwise,
    ALL plans for the project are wiped.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    q = db.query(models.TestPlan).filter(models.TestPlan.project_id == project_id)
    if status:
        q = q.filter(models.TestPlan.status == status)
    deleted = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.delete("/api/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(models.TestPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan)
    db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_plan_type(payload: schemas.TestPlanCreate) -> str:
    """Decide which planner to run based on the payload."""
    if payload.plan_type:
        if payload.plan_type not in PLAN_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plan_type. Must be one of: {sorted(PLAN_TYPES)}",
            )
        return payload.plan_type
    # Back-compat: figma_file_id implies design_fidelity; otherwise feature_flow
    return "design_fidelity" if payload.figma_file_id else "feature_flow"


def _generate_and_persist_plan(
    project_id: int,
    feature_description: str,
    plan_type: str,
    figma_file_id: str | None,
    voice_transcript: str | None,
    db: Session,
) -> models.TestPlan:
    """Run the requested planner, persist the plan + cases, return the TestPlan."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    screens = (
        db.query(models.Screen).filter(models.Screen.project_id == project_id).all()
    )
    if not screens:
        raise HTTPException(
            status_code=400,
            detail="Cannot generate a plan: no screens uploaded yet for this project.",
        )
    edges = db.query(models.Edge).filter(models.Edge.project_id == project_id).all()

    screens_data = [
        {
            "id": s.id,
            "name": s.name,
            "display_name": s.display_name,
            "purpose": s.purpose,
            "screenshot_path": s.screenshot_path,
            "elements": s.elements or [],
            "context_hints": s.context_hints,
        }
        for s in screens
    ]
    edges_data = [
        {
            "from_screen_id": e.from_screen_id,
            "to_screen_id": e.to_screen_id,
            "trigger": e.trigger,
        }
        for e in edges
    ]

    # Dispatch to the selected planner
    cases: list[dict] = []
    try:
        if plan_type == "feature_flow":
            cases = generate_test_plan(
                feature_description=feature_description,
                screens=screens_data,
                edges=edges_data,
            )
        elif plan_type == "design_fidelity":
            if not figma_file_id:
                raise HTTPException(
                    status_code=400,
                    detail="design_fidelity plan_type requires figma_file_id",
                )
            cases = generate_figma_test_plan(
                feature_description=feature_description,
                figma_file_id=figma_file_id,
                screens=screens_data,
            )
        elif plan_type == "functional_flow":
            cases = generate_functional_flow_plan(
                screens=screens_data,
                edges=edges_data,
            )
        elif plan_type == "deeplink_utility":
            cases = generate_deeplink_utility_plan(
                screens=screens_data,
                edges=edges_data,
            )
        elif plan_type == "edge_cases":
            cases = generate_edge_cases_plan(
                feature_description=feature_description,
                screens=screens_data,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown plan_type: {plan_type}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[plans] {plan_type} planner raised: {exc}")
        cases = []

    plan = models.TestPlan(
        project_id=project_id,
        feature_description=feature_description,
        voice_transcript=voice_transcript,
        status="draft",
        plan_type=plan_type,
    )
    db.add(plan)
    db.flush()  # get plan.id without commit

    name_to_id = {s.name: s.id for s in screens}
    seen_titles: set[str] = set()
    for c in cases:
        target_name = c.get("target_screen_name")
        target_id = name_to_id.get(target_name)
        title = c.get("title", "Untitled case")
        # Dedup within this plan — normalize for comparison
        dedup_key = f"{title.strip().lower()}|{target_name or ''}"
        if dedup_key in seen_titles:
            continue
        seen_titles.add(dedup_key)
        case = models.TestCase(
            plan_id=plan.id,
            title=title,
            target_screen_id=target_id,
            navigation_path=c.get("navigation_path"),
            acceptance_criteria=c.get("acceptance_criteria", ""),
            branch_label=c.get("branch_label"),
            status="proposed",
        )
        db.add(case)

    db.commit()
    db.refresh(plan)
    logger.info(
        f"[plans] {plan_type}: {len(cases)} cases for project {project_id} plan {plan.id}"
    )
    return plan
