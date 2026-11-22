"""Web mode: open http://localhost:7860 in the browser.

Run: uv run bot.py
The browser handles the microphone (echo cancellation, automatic gain), so the child can
interrupt the assistant.
"""

import asyncio
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.runner.run import app, main
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner
from pydantic import BaseModel

from aipet import character, engines, env, migrate, monitoring, parents, paths, session_log, skills
from aipet.config import load_config
from aipet.energy import Energy
from aipet.memory.forget import ForgetRequest
from aipet.prompts import build_system_prompt
from aipet.rhythm import TZ, Rhythm
from aipet.session import Session

STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
HANG_UP_FALLBACK_SECS = 5  # the page closes the call itself; the server does it if the page did not


# Our page replaces the runner's default UI (routes declared before main()).
@app.get("/", include_in_schema=False)
def index():
    return HTMLResponse(render_index(character.of(load_config())))


def render_index(pet) -> str:
    """The page with this pet's name and face (face.svg / face.css from its pack)."""
    page = (STATIC / "index.html").read_text()
    face = pet.face_dir
    css = (face / "face.css").read_text() if (face / "face.css").exists() else ""
    svg = (face / "face.svg").read_text() if (face / "face.svg").exists() else ""
    return (
        page.replace("/*PET_FACE_CSS*/", css)
        .replace("<!--PET_FACE_SVG-->", svg)
        .replace("PET_NAME", html.escape(pet.name))
    )


@app.get("/config.json", include_in_schema=False)
def public_config():
    cfg = load_config()
    return {"name": character.of(cfg).name}


# --- Debug: state of the current session ------------------------------------
# Route handlers that only read files are plain `def`: FastAPI runs them in its thread pool, so a
# parent browsing the dashboard never blocks the event loop that carries the live audio.
SESSION: dict = {}  # the latest browser session (context, conversation, started, session)


def _jsonable(obj):
    try:
        return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", repr(o))))
    except Exception:
        return repr(obj)


@app.get("/debug/state", include_in_schema=False)
async def debug_state():
    cfg = load_config()
    ctx = SESSION.get("context")
    conversation = SESSION.get("conversation")
    return {
        "engines": engines.describe(cfg),
        "listening": cfg.get("listening", {}),
        "session": {
            "active": ctx is not None,
            "since": SESSION.get("started"),
        },
        "system_prompt": conversation.system_prompt() if conversation else build_system_prompt(cfg),
        "speakers": conversation.who.state() if conversation else None,
        "skills": (conversation.skills if conversation else skills.Skills.from_config(cfg)).state(),
        "energy": _energy(conversation).snapshot() if cfg.get("energy", {}).get("enabled", True) else None,
        "asleep": conversation.sleep.reason() if conversation else _asleep_outside_session(cfg),
        "condensation": conversation.memory.last if conversation else None,
        "context": [_jsonable(x) for x in ctx.get_messages(truncate_large_values=True)] if ctx else [],
    }


@app.middleware("http")
async def guard_debug_routes(request: Request, call_next):
    """Every /debug/ route (reads included: they expose the family's memory and transcripts) answers
    only the locally served page: a connection from this machine, the X-Pet header and, when present,
    a localhost Origin. (Even if the server is started with --host 0.0.0.0, the LAN gets nothing.)"""
    if request.url.path.startswith("/debug/"):
        host = request.client.host if request.client else ""
        if host not in LOOPBACK:
            return JSONResponse({"detail": "local connections only"}, status_code=403)
        try:
            _guard(request.headers.get("x-pet"), request.headers.get("origin"))
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


def _guard(x_pet: str | None, origin: str | None):
    """Debug actions: only from the locally served page. The Pipecat runner allows all origins
    (CORS *), so we also check the Origin header sent by the browser."""
    if x_pet != "1":
        raise HTTPException(403, "missing X-Pet header")
    if origin and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", origin):
        raise HTTPException(403, f"origin refused: {origin}")


def _session_file(name: str) -> Path:
    """A session log by its file name (never a path)."""
    if not re.fullmatch(r"[\w-]+\.jsonl", name):
        raise HTTPException(400, "invalid name")
    p = session_log.SESSIONS / name
    if not p.is_file():
        raise HTTPException(404, "session not found")
    return p


