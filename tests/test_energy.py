"""Energy (naps) and the sleep gate, with a fake clock."""

import asyncio

from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame, UserStartedSpeakingFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from aipet.energy import NAP_LINES, NIGHT_LINES, Energy, SleepGate


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def talk(e, clock, minutes, every=30):
    for _ in range(int(minutes * 60 / every)):
        clock.t += every
        e.activity()


def test_drains_while_talking_and_naps_at_zero():
    c = Clock()
    e = Energy(talk_minutes=20, recharge_minutes=30, clock=c)
    e.activity()
    talk(e, c, 10)
    assert abs(e.level() - 0.5) < 0.02 and e.state() == "normal"
    talk(e, c, 3)
    assert e.state() == "tired"
    talk(e, c, 5)
    assert e.state() == "very_tired"
    talk(e, c, 3)
    assert e.asleep and e.state() == "asleep"


def test_recharges_at_rest_and_wakes_up():
    c = Clock()
    e = Energy(talk_minutes=20, recharge_minutes=30, wake_at=0.5, clock=c)
    e.activity()
    talk(e, c, 21)
    assert e.asleep
    c.t += 90 + 10 * 60  # 10 min of rest after the active gap: a third of a charge
    assert e.asleep
    c.t += 6 * 60
    assert not e.asleep and e.level() >= 0.5


def test_talking_to_a_sleeping_pet_does_not_drain_or_reset():
    c = Clock()
    e = Energy(talk_minutes=1, recharge_minutes=10, clock=c)
    e.activity()
    talk(e, c, 2, every=10)
    assert e.asleep
    level = e.level()
    for _ in range(5):
        c.t += 10
        e.activity()
    assert e.level() >= level and e.asleep


def test_never_naps_in_a_sad_moment():
    c = Clock()
    e = Energy(talk_minutes=1, clock=c)
    e.activity()
    e.protect()
    talk(e, c, 2, every=10)
    assert not e.asleep and e.state() == "very_tired"


def test_recharge_button_and_persistence(tmp_path):
    c = Clock()
    f = tmp_path / "energy.json"
    e = Energy(talk_minutes=1, clock=c, state_file=f)
    e.activity()
    talk(e, c, 2, every=10)
    assert e.asleep and Energy(talk_minutes=1, clock=c, state_file=f).asleep  # survives a restart
    e.recharge()
    assert not e.asleep and e.level() == 1.0
    assert Energy(clock=c, state_file=f).level() == 1.0


def run_gate(reason):
    out = []
    gate = SleepGate(lambda: reason, energy=None)

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        out.append(frame)

    gate.push_frame = push

    async def go():
        await SleepGate.process_frame(gate, UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await SleepGate.process_frame(gate, LLMContextFrame(LLMContext()), FrameDirection.DOWNSTREAM)

    asyncio.run(go())
    return out


def test_sleep_gate():
    awake = run_gate(None)
    assert any(isinstance(f, LLMContextFrame) for f in awake)
    nap = run_gate("nap")
    assert not any(isinstance(f, LLMContextFrame) for f in nap)  # no model call while asleep
    assert [f.text for f in nap if isinstance(f, TTSSpeakFrame)][0] in NAP_LINES
    night = run_gate("night")
    assert [f.text for f in night if isinstance(f, TTSSpeakFrame)][0] in NIGHT_LINES
