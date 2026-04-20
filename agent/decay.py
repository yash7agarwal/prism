"""Decay pass — flag trends whose evidence has gone stale.

An entity is "decayed" when its most recent observation is older than
DECAY_DAYS (default 60). Decayed trends get `decay_state='needs_revalidation'`
and the research brief surfaces them as validation targets so the next
planner run probes for fresh evidence (confirm, deprecate, or update).

Deterministic — no LLM, no retrieval. Called from the orchestrator daemon's
daily tick alongside the quality-regression check.

Reverse direction: when a new observation lands on a decayed entity, the
state is reset to "fresh" automatically by `_refresh_on_observation`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.db import SessionLocal
from webapp.api.models import KnowledgeEntity, KnowledgeObservation

logger = logging.getLogger(__name__)

DECAY_DAYS = 60
DECAY_TARGET_TYPES = ("trend", "regulation")


def sweep_once(db: Session | None = None) -> dict[str, Any]:
    """Mark stale entities as needs_revalidation; refresh any with new evidence.

    Returns a summary {flagged, refreshed, still_fresh} for logging.
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=DECAY_DAYS)

        candidates = (
            db.query(KnowledgeEntity)
            .filter(KnowledgeEntity.entity_type.in_(DECAY_TARGET_TYPES))
            .all()
        )

        flagged = refreshed = still_fresh = 0
        for e in candidates:
            latest = (
                db.query(KnowledgeObservation.observed_at)
                .filter(KnowledgeObservation.entity_id == e.id)
                .order_by(KnowledgeObservation.observed_at.desc())
                .first()
            )
            latest_at = latest[0] if latest else None
            is_stale = latest_at is None or latest_at < cutoff

            if is_stale and e.decay_state != "needs_revalidation":
                e.decay_state = "needs_revalidation"
                flagged += 1
            elif not is_stale and e.decay_state == "needs_revalidation":
                e.decay_state = "fresh"
                refreshed += 1
            elif not is_stale:
                still_fresh += 1
        db.commit()
        logger.info(
            "[decay] swept %d entities: flagged=%d refreshed=%d still_fresh=%d",
            len(candidates), flagged, refreshed, still_fresh,
        )
        return {
            "swept": len(candidates),
            "flagged": flagged,
            "refreshed": refreshed,
            "still_fresh": still_fresh,
        }
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import json
    print(json.dumps(sweep_once(), indent=2))
