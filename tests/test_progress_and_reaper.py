"""v0.20.0: project-progress aggregator + manual orphan reaper.

These tests exercise the new endpoints with an in-memory SQLite so we don't
depend on live Railway. The startup reaper itself is exercised indirectly
through the manual reaper, which uses the same logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.api.db import Base, get_db
from webapp.api.main import app
from webapp.api.models import Project, WorkItem


@pytest.fixture
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test_progress.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), TestingSession
    app.dependency_overrides.clear()


def _seed(session_factory, n_pending=0, n_in_prog_fresh=0, n_in_prog_stale=0,
          n_completed=0, n_failed=0, completed_durations_s=None) -> int:
    """Create a project + work items and return project_id."""
    db = session_factory()
    try:
        p = Project(name="Test Project", description="t")
        db.add(p)
        db.commit()
        db.refresh(p)
        now = datetime.utcnow()
        rows: list[WorkItem] = []
        for _ in range(n_pending):
            rows.append(WorkItem(project_id=p.id, agent_type="intel",
                                 category="industry_identification", description="x",
                                 status="pending"))
        for _ in range(n_in_prog_fresh):
            rows.append(WorkItem(project_id=p.id, agent_type="intel",
                                 category="competitor_profile", description="x",
                                 status="in_progress",
                                 started_at=now - timedelta(seconds=30),
                                 last_progress_at=now - timedelta(seconds=30)))
        for _ in range(n_in_prog_stale):
            rows.append(WorkItem(project_id=p.id, agent_type="intel",
                                 category="competitor_profile", description="x",
                                 status="in_progress",
                                 started_at=now - timedelta(hours=4),
                                 last_progress_at=now - timedelta(hours=4)))
        for i in range(n_completed):
            dur = (completed_durations_s or [60])[i % len(completed_durations_s or [60])]
            rows.append(WorkItem(project_id=p.id, agent_type="intel",
                                 category="competitor_profile", description="x",
                                 status="completed",
                                 started_at=now - timedelta(seconds=dur + 10),
                                 completed_at=now - timedelta(seconds=10),
                                 last_progress_at=now - timedelta(seconds=10)))
        for _ in range(n_failed):
            rows.append(WorkItem(project_id=p.id, agent_type="intel",
                                 category="x", description="x", status="failed",
                                 completed_at=now))
        db.add_all(rows)
        db.commit()
        return p.id
    finally:
        db.close()


def test_progress_empty_project(client):
    c, sf = client
    pid = _seed(sf)
    r = c.get(f"/api/knowledge/project-progress?project_id={pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["percent_complete"] == 0.0
    assert body["estimated_minutes_remaining"] is None
    assert body["stalled"] == 0


def test_progress_counts_match(client):
    c, sf = client
    pid = _seed(sf, n_pending=5, n_in_prog_fresh=1, n_completed=3, n_failed=2)
    r = c.get(f"/api/knowledge/project-progress?project_id={pid}")
    body = r.json()
    assert body["pending"] == 5
    assert body["in_progress"] == 1
    assert body["completed"] == 3
    assert body["failed"] == 2
    assert body["total"] == 11
    # 3 completed of 11 total = 27.3%
    assert body["percent_complete"] == 27.3


def test_progress_stalled_count_excludes_fresh(client):
    """Fresh in_progress (heartbeat <10m) should NOT count as stalled."""
    c, sf = client
    pid = _seed(sf, n_in_prog_fresh=2, n_in_prog_stale=3)
    body = c.get(f"/api/knowledge/project-progress?project_id={pid}").json()
    assert body["in_progress"] == 5
    assert body["stalled"] == 3


def test_progress_eta_needs_min_5_samples(client):
    """ETA is None if fewer than 5 completed samples."""
    c, sf = client
    pid = _seed(sf, n_pending=10, n_completed=4, completed_durations_s=[60])
    body = c.get(f"/api/knowledge/project-progress?project_id={pid}").json()
    assert body["avg_item_seconds"] is None
    assert body["estimated_minutes_remaining"] is None


def test_progress_eta_calculated_from_avg(client):
    """Avg 60s per item × 10 pending = 10 minutes ETA."""
    c, sf = client
    pid = _seed(sf, n_pending=10, n_completed=5,
                completed_durations_s=[60, 60, 60, 60, 60])
    body = c.get(f"/api/knowledge/project-progress?project_id={pid}").json()
    assert body["avg_item_seconds"] == pytest.approx(60.0, rel=0.05)
    assert body["estimated_minutes_remaining"] == 10


def test_reap_orphans_marks_only_stale(client):
    """Reaper must leave fresh in_progress alone, fail only stale ones."""
    c, sf = client
    pid = _seed(sf, n_in_prog_fresh=2, n_in_prog_stale=3)
    r = c.post(f"/api/knowledge/work-items/reap-orphans?project_id={pid}")
    assert r.status_code == 200
    assert r.json()["reaped"] == 3
    body = c.get(f"/api/knowledge/project-progress?project_id={pid}").json()
    # 3 stale → failed; 2 fresh stay in_progress
    assert body["in_progress"] == 2
    assert body["failed"] == 3
    assert body["stalled"] == 0


def test_reap_orphans_idempotent(client):
    """Calling twice in a row reaps zero on the second call."""
    c, sf = client
    pid = _seed(sf, n_in_prog_stale=2)
    assert c.post(f"/api/knowledge/work-items/reap-orphans?project_id={pid}").json()["reaped"] == 2
    assert c.post(f"/api/knowledge/work-items/reap-orphans?project_id={pid}").json()["reaped"] == 0
