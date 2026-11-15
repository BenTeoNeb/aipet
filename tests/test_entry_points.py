"""Smoke tests of the command-line entry points: each starts and stops without a microphone, a
speaker or the network (fake transport, fake runner, fake keys)."""

import asyncio
import json
import sys

from pipecat.processors.frame_processor import FrameProcessor

from aipet import analyze, headless, session_log


class FakeTransport:
    def __init__(self, params):
        pass

    def input(self):
        return FrameProcessor()

    def output(self):
        return FrameProcessor()


class FakeRunner:
    async def add_workers(self, *workers):
        self.workers = workers

    async def run(self):  # the pipeline would run here until Ctrl+C
        return


def test_headless_starts_and_shuts_down(tmp_path, monkeypatch):
    monkeypatch.setenv("PET_HOME", str(tmp_path))
    monkeypatch.setattr(session_log, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(headless, "LocalAudioTransport", FakeTransport)
    monkeypatch.setattr(headless, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(session_log, "setup_file_logging", lambda days: None)

    for use_wake in (False, True):
        asyncio.run(headless.main(use_wake, None, None))
    ends = [
        json.loads(line)["type"] for f in (tmp_path / "sessions").glob("*.jsonl") for line in f.read_text().splitlines()
    ]
    assert "session_end" in ends  # the always-listening session was opened, then closed


def test_analyze_prints_a_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(session_log, "SESSIONS", tmp_path / "sessions")
    log = session_log.SessionLog("test")
    log.start()
    log.event("user_turn", text="bonjour", speaker="Mila")
    log.end("test")

    monkeypatch.setattr(sys, "argv", ["pet-analyze", "--days", "1"])
    analyze.main()
    monkeypatch.setattr(sys, "argv", ["pet-analyze", "--session", "last"])
    analyze.main()
    out = capsys.readouterr().out
    assert "user_turn" in out
