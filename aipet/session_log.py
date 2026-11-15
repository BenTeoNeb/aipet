"""Detailed session logs, for analysis and debugging.

- `~/.aipet/pets/<pet>/logs/sessions/YYYY-MM-DD_HHMMSS_<mode>.jsonl`: one JSON line per event of a session
  (web: one browser connection; headless: one wake-up until sleep).
- `~/.aipet/pets/<pet>/logs/aipet.log`: the full text log (loguru), rotated.

A session contains: a config snapshot, wake/sleep, start/end of speech (child and the pet), final
transcriptions, the raw model reply (emotion tags included), tool calls and results, emotions,
speakers, condensations, errors, metrics (TTFB per service, tokens, TTS characters) and, per
turn, the latency "end of child speech → the pet's voice".

These files contain the children's speech: they stay local (never in git) and are deleted after
`[logs].keep_days` days.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    ProcessingMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.llm_service import LLMService

from aipet import paths
from aipet.fileio import lock_for
from aipet.timefmt import TZ

LOG_DIR = paths.logs_dir()  # this pet's logs
SESSIONS = LOG_DIR / "sessions"


def _short(v, n: int = 800):
    """JSON-serializable value that is not too long (tool results…)."""
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        s = repr(v)
    return json.loads(s) if len(s) <= n else s[:n] + "…"


def setup_file_logging(keep_days: int = 14):
    """Persistent text log (in addition to the console)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOG_DIR / "aipet.log",
        rotation="10 MB",
        retention=f"{keep_days} days",
        level="DEBUG",
        enqueue=True,
        encoding="utf-8",
    )


def scrub(words: list[str]) -> int:
    """Forget for real: drop log lines (session JSONL and the text log) containing all `words`
    (accent-insensitive). Returns the number of lines removed."""
    import re
    import unicodedata

    def norm(t: str) -> str:
        t = unicodedata.normalize("NFKD", t.lower())
        return "".join(c for c in t if not unicodedata.combining(c))

    patterns = [re.compile(r"\b" + re.escape(norm(w))) for w in words if w]
    if not patterns:
        return 0
    removed = 0
    for p in [*SESSIONS.glob("*.jsonl"), *LOG_DIR.glob("*.log*")]:  # *.log*: also logs from before the rename
        # Session files: locked against `SessionLog.event` appends. The text log is rewritten in place
        # (loguru keeps it open: replacing the file would lose the next lines).
        with lock_for(p):
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            kept = [line for line in lines if not all(pt.search(norm(line)) for pt in patterns)]
            if len(kept) != len(lines):
                p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                removed += len(lines) - len(kept)
    return removed


def purge(keep_days: int = 30):
    limit = time.time() - keep_days * 86400
    for p in SESSIONS.glob("*.jsonl"):
        if p.stat().st_mtime < limit:
            p.unlink(missing_ok=True)


class NullLog:
    """Stands in for a session log where there is none (tests, tools): every call does nothing, so
    callers never check for one."""

    path = None
    active = False

    def start(self, **info):
        pass

    def end(self, reason: str = ""):
        pass

    def event(self, type: str, **data):
        pass


NO_LOG = NullLog()


class SessionLog:
    """One JSONL file per session. `event()` can be called from anywhere (memory, wake…)."""

    def __init__(self, mode: str):
        self.mode = mode
        self.path: Path | None = None
        self.t0 = 0.0

    @property
    def active(self) -> bool:
        return self.path is not None

    def start(self, **info):
        if self.path:
            self.end("new session")
        SESSIONS.mkdir(parents=True, exist_ok=True)
        now = datetime.now(TZ)
        self.path = SESSIONS / f"{now:%Y-%m-%d_%H%M%S}_{self.mode}.jsonl"
        self.t0 = time.monotonic()
        self.event("session_start", mode=self.mode, **info)
        logger.info(f"📒 Session log: {self.path}")

    def end(self, reason: str = ""):
        if self.path:
            self.event("session_end", reason=reason, duration_s=round(time.monotonic() - self.t0, 1))
            self.path = None

    def event(self, type: str, **data):
        if not self.path:
            return
        line = {
            "t": datetime.now(TZ).isoformat(timespec="milliseconds"),
            "s": round(time.monotonic() - self.t0, 3),
            "type": type,
            **data,
        }
        try:
            with lock_for(self.path), open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # a full disk must not cut the voice
            logger.warning(f"Cannot write session log: {e}")


