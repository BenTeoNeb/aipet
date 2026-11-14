"""Debug routes answer only the locally served page (they expose the family's memory)."""

import pytest
from fastapi.testclient import TestClient

from aipet.web import app

ROUTES = ["/debug/state", "/debug/memory", "/debug/memory/search?q=chat", "/debug/sessions", "/debug/sessions/x.jsonl"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PET_MEMORY_DIR", str(tmp_path / "mem"))
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
def test_reads_need_the_header(client, route):
    assert client.get(route).status_code == 403


@pytest.mark.parametrize("route", ROUTES)
def test_other_origins_are_refused_even_with_the_header(client, route):
    r = client.get(route, headers={"X-Pet": "1", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_local_page_can_read(client):
    r = client.get("/debug/memory", headers={"X-Pet": "1", "Origin": "http://localhost:7860"})
    assert r.status_code == 200
    assert client.get("/debug/sessions", headers={"X-Pet": "1"}).status_code == 200  # same-origin: no Origin


def test_writes_still_guarded(client):
    assert client.post("/debug/reset").status_code == 403
    assert client.post("/config.json").status_code in (404, 405)  # unrelated route untouched
    assert client.get("/config.json").status_code == 200