def _memory():
    from aipet.memory import Memory

    cfg = load_config()
    pet = character.of(cfg)
    return Memory.shared(paths.memory_dir(cfg, pet.id), pet.name)


@app.get("/debug/memory", include_in_schema=False)
def debug_memory():
    mem = _memory()
    return {
        "dir": str(mem.root),
        "files": [
            {
                "path": str(p.relative_to(mem.root)),
                "size": p.stat().st_size,
                "modified": p.stat().st_mtime,
                "content": p.read_text()[:20000],
            }
            for p in mem.files() + sorted(mem.root.glob("jeux/*.json")) + sorted(mem.root.glob("histoires/*.json"))
        ],
    }


@app.get("/debug/memory/search", include_in_schema=False)
def debug_memory_search(q: str, kind: str = "all"):
    return _memory().search(q, kind, limit=10)


@app.get("/debug/sessions", include_in_schema=False)
def debug_sessions():
    return session_log.list_sessions()


@app.get("/debug/sessions/{name}", include_in_schema=False)
def debug_session(name: str):
    ev = session_log.read_session(_session_file(name))
    return {"name": name, "summary": session_log.summarize(ev), "events": ev}


@app.post("/debug/memory/delete", include_in_schema=False)
async def debug_memory_delete(path: str):
    """Parental control: delete a memory file."""
    if not await asyncio.to_thread(_memory().delete, path):
        return {"ok": False, "reason": "file cannot be deleted"}
    conversation = SESSION.get("conversation")
    if conversation:  # the running pet forgets it right away (its system prompt held the file)
        await conversation.refresh_prompt()
    return {"ok": True}


@app.post("/debug/memory/condense", include_in_schema=False)
async def debug_memory_condense():
    conversation = SESSION.get("conversation")
    if conversation is None:
        return {"ok": False, "reason": "no session"}
    changed = await conversation.memory.checkpoint("manual")
    return {
        "ok": conversation.memory.last.get("error") is None,
        "changes": changed,
        "error": conversation.memory.last.get("error"),
    }


def _asleep_outside_session(cfg: dict) -> str | None:
    if env.get("NO_SLEEP") == "1":
        return None
    if cfg.get("energy", {}).get("enabled", True) and _energy(None).asleep:
        return "nap"
    return "night" if Rhythm.from_config(cfg).is_night(datetime.now(TZ)) else None


def _energy(conversation) -> Energy:
    """The live energy counter during a session, else the saved one (read from disk)."""
    if conversation and conversation.sleep.energy:
        return conversation.sleep.energy
    cfg = load_config()
    energy = Energy.from_config({**cfg, "energy": {**cfg.get("energy", {}), "enabled": True}})
    assert energy is not None  # enabled above
    return energy


@app.post("/debug/energy/recharge", include_in_schema=False)
async def debug_energy_recharge():
    energy = _energy(SESSION.get("conversation"))
    energy.recharge()
    return {"ok": True, **energy.snapshot()}


@app.post("/debug/reset", include_in_schema=False)
async def debug_reset():
    ctx = SESSION.get("context")
    if ctx is None:
        return {"ok": False, "reason": "no session"}
    conversation = SESSION.get("conversation")
    if conversation:
        conversation.reset_context()
    else:
        ctx.set_messages([])
    return {"ok": True}


# --- Parents' space (/parents) and technical monitoring ---------------------
@app.get("/parents", include_in_schema=False)
def parents_page():
    pet = character.of(load_config())
    return HTMLResponse((STATIC / "parents.html").read_text().replace("PET_NAME", html.escape(pet.name)))


@app.get("/adventures", include_in_schema=False)
def adventures_page():
    """The adventure explorer (map, puzzle chain, roads, games played); its data comes from the
    guarded /debug/adventures (it holds what the children said)."""
    return HTMLResponse((STATIC / "adventures.html").read_text())


@app.get("/debug/adventures", include_in_schema=False)
def debug_adventures():
    from aipet.games import adventure_viz

    return adventure_viz.collect(load_config())


def _day(d: str | None):
    if not d:
        return datetime.now(TZ).date()
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(400, "invalid day") from e


@app.get("/debug/parents/days", include_in_schema=False)
def parents_days():
    return parents.days_with_sessions()


