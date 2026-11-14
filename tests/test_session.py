"""The conversation lifecycle shared by the web page and the Pi (no network: fake keys, no model call)."""

import asyncio
import json

from pipecat.processors.frame_processor import FrameProcessor

from aipet import session_log
from aipet.session import Session


class Transport:
    def input(self):
        return FrameProcessor()

    def output(self):
        return FrameProcessor()


class FakeSession(Session):
    mode = "test"

    def __init__(self):
        super().__init__(Transport(), mute_while_speaking=False)
        self.hung = []

    async def hang_up(self, reason):
        self.hung.append(reason)


def events(path):
    return [json.loads(line)["type"] for line in path.read_text().splitlines()]


def test_open_pause_and_close(tmp_path, monkeypatch):
    monkeypatch.setenv("PET_HOME", str(tmp_path))
    monkeypatch.setattr(session_log, "SESSIONS", tmp_path / "sessions")

    async def go():
        s = FakeSession()
        queued = []

        async def queue(frames):
            queued.extend(frames)

        s.worker.queue_frames = queue
        await s.open()
        assert s.log.active and s.context.get_messages()[-1]["role"] == "developer"  # the greeting
        first = s.log.path
        s.pause()  # Pi: back to waiting for the wake word
        await asyncio.sleep(0.05)
        assert not s.log.active and events(first)[-2:] == ["sleep", "session_end"]

        await s.open(greet=False, wake_score=0.9)
        second = s.log.path  # (same file when both start within a second)
        await s.close("shutdown")
        await s.close("shutdown")  # once only
        assert events(second).count("session_end") == (2 if second == first else 1)
        assert s.keeper.worker is None  # no prompt refresh after the end

    asyncio.run(go())
