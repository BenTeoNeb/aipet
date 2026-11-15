"""Wake-word timers (openWakeWord model faked)."""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    ErrorFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from aipet import wake


class FakeOWW:
    def predict(self, chunk):
        return {"x": 0.0}

    def reset(self):
        pass


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(wake, "load_wake_model", lambda name: FakeOWW())
    pushed = []

    def make(**kw):
        g = wake.WakeWordGate(model="x", threshold=0.5, **kw)

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        g.push_frame = push
        g.pushed = pushed
        return g

    return make


def test_wake_arms_timers_even_if_on_wake_crashes(gate):
    async def boom():
        raise RuntimeError("x")

    async def run():
        g = gate(idle_timeout=0.05, max_awake=5, on_wake=boom)
        await g._wake(0.9)
        assert g._awake and g._idle_task and g._watchdog
        assert any(isinstance(f, OutputAudioRawFrame) for f in g.pushed)  # the short chime
        await asyncio.sleep(0.15)
        assert not g._awake  # back to sleep despite the error

    asyncio.run(run())


def test_watchdog_restarts_when_the_pet_speaks(gate):
    async def run():
        g = gate(idle_timeout=10, max_awake=0.2)
        await g._wake(0.9)
        for _ in range(4):  # a long story: Flocon speaks in chunks
            await asyncio.sleep(0.1)
            g._cancel_idle()  # what BotStartedSpeaking does
            g._arm_watchdog()
        assert g._awake
        await asyncio.sleep(0.3)
        assert not g._awake

    asyncio.run(run())


def test_no_sleep_while_a_reply_is_being_prepared(gate):
    async def run():
        g = gate(idle_timeout=0.05, max_awake=5)
        await g._wake(0.9)
        g._pending = True
        await asyncio.sleep(0.2)
        assert g._awake  # waiting for the reply
        g._pending = False
        await asyncio.sleep(1.2)
        assert not g._awake

    asyncio.run(run())


# ---- through real frames (process_frame) ----------------------------------


@pytest.fixture
def framed(gate, monkeypatch):
    async def noop(self, frame, direction):
        return None

    monkeypatch.setattr(FrameProcessor, "process_frame", noop)  # no full processor initialization
    return gate


def test_long_uninterrupted_story_not_cut(framed):
    async def run():
        g = framed(idle_timeout=10, max_awake=0.3)
        await g._wake(0.9)
        await g.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        for _ in range(10):  # 1 s of continuous speech, far more than max_awake
            g._last_watchdog_arm = 0  # (re-arming is limited to once per second in reality)
            await g.process_frame(BotSpeakingFrame(), FrameDirection.UPSTREAM)
            await asyncio.sleep(0.1)
        assert g._awake
        await asyncio.sleep(0.4)
        assert not g._awake

    asyncio.run(run())


def test_audio_blocked_asleep_and_passes_awake(framed):
    async def run():
        g = framed(idle_timeout=10, max_awake=10)
        audio = InputAudioRawFrame(audio=b"\0\0" * 320, sample_rate=16000, num_channels=1)
        await g.process_frame(audio, FrameDirection.DOWNSTREAM)
        assert audio not in g.pushed
        await g._wake(0.9)
        await g.process_frame(audio, FrameDirection.DOWNSTREAM)
        assert audio in g.pushed
        g.sleep()

    asyncio.run(run())


def test_pending_reply_then_rate_limited_error_sound(framed):
    async def run():
        g = framed(idle_timeout=10, max_awake=10)
        await g._wake(0.9)
        await g.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert g._pending
        n0 = sum(isinstance(f, OutputAudioRawFrame) for f in g.pushed)
        for _ in range(3):
            await g.process_frame(ErrorFrame(error="network"), FrameDirection.UPSTREAM)
        assert not g._pending
        assert sum(isinstance(f, OutputAudioRawFrame) for f in g.pushed) == n0 + 1  # a single sound within 5 s
        g.sleep()

    asyncio.run(run())


def test_no_sleep_while_a_tool_runs(gate, monkeypatch):
    from pipecat.frames.frames import FunctionCallInProgressFrame, FunctionCallResultFrame

    monkeypatch.setattr(wake, "TOOL_WAIT_SECS", 60)

    async def run():
        g = gate(idle_timeout=0.05, max_awake=5)
        await g._wake(0.9)
        call = FunctionCallInProgressFrame(function_name="story_start", tool_call_id="t1", arguments={})
        await g.process_frame(call, FrameDirection.DOWNSTREAM)
        await g.process_frame(call, FrameDirection.UPSTREAM)  # broadcast both ways
        g._arm_idle()  # e.g. the waiting phrase ended
        await asyncio.sleep(0.3)
        assert g._awake  # a story plan is being written: stay awake
        result = FunctionCallResultFrame(function_name="story_start", tool_call_id="t1", arguments={}, result={})
        await g.process_frame(result, FrameDirection.DOWNSTREAM)
        assert g._pending and not g._tools  # the model now writes the reply
        g._pending = False
        await asyncio.sleep(0.2)
        assert not g._awake

    asyncio.run(run())


def test_a_cancelled_tool_call_lets_the_pet_sleep(gate, monkeypatch):
    from pipecat.frames.frames import FunctionCallCancelFrame, FunctionCallInProgressFrame

    monkeypatch.setattr(wake, "TOOL_WAIT_SECS", 60)

    async def run():
        g = gate(idle_timeout=0.05, max_awake=5)
        await g._wake(0.9)
        call = FunctionCallInProgressFrame(function_name="story_start", tool_call_id="t1", arguments={})
        await g.process_frame(call, FrameDirection.DOWNSTREAM)
        # Interrupted: Pipecat sends a cancel instead of a result.
        await g.process_frame(
            FunctionCallCancelFrame(function_name="story_start", tool_call_id="t1"), FrameDirection.DOWNSTREAM
        )
        assert not g._tools
        await asyncio.sleep(0.2)
        assert not g._awake  # the mic does not stay open for the tool's 60 s

    asyncio.run(run())
