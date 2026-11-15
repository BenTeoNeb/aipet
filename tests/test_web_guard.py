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


H = {"X-Pet": "1", "Origin": "http://localhost:7860"}


def test_bad_bodies_and_names_are_client_errors(client):
    assert client.post("/debug/parents/seen", json=["not", "a", "dict"], headers=H).status_code == 422
    assert client.post("/debug/memory/edit", json={"change": {}}, headers=H).status_code == 422
    assert client.get("/debug/parents/thread/..%2Fx.jsonl", headers=H).status_code in (400, 404)
    assert client.get("/debug/tech/latency/nope.jsonl", headers=H).status_code == 404


def test_closing_an_old_tab_keeps_the_new_tabs_session(tmp_path, monkeypatch):
    import asyncio

    from pipecat.processors.frame_processor import FrameProcessor

    from aipet import session_log, web

    class Transport:
        def input(self):
            return FrameProcessor()

        def output(self):
            return FrameProcessor()

    monkeypatch.setenv("PET_HOME", str(tmp_path))
    monkeypatch.setattr(session_log, "SESSIONS", tmp_path / "sessions")

    async def go():
        old, new = (web.WebSession(Transport(), mute_while_speaking=False) for _ in range(2))
        for s in (old, new):
            s.worker.cancel = lambda: asyncio.sleep(0)
        web.SESSION.update(session=new, conversation=new.conversation)
        await old.close("browser disconnected")
        assert web.SESSION["session"] is new
        await new.close("browser disconnected")
        assert not web.SESSION

    asyncio.run(go())