class SessionObserver(BaseObserver):
    """Watches frames flow through the pipeline (without slowing it down) and logs them."""

    def __init__(self, log: SessionLog, **kwargs):
        super().__init__(**kwargs)
        self.log = log
        self._user_stop: float | None = None
        self._llm_start: float | None = None
        self._llm_first: float | None = None
        self._llm_text: list[str] = []
        self._turn = 0
        self._calls_seen: set[tuple[str, str]] = set()  # a tool call travels upstream AND downstream
        self._last_seen: dict[str, float] = {}  # frames broadcast both ways: log only once
        self._last_transcript: float | None = None

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:  # every processor re-pushes the frame: once is enough
            return
        f, ev, now = data.frame, self.log.event, time.monotonic()

        # Speech start/stop and interruptions are broadcast upstream AND downstream
        # (two distinct frames): keep only the first of the two.
        if isinstance(
            f,
            (
                UserStartedSpeakingFrame,
                UserStoppedSpeakingFrame,
                BotStartedSpeakingFrame,
                BotStoppedSpeakingFrame,
                InterruptionFrame,
            ),
        ):
            k = type(f).__name__
            if now - self._last_seen.get(k, -1) < 0.25:
                return
            self._last_seen[k] = now

        if isinstance(f, UserStartedSpeakingFrame):
            ev("user_started_speaking")
        elif isinstance(f, UserStoppedSpeakingFrame):
            self._user_stop = now
            ev("user_stopped_speaking")
        elif isinstance(f, TranscriptionFrame):
            self._last_transcript = now
            ev("transcription", text=f.text)
        elif isinstance(f, LLMFullResponseStartFrame):
            self._llm_start, self._llm_first, self._llm_text = now, None, []
        elif isinstance(f, LLMTextFrame):
            if not isinstance(data.source, LLMService):
                return  # text rewritten further down (tags removed): keep the model's raw version
            if self._llm_first is None:
                self._llm_first = now
            self._llm_text.append(f.text)
        elif isinstance(f, LLMFullResponseEndFrame):
            text = "".join(self._llm_text)
            if self._llm_start is not None:
                ev(
                    "llm_response",
                    raw_text=text,
                    first_text_ms=round((self._llm_first - self._llm_start) * 1000)
                    if self._llm_first and self._llm_start
                    else None,
                    duration_ms=round((now - self._llm_start) * 1000) if self._llm_start else None,
                )
            self._llm_start, self._llm_text = None, []
        elif isinstance(f, BotStartedSpeakingFrame):
            self._turn += 1
            lat = round((now - self._user_stop) * 1000) if self._user_stop else None
            # Perceived latency: from the last transcription (the child had finished speaking
            # before the end of turn was detected).
            lat_tr = round((now - self._last_transcript) * 1000) if self._user_stop and self._last_transcript else None
            ev("bot_started_speaking", turn=self._turn, voice_latency_ms=lat, perceived_latency_ms=lat_tr)
            self._user_stop = None
        elif isinstance(f, BotStoppedSpeakingFrame):
            ev("bot_stopped_speaking")
        elif isinstance(f, InterruptionFrame):
            ev("interruption")
        elif isinstance(f, (FunctionCallInProgressFrame, FunctionCallResultFrame)):
            key = (type(f).__name__, f.tool_call_id)
            if key in self._calls_seen:
                return
            self._calls_seen.add(key)
            if len(self._calls_seen) > 500:
                self._calls_seen.clear()
            if isinstance(f, FunctionCallInProgressFrame):
                ev("tool_call", name=f.function_name, arguments=_short(f.arguments))
            else:
                ev("tool_result", name=f.function_name, result=_short(f.result))
        elif isinstance(f, ErrorFrame):
            ev("error", message=str(f.error), fatal=getattr(f, "fatal", False))
        elif isinstance(f, MetricsFrame):
            for m in f.data:
                if isinstance(m, TTFBMetricsData) and m.value:
                    ev("metric", name="ttfb_ms", service=m.processor, value=round(m.value * 1000))
                elif isinstance(m, ProcessingMetricsData) and m.value:
                    ev("metric", name="processing_ms", service=m.processor, value=round(m.value * 1000))
                elif isinstance(m, LLMUsageMetricsData):
                    u = m.value
                    ev(
                        "metric",
                        name="tokens",
                        service=m.processor,
                        input=u.prompt_tokens,
                        output=u.completion_tokens,
                        cache=u.cache_read_input_tokens,
                        reasoning=u.reasoning_tokens,
                    )
                elif isinstance(m, TTSUsageMetricsData):
                    ev("metric", name="tts_characters", service=m.processor, value=m.value)
        elif isinstance(f, RTVIServerMessageFrame) and isinstance(f.data, dict) and f.data.get("type") == "emotion":
            ev("emotion", emotion=f.data.get("emotion"))  # when the face changes


