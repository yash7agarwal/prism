"""HTTP client for Loupe — the UAT/verification sibling.

Prism calls Loupe read-only to gather build-side evidence (UAT verdicts,
Figma frames, match scores) that complements its own market intelligence.
The output is consumed by `agent/prd_synthesizer.py`.

Design notes:
- **Graceful by default.** If Loupe is unreachable, any call returns a
  structured empty bundle (`{"available": False, ...}`). Callers never
  have to wrap in try/except.
- **Read-only.** We never POST, PUT, or DELETE against Loupe.
- **Feature fuzzy-match.** Loupe has no free-text feature index; we match
  against `UatRun.feature_description` + `TestPlan.feature_description`
  via Python substring (case-insensitive).
- **Single base URL.** `LOUPE_API_URL` env (default `http://localhost:8001`).
  On Railway, set to the internal URL of a deployed Loupe service.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://localhost:8001"
TIMEOUT_S = 10


def _base_url() -> str:
    return (os.environ.get("LOUPE_API_URL") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    h = {"accept": "application/json"}
    token = os.environ.get("LOUPE_API_TOKEN", "").strip()
    if token:
        h["authorization"] = f"Bearer {token}"
    return h


def is_reachable() -> bool:
    """Quick health probe. Non-raising."""
    try:
        r = httpx.get(f"{_base_url()}/health", timeout=3, headers=_headers())
        return r.status_code < 500
    except Exception:
        return False


def list_uat_runs(project_id: int) -> list[dict]:
    """All UatRun rows for a project. Empty list on any failure."""
    try:
        r = httpx.get(
            f"{_base_url()}/api/projects/{project_id}/uat/runs",
            timeout=TIMEOUT_S, headers=_headers(),
        )
        if r.status_code != 200:
            return []
        return r.json() or []
    except Exception as exc:
        logger.debug("[loupe] list_uat_runs failed: %s", exc)
        return []


def get_uat_run(run_id: int) -> dict | None:
    """Full UatRun detail including nested frame results (if the schema includes them).

    Returns None on 404 / network failure so the caller can treat it as
    "no data" without an exception path.
    """
    try:
        r = httpx.get(
            f"{_base_url()}/api/uat/runs/{run_id}",
            timeout=TIMEOUT_S, headers=_headers(),
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:
        logger.debug("[loupe] get_uat_run(%d) failed: %s", run_id, exc)
        return None


def get_uat_report_md(run_id: int) -> str:
    """Plain-text markdown report for a run. Empty string on failure."""
    try:
        r = httpx.get(
            f"{_base_url()}/api/uat/runs/{run_id}/report.md",
            timeout=TIMEOUT_S, headers=_headers(),
        )
        return r.text if r.status_code == 200 else ""
    except Exception as exc:
        logger.debug("[loupe] get_uat_report_md(%d) failed: %s", run_id, exc)
        return ""


def get_figma_import(import_id: int) -> dict | None:
    """Figma import detail (status + frame list). None on failure."""
    try:
        r = httpx.get(
            f"{_base_url()}/api/figma/imports/{import_id}",
            timeout=TIMEOUT_S, headers=_headers(),
        )
        return r.json() if r.status_code == 200 else None
    except Exception as exc:
        logger.debug("[loupe] get_figma_import(%d) failed: %s", import_id, exc)
        return None


def fetch_feature_evidence(project_id: int, feature_description: str) -> dict[str, Any]:
    """Collect UAT + Figma evidence relevant to a feature for PRD synthesis.

    Matching strategy: fuzzy substring match of `feature_description` against
    each UatRun's `feature_description` (case-insensitive). All matched runs
    + their full detail are returned.

    Returns:
        {
            "available": bool,          # False if Loupe is unreachable
            "matched_runs": [run_detail, ...],   # UatRun JSON with frame results
            "figma_imports": [{import_id, file_name, status, frame_count}],
            "total_runs_for_project": int,       # so PRD can say "3 of 47 runs matched"
            "feature_tokens": str,               # what we matched against
        }
    """
    if not is_reachable():
        return {
            "available": False,
            "matched_runs": [],
            "figma_imports": [],
            "total_runs_for_project": 0,
            "feature_tokens": feature_description,
        }

    needle = (feature_description or "").lower().strip()
    runs = list_uat_runs(project_id)

    matched_ids: list[int] = []
    figma_import_ids: set[int] = set()
    for r in runs:
        desc = (r.get("feature_description") or "").lower()
        if needle and needle in desc:
            matched_ids.append(r["id"])
            if r.get("figma_import_id"):
                figma_import_ids.add(r["figma_import_id"])

    # Fetch full detail (with frame results) for matched runs only — keeps
    # the synthesis payload bounded.
    matched_runs = []
    for rid in matched_ids[:5]:  # cap — 5 runs × ~20 frames is already a lot
        detail = get_uat_run(rid)
        if detail:
            matched_runs.append(detail)

    figma_imports = []
    for fid in list(figma_import_ids)[:3]:
        imp = get_figma_import(fid)
        if imp:
            figma_imports.append({
                "import_id": imp.get("id"),
                "file_name": imp.get("file_name"),
                "status": imp.get("status"),
                "frame_count": imp.get("total_frames"),
            })

    return {
        "available": True,
        "matched_runs": matched_runs,
        "figma_imports": figma_imports,
        "total_runs_for_project": len(runs),
        "feature_tokens": feature_description,
    }
