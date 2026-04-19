"""Cost + quota summary endpoint. Reads the cost_ledger table.

GET /api/cost/summary?window_days=30 → per-provider rollup with quota pct.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from utils import cost_tracker

router = APIRouter(tags=["cost"])


@router.get("/api/cost/summary")
def get_summary(window_days: int = Query(30, ge=1, le=365)) -> dict:
    return cost_tracker.summary(window_days=window_days)
