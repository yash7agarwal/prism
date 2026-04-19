"""Cost tracker — persist every external-API call so we can watch spend.

Every call to Groq / Claude / Gemini / Tavily should land one row in
`cost_ledger`. The API endpoint at `/api/cost/summary` reads this ledger
and produces per-provider, per-day/month rollups.

Design rules:
1. **Fail-silent.** If the DB write errors, log a warning and return — never
   raise into the caller. Cost tracking must not break the agent loop.
2. **Thread-safe.** Each call opens + closes its own `Session` so callers
   don't have to hold one.
3. **Quota warning on write.** After recording, compute current-window usage;
   log WARNING if any provider has crossed 80% of its quota.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Per-token USD rates (rough; update as pricing shifts).
# Groq Llama 3.3 is free but we track usage for quota management.
_RATES: dict[str, dict[str, float]] = {
    "claude": {"in": 3.0 / 1_000_000, "out": 15.0 / 1_000_000},
    "gemini": {"in": 0.0, "out": 0.0},  # free tier
    "groq":   {"in": 0.0, "out": 0.0},  # free tier
    "tavily": {"search": 0.0},          # free tier
}

# Quotas — keep these conservative so warnings fire early.
# Keys are provider, values are (metric, window_days, limit).
_QUOTAS: dict[str, tuple[str, int, int]] = {
    "groq":   ("calls", 1, 14_400),
    "gemini": ("calls", 1, 1_500),
    "tavily": ("search_count", 30, 1_000),
    # Claude is pay-as-you-go; no hard quota, but alert at $10/day rolling.
    "claude": ("cost", 1, 10),
}
_WARN_THRESHOLD = 0.80  # fire the warning at 80% of quota

# In-process dedupe of quota warnings so we don't log the same warning every
# call once we cross the threshold.
_warned_today: dict[str, datetime] = {}


def _estimated_cost(provider: str, tokens_in: int, tokens_out: int, search_count: int) -> float:
    rate = _RATES.get(provider, {})
    return (
        tokens_in * rate.get("in", 0.0)
        + tokens_out * rate.get("out", 0.0)
        + search_count * rate.get("search", 0.0)
    )


def record(
    provider: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    search_count: int = 0,
    call_type: str = "unknown",
    model: str | None = None,
    success: bool = True,
    error: str | None = None,
    agent_type: str | None = None,
    project_id: int | None = None,
    session_id: int | None = None,
) -> None:
    """Persist one API-call row. Fail-silent."""
    try:
        # Import lazily so this module can be imported before webapp.api.db is
        # wired (e.g. in utility modules loaded at startup).
        from webapp.api.db import SessionLocal
        from webapp.api.models import CostLedger

        cost = _estimated_cost(provider, tokens_in, tokens_out, search_count)
        db = SessionLocal()
        try:
            db.add(CostLedger(
                provider=provider,
                call_type=call_type,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                search_count=search_count,
                estimated_cost_usd=cost,
                success=success,
                error=error,
                agent_type=agent_type,
                project_id=project_id,
                session_id=session_id,
            ))
            db.commit()
        finally:
            db.close()

        _maybe_warn_quota(provider)
    except Exception as exc:  # noqa: BLE001 — fail-silent by design
        logger.warning("[cost_tracker] record() failed for %s: %s", provider, exc)


def _maybe_warn_quota(provider: str) -> None:
    """If provider is at or above 80% of its quota window, log a warning.

    De-duped: one warning per provider per 6 hours in the current process.
    """
    quota = _QUOTAS.get(provider)
    if not quota:
        return
    metric, window_days, limit = quota

    now = datetime.utcnow()
    last = _warned_today.get(provider)
    if last and (now - last) < timedelta(hours=6):
        return

    try:
        from sqlalchemy import func as sa_func
        from webapp.api.db import SessionLocal
        from webapp.api.models import CostLedger

        since = now - timedelta(days=window_days)
        db = SessionLocal()
        try:
            base = db.query(CostLedger).filter(
                CostLedger.provider == provider,
                CostLedger.recorded_at >= since,
            )
            if metric == "calls":
                used: float = base.count()
            elif metric == "search_count":
                used = float(base.with_entities(sa_func.sum(CostLedger.search_count)).scalar() or 0)
            elif metric == "cost":
                used = float(base.with_entities(sa_func.sum(CostLedger.estimated_cost_usd)).scalar() or 0.0)
            else:
                return
        finally:
            db.close()

        pct = used / limit if limit else 0.0
        if pct >= _WARN_THRESHOLD:
            logger.warning(
                "[cost_tracker] QUOTA %s: %.1f/%d %s over %dd (%.0f%%)",
                provider.upper(), used, limit, metric, window_days, pct * 100,
            )
            _warned_today[provider] = now
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cost_tracker] quota check failed for %s: %s", provider, exc)


def summary(window_days: int = 30) -> dict[str, Any]:
    """Return a per-provider rollup for `/api/cost/summary`."""
    from sqlalchemy import func as sa_func
    from webapp.api.db import SessionLocal
    from webapp.api.models import CostLedger

    since = datetime.utcnow() - timedelta(days=window_days)
    db = SessionLocal()
    try:
        rows = (
            db.query(
                CostLedger.provider,
                sa_func.count(CostLedger.id),
                sa_func.coalesce(sa_func.sum(CostLedger.tokens_in), 0),
                sa_func.coalesce(sa_func.sum(CostLedger.tokens_out), 0),
                sa_func.coalesce(sa_func.sum(CostLedger.search_count), 0),
                sa_func.coalesce(sa_func.sum(CostLedger.estimated_cost_usd), 0.0),
                sa_func.sum(
                    # errors
                    (CostLedger.success == False).cast(__import__("sqlalchemy").Integer)  # noqa: E712
                ),
            )
            .filter(CostLedger.recorded_at >= since)
            .group_by(CostLedger.provider)
            .all()
        )
    finally:
        db.close()

    providers: list[dict] = []
    for (provider, calls, t_in, t_out, searches, cost_usd, errors) in rows:
        quota = _QUOTAS.get(provider)
        quota_info: dict | None = None
        if quota:
            metric, qwin, limit = quota
            if metric == "calls":
                used = calls
            elif metric == "search_count":
                used = searches
            else:
                used = round(float(cost_usd), 2)
            quota_info = {
                "metric": metric,
                "window_days": qwin,
                "limit": limit,
                "used": used,
                "pct": round(used / limit * 100, 1) if limit else 0.0,
            }
        providers.append({
            "provider": provider,
            "calls": calls,
            "tokens_in": int(t_in),
            "tokens_out": int(t_out),
            "search_count": int(searches),
            "estimated_cost_usd": round(float(cost_usd), 4),
            "errors": int(errors or 0),
            "quota": quota_info,
        })
    return {
        "window_days": window_days,
        "since": since.isoformat(),
        "providers": providers,
    }
