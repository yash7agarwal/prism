"""DigestRunner — pseudo-agent that generates the daily digest and pushes it.

Plugs into the orchestrator's existing interval scheduler. Configured in
DEFAULT_CONFIG with interval_hours=24 so a digest goes out once per day per
project. Exposes the same run_session contract as other agents so
run_agent_session can drive it without special-casing.

Push target: Telegram. Reads `PRISM_DIGEST_CHAT_ID` (chat id) + the
`TELEGRAM_BOT_TOKEN` already used by the bot. If the chat id is unset,
digest is generated and logged but not pushed — the `/intel digest`
command still works for manual pulls.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class DigestRunner:
    agent_type = "digest"

    def __init__(self, project_id: int, db: Session):
        self.project_id = project_id
        self.db = db

    def run_session(self, **_kwargs: Any) -> dict:
        """Generate today's digest; push to Telegram if configured."""
        try:
            # Deferred import — orchestrator owns the digest synthesis logic.
            from agent.product_os_orchestrator import get_orchestrator

            orch = get_orchestrator(self.project_id)
            digest = orch.generate_daily_digest()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[digest] generation failed for project %s", self.project_id)
            return {"status": "error", "message": str(exc),
                    "items_completed": 0, "items_failed": 1}

        pushed = _push_to_telegram(self.project_id, digest)
        return {
            "status": "completed",
            "items_completed": 1,
            "items_failed": 0,
            "digest_length": len(digest or ""),
            "pushed": pushed,
        }


def _push_to_telegram(project_id: int, digest: str) -> dict:
    """Send the digest to the configured chat. Returns {sent: bool, reason?}."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("PRISM_DIGEST_CHAT_ID")
    if not token:
        return {"sent": False, "reason": "TELEGRAM_BOT_TOKEN not set"}
    if not chat_id:
        return {"sent": False, "reason": "PRISM_DIGEST_CHAT_ID not set"}

    try:
        from webapp.api.db import SessionLocal
        from webapp.api.models import Project
        db = SessionLocal()
        try:
            project = db.get(Project, project_id)
            name = project.name if project else f"#{project_id}"
        finally:
            db.close()

        # Telegram caps at 4096 chars per message; trim conservatively.
        body = f"*Prism digest — {name}*\n\n{digest}"
        if len(body) > 3800:
            body = body[:3800] + "\n\n…_digest truncated; open Prism for full detail_"

        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": body, "parse_mode": "Markdown"},
            timeout=15,
        )
        if resp.status_code == 200:
            return {"sent": True}
        return {"sent": False, "reason": f"telegram {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[digest] telegram push failed: %s", exc)
        return {"sent": False, "reason": str(exc)}
