"""Extract successful research patterns into memory/patterns.md.

A session "succeeded well" when its `quality_score_json` shows both:
  - retrieval_yield >= 0.7 (queries actually found sources)
  - novelty_yield >= 0.5 (synthesis surfaced things not already in the KG)

When that bar is cleared, the specific query templates the planner produced
are worth reusing. This module extracts them and appends to `memory/patterns.md`
so the planner prompt can reference prior wins in future runs and the user
can see what's working.

Deterministic, no LLM. Called from base_autonomous_agent.run_session after
the session record is finalized.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.models import AgentSession, KnowledgeArtifact, Project

logger = logging.getLogger(__name__)

_PATTERNS_PATH = Path(__file__).resolve().parent.parent / "memory" / "patterns.md"

RETRIEVAL_YIELD_MIN = 0.7
NOVELTY_YIELD_MIN = 0.5
HEADER = "# Successful research patterns\n\n"
_INTRO = (
    "_Auto-populated by `agent/pattern_writer.py`. Each section is a snapshot "
    "of the queries the planner produced for a research run whose "
    "retrieval_yield ≥ 0.7 and novelty_yield ≥ 0.5. Reuse these shapes when "
    "a new project's domain looks similar._\n\n"
)


@dataclass
class PatternEntry:
    project_name: str
    inferred_industry: str
    session_id: int
    retrieval_yield: float
    novelty_yield: float
    plan_queries: list[dict]
    recorded_at: str


def _latest_plan_artifact(db: Session, project_id: int) -> KnowledgeArtifact | None:
    return (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == "research_plan",
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .first()
    )


def _extract_queries(artifact: KnowledgeArtifact) -> list[dict]:
    try:
        payload = json.loads(artifact.content_md)
    except json.JSONDecodeError:
        return []
    return payload.get("queries", [])


def _format_entry(entry: PatternEntry) -> str:
    lines = [
        f"## {entry.project_name} — {entry.inferred_industry}  (session {entry.session_id})",
        "",
        f"_{entry.recorded_at}_ · retrieval_yield={entry.retrieval_yield:.2f} · "
        f"novelty_yield={entry.novelty_yield:.2f}",
        "",
    ]
    kind_groups: dict[str, list[str]] = {}
    for q in entry.plan_queries:
        kind_groups.setdefault(q.get("kind", "discovery"), []).append(q.get("query", ""))
    for kind in ("discovery", "deepening", "validation", "lateral"):
        if kind not in kind_groups:
            continue
        lines.append(f"**{kind}**")
        for q in kind_groups[kind]:
            lines.append(f"- {q}")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _already_captured(session_id: int) -> bool:
    if not _PATTERNS_PATH.exists():
        return False
    content = _PATTERNS_PATH.read_text()
    # Each entry embeds "(session <id>)" in its header.
    return f"(session {session_id})" in content


def record_if_successful(db: Session, session: AgentSession) -> bool:
    """If the session's quality crosses both thresholds, append its planner queries
    to memory/patterns.md. Returns True if an entry was written.
    """
    q = session.quality_score_json or {}
    r = q.get("retrieval_yield")
    n = q.get("novelty_yield")
    if r is None or n is None:
        return False
    if r < RETRIEVAL_YIELD_MIN or n < NOVELTY_YIELD_MIN:
        return False
    if _already_captured(session.id):
        return False

    plan_artifact = _latest_plan_artifact(db, session.project_id)
    if plan_artifact is None:
        return False
    queries = _extract_queries(plan_artifact)
    if not queries:
        return False

    project = db.get(Project, session.project_id)
    industries = q.get("inferred_industries") or []
    entry = PatternEntry(
        project_name=project.name if project else f"project-{session.project_id}",
        inferred_industry=industries[0] if industries else "unknown",
        session_id=session.id,
        retrieval_yield=float(r),
        novelty_yield=float(n),
        plan_queries=queries,
        recorded_at=datetime.utcnow().isoformat() + "Z",
    )

    _PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _PATTERNS_PATH.exists():
        _PATTERNS_PATH.write_text(HEADER + _INTRO)
    with _PATTERNS_PATH.open("a") as f:
        f.write(_format_entry(entry))
    logger.info(
        "[pattern_writer] recorded session %d — %s (%s) r=%.2f n=%.2f",
        session.id, entry.project_name, entry.inferred_industry, r, n,
    )
    return True
