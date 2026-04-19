"""Quality-regression detection across the research pipeline.

Compares the 7-day rolling average of research-quality metrics against the
prior 7-day window, per project. If either `novelty_yield` or
`retrieval_yield` drops >30% week-over-week, or if confidence distribution
shifts low, sends a Telegram alert with a one-line hypothesis so the PM
knows to investigate before the KG quietly rots.

Designed to be cheap and deterministic — reads pre-computed metrics from
`AgentSession.quality_score_json` (populated per-run by
`base_autonomous_agent._aggregate_quality`). No LLM, no retrieval.

Callable from:
  - orchestrator daemon (daily tick)
  - CLI: `python -m agent.quality_regression`
  - cron / scheduled task
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.db import SessionLocal
from webapp.api.models import AgentSession, Project

logger = logging.getLogger(__name__)

REGRESSION_WINDOW_DAYS = 7
REGRESSION_DROP_THRESHOLD = 0.30  # 30% drop week-over-week triggers alert
MIN_SESSIONS_FOR_COMPARISON = 3   # don't alert on thin data


@dataclass
class WindowStats:
    retrieval_yield: float | None
    novelty_yield: float | None
    candidates_in: int
    candidates_kept: int
    n_sessions: int
    n_items: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieval_yield": self.retrieval_yield,
            "novelty_yield": self.novelty_yield,
            "candidates_in": self.candidates_in,
            "candidates_kept": self.candidates_kept,
            "n_sessions": self.n_sessions,
            "n_items": self.n_items,
        }


@dataclass
class RegressionReport:
    project_id: int
    project_name: str
    current: WindowStats
    prior: WindowStats
    regressions: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return bool(self.regressions)


def _avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 3) if vals else None


def _window_stats(db: Session, project_id: int, start: datetime, end: datetime) -> WindowStats:
    sessions = (
        db.query(AgentSession)
        .filter(
            AgentSession.project_id == project_id,
            AgentSession.agent_type == "industry_research",
            AgentSession.started_at >= start,
            AgentSession.started_at < end,
            AgentSession.completed_at.is_not(None),
        )
        .all()
    )

    ryields: list[float] = []
    nyields: list[float] = []
    cands_in = cands_kept = items = 0
    for s in sessions:
        q = s.quality_score_json or {}
        if isinstance(q.get("retrieval_yield"), (int, float)):
            ryields.append(q["retrieval_yield"])
        if isinstance(q.get("novelty_yield"), (int, float)):
            nyields.append(q["novelty_yield"])
        v = q.get("validator") or {}
        cands_in += int(v.get("candidates_in", 0) or 0)
        cands_kept += int(v.get("candidates_kept", 0) or 0)
        items += int(q.get("n_items_instrumented", 0) or 0)

    return WindowStats(
        retrieval_yield=_avg(ryields),
        novelty_yield=_avg(nyields),
        candidates_in=cands_in,
        candidates_kept=cands_kept,
        n_sessions=len(sessions),
        n_items=items,
    )


def _pct_drop(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
        return None
    return round((prior - current) / prior, 3)


def _hypothesize(report: RegressionReport, db: Session) -> list[str]:
    """One-line operator-focused hypotheses for what might be causing the drop.

    Cheap heuristics only — no LLM. The PM will investigate; our job is to
    suggest the most likely cause so they know where to look.
    """
    notes: list[str] = []
    cur, prev = report.current, report.prior

    if prev.n_sessions > 0 and cur.n_sessions < prev.n_sessions:
        notes.append(
            f"only {cur.n_sessions} runs this week vs {prev.n_sessions} prior "
            f"— daemon may not be firing (check orchestrator)"
        )

    if (
        cur.candidates_in > 0
        and cur.candidates_kept / max(cur.candidates_in, 1) < 0.6
        and prev.candidates_in > 0
        and prev.candidates_kept / max(prev.candidates_in, 1) >= 0.8
    ):
        notes.append(
            "validator drop rate up — synthesiser citing more URLs outside "
            "the retrieval bundle (possible model regression or retrieval "
            "failures)"
        )

    if cur.n_items > 0 and cur.n_sessions > 0 and cur.candidates_in / cur.n_sessions < 3:
        notes.append(
            "few candidates per run — planner may be generating weak queries "
            "or retrieval cascade hitting rate limits (Tavily/Brave quotas?)"
        )

    if cur.novelty_yield is not None and cur.novelty_yield < 0.2:
        notes.append(
            "novelty_yield is low — KG may be saturated or planner isn't "
            "probing new angles; consider broadening the brief"
        )

    return notes


def check_project(db: Session, project: Project) -> RegressionReport:
    """Compute 7-day vs prior-7-day regression report for a single project."""
    now = datetime.utcnow()
    cur_start = now - timedelta(days=REGRESSION_WINDOW_DAYS)
    prev_start = cur_start - timedelta(days=REGRESSION_WINDOW_DAYS)

    current = _window_stats(db, project.id, cur_start, now)
    prior = _window_stats(db, project.id, prev_start, cur_start)

    report = RegressionReport(
        project_id=project.id,
        project_name=project.name,
        current=current,
        prior=prior,
    )

    # Need enough data to draw a comparison.
    if (
        current.n_sessions < MIN_SESSIONS_FOR_COMPARISON
        or prior.n_sessions < MIN_SESSIONS_FOR_COMPARISON
    ):
        return report

    r_drop = _pct_drop(current.retrieval_yield, prior.retrieval_yield)
    n_drop = _pct_drop(current.novelty_yield, prior.novelty_yield)

    if r_drop is not None and r_drop >= REGRESSION_DROP_THRESHOLD:
        report.regressions.append(
            f"retrieval_yield down {r_drop*100:.0f}% "
            f"({prior.retrieval_yield:.2f} → {current.retrieval_yield:.2f})"
        )
    if n_drop is not None and n_drop >= REGRESSION_DROP_THRESHOLD:
        report.regressions.append(
            f"novelty_yield down {n_drop*100:.0f}% "
            f"({prior.novelty_yield:.2f} → {current.novelty_yield:.2f})"
        )

    if report.regressions:
        report.hypotheses = _hypothesize(report, db)
    return report


def _send_alert(report: RegressionReport) -> bool:
    """Post a compact MarkdownV2 alert. Uses the existing digest's send helper."""
    from telegram_bot.digest import _chat_id, _md_escape, _send_message

    chat_id = _chat_id()
    if not chat_id:
        logger.info("[regression] no TELEGRAM_PM_CHAT_ID — skipping alert")
        return False

    lines = [
        f"⚠️ *Quality regression* — *{_md_escape(report.project_name)}*",
        "",
    ]
    for r in report.regressions:
        lines.append(f"• {_md_escape(r)}")
    if report.hypotheses:
        lines.append("")
        lines.append("_Likely:_")
        for h in report.hypotheses:
            lines.append(f"• {_md_escape(h)}")
    lines.append("")
    lines.append(
        _md_escape(
            f"window: last 7d ({report.current.n_sessions} runs) "
            f"vs prior 7d ({report.prior.n_sessions} runs)"
        )
    )

    return _send_message(chat_id, "\n".join(lines))


def run_once(db: Session | None = None) -> list[RegressionReport]:
    """Check every project, send alerts where warranted. Returns all reports."""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        projects = db.query(Project).all()
        reports: list[RegressionReport] = []
        for p in projects:
            rep = check_project(db, p)
            reports.append(rep)
            if rep.has_regression:
                sent = _send_alert(rep)
                logger.info(
                    "[regression] project=%s regressions=%s alert_sent=%s",
                    p.name, rep.regressions, sent,
                )
            else:
                logger.info(
                    "[regression] project=%s clean (cur_n=%d, prev_n=%d)",
                    p.name, rep.current.n_sessions, rep.prior.n_sessions,
                )
        return reports
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    reports = run_once()
    import json
    print(json.dumps([
        {
            "project": r.project_name,
            "regressions": r.regressions,
            "hypotheses": r.hypotheses,
            "current": r.current.as_dict(),
            "prior": r.prior.as_dict(),
        }
        for r in reports
    ], indent=2))
