"""Stats-consistency invariants — Ch.12 in LESSONS.

For every project, the counts shown on the project-detail "stats" card must
match the lengths of the corresponding list endpoints. A divergence is the
root cause of the Intuit/Sarvam.ai bug: users saw "3 competitors" but the
competitors tab was empty because two queries computed the number by
different rules.

Run locally:
    pytest tests/test_stats_consistency.py -v
Or against live Railway:
    PRISM_BASE=https://prism-api-production-18bf.up.railway.app \\
      pytest tests/test_stats_consistency.py -v

The suite is read-only — no writes, safe to run against production.
"""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("PRISM_BASE", "http://localhost:8100").rstrip("/")
TIMEOUT_S = 30


def _get(path: str):
    r = httpx.get(f"{BASE}{path}", timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def _projects() -> list[dict]:
    try:
        return _get("/api/projects")
    except Exception as exc:
        pytest.skip(f"Prism API unreachable at {BASE}: {exc}")


@pytest.fixture(scope="module")
def projects() -> list[dict]:
    return _projects()


@pytest.mark.parametrize("_fixture_anchor", [None])
def test_api_reachable(_fixture_anchor):
    health = _get("/api/health")
    assert health.get("status") == "ok"


def test_at_least_one_project(projects):
    assert len(projects) > 0, "no projects to validate"


def _project_ids(projects):
    return [p["id"] for p in projects]


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_competitor_count_matches_list(project_id):
    """stats.competitor_count must equal len(GET /competitors)."""
    detail = _get(f"/api/projects/{project_id}")
    stats_n = detail.get("stats", {}).get("competitor_count", 0)
    comps = _get(f"/api/knowledge/competitors?project_id={project_id}")
    assert stats_n == len(comps), (
        f"project {project_id} ({detail.get('name')}): "
        f"stats.competitor_count={stats_n} but /competitors returned {len(comps)}"
    )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_entity_count_matches_list(project_id):
    """stats.entity_count must equal len(GET /entities?project_id=<>)."""
    detail = _get(f"/api/projects/{project_id}")
    stats_n = detail.get("stats", {}).get("entity_count", 0)
    # /entities uses a default limit; bump it so we don't false-fail on big projects.
    entities = _get(f"/api/knowledge/entities?project_id={project_id}&limit=500")
    assert stats_n == len(entities), (
        f"project {project_id} ({detail.get('name')}): "
        f"stats.entity_count={stats_n} but /entities returned {len(entities)}"
    )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_observation_count_nonzero_when_entities_nonzero(project_id):
    """Weaker invariant: if there are entities, observations should not be negative.

    We don't assert a strict equality here because `stats.observation_count`
    aggregates across all entities of all types while the per-entity
    observation endpoint is scoped — so there isn't a single list endpoint
    to compare against. Assert only that the number is coherent (>=0) and
    that projects with zero entities also have zero observations.
    """
    detail = _get(f"/api/projects/{project_id}")
    stats = detail.get("stats", {})
    ec = stats.get("entity_count", 0)
    oc = stats.get("observation_count", 0)
    assert oc >= 0
    if ec == 0:
        assert oc == 0, (
            f"project {project_id} ({detail.get('name')}): "
            f"no entities but observation_count={oc}"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_project_detail_has_required_stats(project_id):
    """Sanity: project detail always includes the full stats dict."""
    detail = _get(f"/api/projects/{project_id}")
    stats = detail.get("stats")
    assert stats is not None, f"project {project_id} missing stats"
    for k in ("screen_count", "entity_count", "observation_count", "competitor_count"):
        assert k in stats, f"project {project_id} stats missing {k!r}"
        assert isinstance(stats[k], int), f"stats.{k} must be int, got {type(stats[k]).__name__}"