# ---------------------------------------------------------------------------
# Reading (debug panel, analysis)
# ---------------------------------------------------------------------------
def read_session(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def summarize(events: list[dict]) -> dict:
    lat = [e["voice_latency_ms"] for e in events if e["type"] == "bot_started_speaking" and e.get("voice_latency_ms")]
    lat_p = [
        e["perceived_latency_ms"]
        for e in events
        if e["type"] == "bot_started_speaking" and e.get("perceived_latency_ms")
    ]
    tok = [e for e in events if e["type"] == "metric" and e.get("name") == "tokens"]
    start = next((e for e in events if e["type"] == "session_start"), {})
    end = next((e for e in reversed(events) if e["type"] == "session_end"), None)

    def count(t: str) -> int:
        return sum(1 for e in events if e["type"] == t)

    return {
        "start": start.get("t"),
        "mode": start.get("mode"),
        "duration_s": end.get("duration_s") if end else (events[-1]["s"] if events else 0),
        "ongoing": end is None,
        "turns": count("bot_started_speaking"),
        "transcripts": count("transcription"),
        "latency_avg_ms": round(sum(lat) / len(lat)) if lat else None,
        "latency_max_ms": max(lat) if lat else None,
        "perceived_latency_avg_ms": round(sum(lat_p) / len(lat_p)) if lat_p else None,
        "silences": count("silence"),
        "fillers": count("filler_spoken"),
        "short_replies_replayed": count("short_reply_replayed"),
        "waits": count("wait"),
        "waits_useful": count("wait_useful"),
        "waits_expired": count("wait_expired"),
        "tools": count("tool_call"),
        "errors": count("error"),
        "interruptions": count("interruption"),
        "emotions": count("emotion"),
        "tokens_in": sum(e.get("input") or 0 for e in tok),
        "tokens_out": sum(e.get("output") or 0 for e in tok),
        "speakers": sorted({e.get("name") for e in events if e["type"] == "speaker" and e.get("name")}),
        "names_to_confirm": sorted({e.get("name") for e in events if e["type"] == "speaker_to_confirm"}),
    }


def list_sessions(limit: int = 50) -> list[dict]:
    files = sorted(SESSIONS.glob("*.jsonl"), reverse=True)[:limit]
    return [{"name": p.name, "size": p.stat().st_size, **summarize(read_session(p))} for p in files]
