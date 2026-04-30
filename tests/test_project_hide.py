"""v0.21.2: project hide / unhide / list filter + hard delete invariants."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.api.db import Base, get_db
from webapp.api.main import app


@pytest.fixture
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test_hide.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make(client, name="P"):
    r = client.post("/api/projects", json={"name": name, "description": "t"})
    assert r.status_code == 201
    return r.json()


def test_default_list_excludes_hidden(client):
    a = _make(client, "Alpha")
    b = _make(client, "Beta")
    assert client.post(f"/api/projects/{a['id']}/hide").status_code == 200
    visible = client.get("/api/projects").json()
    assert {p["id"] for p in visible} == {b["id"]}


def test_include_hidden_returns_all(client):
    a = _make(client, "Alpha")
    b = _make(client, "Beta")
    client.post(f"/api/projects/{a['id']}/hide")
    all_ = client.get("/api/projects?include_hidden=true").json()
    assert {p["id"] for p in all_} == {a["id"], b["id"]}
    by_id = {p["id"]: p for p in all_}
    assert by_id[a["id"]]["is_hidden"] is True
    assert by_id[b["id"]]["is_hidden"] is False


def test_unhide_restores_to_default_list(client):
    a = _make(client, "Alpha")
    client.post(f"/api/projects/{a['id']}/hide")
    assert client.get("/api/projects").json() == []
    r = client.post(f"/api/projects/{a['id']}/unhide")
    assert r.status_code == 200
    assert r.json()["is_hidden"] is False
    visible = client.get("/api/projects").json()
    assert {p["id"] for p in visible} == {a["id"]}


def test_hide_idempotent(client):
    a = _make(client, "Alpha")
    assert client.post(f"/api/projects/{a['id']}/hide").json()["is_hidden"] is True
    # Calling hide twice should still leave it hidden, not toggle.
    assert client.post(f"/api/projects/{a['id']}/hide").json()["is_hidden"] is True


def test_hide_unknown_project_returns_404(client):
    r = client.post("/api/projects/999999/hide")
    assert r.status_code == 404
    r = client.post("/api/projects/999999/unhide")
    assert r.status_code == 404


def test_delete_still_works_after_hide(client):
    """Hide is recoverable; delete is permanent. Both should work in
    sequence — hide then delete cleans up fully."""
    a = _make(client, "Alpha")
    client.post(f"/api/projects/{a['id']}/hide")
    r = client.delete(f"/api/projects/{a['id']}")
    assert r.status_code == 204
    # No longer present, even with include_hidden
    all_ = client.get("/api/projects?include_hidden=true").json()
    assert all(p["id"] != a["id"] for p in all_)


def test_new_project_defaults_to_visible(client):
    """A freshly-created project must NOT default to hidden."""
    a = _make(client, "Alpha")
    assert a["is_hidden"] is False
    visible_ids = {p["id"] for p in client.get("/api/projects").json()}
    assert a["id"] in visible_ids
