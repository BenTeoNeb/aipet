"""Session log: the observer writes the right events, without duplicates."""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from aipet import session_log as sl


@pytest.fixture
def slog(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SESSIONS", tmp_path)
    log = sl.SessionLog("test")
    log.start(config={"x": 1})
    return log


def push(obs, frame, source=None, first=True, direction=FrameDirection.DOWNSTREAM):
    src = source or FrameProcessor()
    asyncio.run(
        obs.on_push_frame(
            FramePushed(
                source=src,
                destination=FrameProcessor(),
                frame=frame,
                direction=direction,
                timestamp=0,
                first_push=first,
            )
        )
    )


def events(log):
    return sl.read_session(log.path)


def test_full_turn_and_latency(slog):
    obs = sl.SessionObserver(slog)
    push(obs, TranscriptionFrame(text="coucou", user_id="", timestamp=""))
    push(obs, UserStoppedSpeakingFrame())
    push(obs, BotStartedSpeakingFrame())
    ev = events(slog)
    assert [e["type"] for e in ev] == [
        "session_start",
        "transcription",
        "user_stopped_speaking",
        "bot_started_speaking",
    ]
    assert ev[-1]["turn"] == 1 and ev[-1]["voice_latency_ms"] is not None


def test_no_duplicates(slog):
    obs = sl.SessionObserver(slog)
    f = FunctionCallInProgressFrame(function_name="search_memory", tool_call_id="1", arguments={"query": "x"})
    push(obs, f)
    push(obs, f, first=False)  # re-pushed by the next processor
    push(
        obs,
        FunctionCallInProgressFrame(function_name="search_memory", tool_call_id="1", arguments={"query": "x"}),
        direction=FrameDirection.UPSTREAM,
    )
    assert sum(e["type"] == "tool_call" for e in events(slog)) == 1


def test_raw_reply_only_from_the_llm(slog, monkeypatch):
    class FakeLLM(FrameProcessor):
        pass

    monkeypatch.setattr(sl, "LLMService", FakeLLM)
    obs, llm = sl.SessionObserver(slog), FakeLLM()
    push(obs, LLMFullResponseStartFrame(), source=llm)
    push(obs, LLMTextFrame("[joie] Coucou"), source=llm)
    push(obs, LLMTextFrame("Coucou"))  # rewritten by the emotion filter: ignored
    push(obs, LLMFullResponseEndFrame(), source=llm)
    rep = [e for e in events(slog) if e["type"] == "llm_response"][0]
    assert rep["raw_text"] == "[joie] Coucou"


def test_summary(slog):
    slog.event("bot_started_speaking", turn=1, voice_latency_ms=1000)
    slog.event("bot_started_speaking", turn=2, voice_latency_ms=3000)
    slog.event("error", message="network")
    slog.event("speaker", name="Léa")
    slog.event("wait")
    slog.event("wait_useful")
    slog.end("done")
    s = sl.summarize(sl.read_session(sorted(sl.SESSIONS.glob("*.jsonl"))[-1]))
    assert s["turns"] == 2 and s["latency_avg_ms"] == 2000 and s["latency_max_ms"] == 3000
    assert s["errors"] == 1 and s["speakers"] == ["Léa"] and not s["ongoing"]
    assert s["waits"] == 1 and s["waits_useful"] == 1 and s["waits_expired"] == 0


def test_nothing_written_without_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SESSIONS", tmp_path)
    log = sl.SessionLog("test")
    log.event("transcription", text="x")
    assert not list(tmp_path.glob("*"))


def test_speech_start_broadcast_both_ways_counts_once(slog):
    obs = sl.SessionObserver(slog)
    push(obs, BotStartedSpeakingFrame(), direction=FrameDirection.UPSTREAM)
    push(obs, BotStartedSpeakingFrame(), direction=FrameDirection.DOWNSTREAM)  # another frame, same instant
    assert sum(e["type"] == "bot_started_speaking" for e in events(slog)) == 1