@app.get("/debug/parents/day", include_in_schema=False)
async def parents_day(d: str | None = None):
    cfg = load_config()
    pet = character.of(cfg)
    digest = await asyncio.to_thread(parents.day_digest, _day(d), pet)
    energy = _energy(SESSION.get("conversation")).snapshot() if cfg.get("energy", {}).get("enabled", True) else None
    return {**digest, "pet": pet.name, "energy": energy, "asleep": _asleep_outside_session(cfg)}


@app.get("/debug/parents/thread/{name}", include_in_schema=False)
def parents_thread(name: str):
    events = session_log.read_session(_session_file(name))
    return {"name": name, "items": parents.thread(events, character.of(load_config()))}


@app.get("/debug/parents/review", include_in_schema=False)
def parents_review():
    return parents.review(_memory().root, character.of(load_config()))


class SeenBody(BaseModel):
    ids: list[str] = []


class EditBody(BaseModel):
    path: str
    change: dict = {}


@app.post("/debug/parents/seen", include_in_schema=False)
def parents_seen(body: SeenBody):
    parents.mark_seen(body.ids[:500])
    return {"ok": True}


@app.get("/debug/memory/structured", include_in_schema=False)
def memory_structured(path: str):
    try:
        return parents.structured(_memory().root, path)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/debug/memory/edit", include_in_schema=False)
async def memory_edit(body: EditBody):
    try:
        res = await asyncio.to_thread(parents.edit, _memory(), body.path, body.change)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    gone = res.pop("gone", None)
    conversation = SESSION.get("conversation")
    if conversation:  # the running pet sees the correction right away
        if gone:
            req = ForgetRequest.parse(gone)
            if req.words:
                conversation.scrub_context(req.contains_all)
        await conversation.refresh_prompt()
    return res


@app.get("/debug/tech/latency", include_in_schema=False)
def tech_latency(days: int = 7):
    return monitoring.latency_days(min(days, 60))


@app.get("/debug/tech/latency/{name}", include_in_schema=False)
def tech_latency_session(name: str):
    return monitoring.latency(session_log.read_session(_session_file(name)))


@app.get("/debug/tech/costs", include_in_schema=False)
async def tech_costs(days: int = 30):
    cfg = load_config()
    return await asyncio.to_thread(monitoring.costs, min(days, 90), cfg.get("costs", {}))


@app.get("/debug/tech/errors", include_in_schema=False)
def tech_errors(days: int = 14):
    return monitoring.errors(min(days, 60))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


class WebSession(Session):
    """A browser connection. Hanging up: the page closes the call (and shows the pet asleep or saying
    goodbye); if it does not within a few seconds, the server ends the session."""

    mode = "web"

    async def hang_up(self, reason: str):
        await self.worker.queue_frames([RTVIServerMessageFrame(data={"type": "hang_up", "reason": reason})])

        async def fallback():
            await asyncio.sleep(HANG_UP_FALLBACK_SECS)
            await self.close(f"hung up ({reason})")

        self.spawn(fallback(), "hang-up fallback")

    async def close(self, reason: str, condensation: str = "end of conversation"):
        if self._closed:
            return
        if SESSION.get("session") is self:  # a newer tab keeps its own debug state
            SESSION.clear()
        await super().close(reason, condensation)
        await self.worker.cancel()


async def bot(runner_args: RunnerArguments):
    if not isinstance(runner_args, SmallWebRTCRunnerArguments):
        raise RuntimeError("This bot only supports the browser WebRTC transport.")

    transport = SmallWebRTCTransport(
        webrtc_connection=runner_args.webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    session = WebSession(transport, mute_while_speaking=False)
    if SESSION:
        logger.warning("A second browser session started: the debug panel now follows the new one")
    SESSION.clear()
    SESSION.update(session=session, context=session.context, conversation=session.conversation, started=time.time())

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Browser connected")
        await session.open()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Browser disconnected")
        await session.close("browser disconnected")

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(session.worker)
    await runner.run()


def run():
    """Entry point of the `pet-web` command (Pipecat's development runner, port 7860)."""
    # Pipecat's runner looks for a `bot()` function in the __main__ module.
    sys.modules["__main__"].bot = bot
    migrate.migrate_legacy()  # data from before character packs (~/.flocon), once
    logs = load_config().get("logs", {})
    session_log.setup_file_logging(logs.get("text_keep_days", 14))
    session_log.purge(logs.get("keep_days", 30))
    main()


if __name__ == "__main__":
    run()
