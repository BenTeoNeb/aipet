"""Energy (naps) and the sleep gate, with a fake clock."""

import asyncio

from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame, UserStartedSpeakingFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from aipet.conversation import Conversation
from aipet.energy import NAP_LINES, NIGHT_LINES, Energy, SleepGate
from aipet.memory import Memory


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


def test_the_sleeping_pet_hangs_up():
    """Seen in a real session: after « bonne nuit » the pet slept but the call stayed open, and the
    child kept talking to a « zzz »."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame

    from aipet.hangup import HangUp, SleepyReplyFrame

    state = {"reason": None}
    hung = []

    async def hang_up(reason):
        hung.append(reason)

    h = HangUp(lambda: state["reason"], hang_up, after=0.05)

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pass

    h.push_frame = push
    send = lambda f: HangUp.process_frame(h, f, FrameDirection.DOWNSTREAM)  # noqa: E731

    async def go():
        await send(BotStoppedSpeakingFrame())  # awake: the conversation goes on
        await asyncio.sleep(0.1)
        assert hung == []
        state["reason"] = "night"  # « bonne nuit » said
        await send(BotStoppedSpeakingFrame())
        await send(UserStartedSpeakingFrame())  # the child answers « bonne nuit » in time…
        await asyncio.sleep(0.1)
        assert hung == []
        h._after = 5  # …and gets a « zzz »: the pet hangs up at once, not after the usual silence
        await send(SleepyReplyFrame("zzz", append_to_context=False))
        await send(BotStartedSpeakingFrame())
        await send(BotStoppedSpeakingFrame())
        await asyncio.sleep(1.1)
        assert hung == ["night"]
        h._after = 0.05
        state["reason"] = "nap"
        await send(BotStoppedSpeakingFrame())
        state["reason"] = None  # a parent recharged it meanwhile: no hang-up
        await asyncio.sleep(0.1)
        assert hung == ["night"]
        # A sleepy reply, then a recharge and a normal talk: a later goodbye waits the usual time.
        await send(SleepyReplyFrame("zzz", append_to_context=False))
        await send(BotStoppedSpeakingFrame())  # awake again (reason None)
        h._after = 5
        state["reason"] = "goodbye"
        await send(BotStoppedSpeakingFrame())
        await asyncio.sleep(1.1)
        assert hung == ["night"]  # not hung up after 1 s
        h._cancel()

    asyncio.run(go())


def test_the_sleepy_reply_is_marked_for_the_hang_up():
    assert all(type(f).__name__ == "SleepyReplyFrame" for f in run_gate("nap") if isinstance(f, TTSSpeakFrame))


def test_goodbye_hangs_up_unless_the_child_goes_on(tmp_path):
    k = Conversation(Memory(tmp_path, "Flocon"), {"memory": {}}, LLMContext(), "BASE")
    assert k.sleep.hang_up_reason() is None
    asyncio.run(k.on_tag("fin"))  # « [joie] [fin] À bientôt ! »
    assert k.sleep.hang_up_reason() == "goodbye"
    k.context.add_message({"role": "user", "content": "Attends, encore une question !"})
    k.tag_last_user_message()
    assert k.sleep.hang_up_reason() is None


def test_reading_the_state_changes_nothing():
    events = []

    class Log:
        def event(self, type_, **kw):
            events.append(type_)

    c = Clock()
    e = Energy(talk_minutes=1, recharge_minutes=10, clock=c, slog=Log())
    e.activity()
    talk(e, c, 2, every=10)
    c.t += 3600  # rested
    for _ in range(3):  # the parents' page polling
        assert not e.asleep and e.snapshot()["state"] != "asleep"
    assert "woke_up" not in events
    e.activity()  # the next child message really wakes it up
    assert events.count("woke_up") == 1


def test_a_long_story_drains_instead_of_recharging():
    c = Clock()
    e = Energy(talk_minutes=20, recharge_minutes=30, clock=c)
    e.activity()
    before = e.level()
    e.activity()  # the pet starts a story…
    c.t += 240
    e.activity(continuous=True)  # …and stops 4 minutes later
    assert e.level() < before - 0.15  # 4 of 20 minutes spent


def tired_pet(minutes):
    c = Clock()
    e = Energy(talk_minutes=20, recharge_minutes=30, wake_at=0.5, clock=c)
    e.activity()
    talk(e, c, minutes)
    return e, c


def goodbye(tmp_path, e):
    k = Conversation(Memory(tmp_path, "Flocon"), {"memory": {}}, LLMContext(), "BASE", energy=e)
    asyncio.run(k.on_tag("fin"))
    assert k.sleep.hang_up_reason() == "goodbye"
    k.context.add_message({"role": "user", "content": "D'accord ?"})
    k.tag_last_user_message()
    return k


def test_a_very_tired_pet_saying_goodbye_really_naps(tmp_path):
    """Seen in a real session: the pet announced its nap but answered the child's « d'accord ? »
    as if nothing happened."""
    e, c = tired_pet(17.5)
    assert e.state() == "very_tired"
    k = goodbye(tmp_path, e)
    assert k.sleep.hang_up_reason() == "nap"  # a sleepy reply, then it hangs up
    c.t += 5 * 60
    assert e.asleep  # a real nap, not a few seconds
    c.t += 15 * 60
    assert not e.asleep


def test_a_merely_tired_pet_saying_goodbye_can_be_called_back(tmp_path):
    e, _ = tired_pet(13)
    assert e.state() == "tired"
    k = goodbye(tmp_path, e)
    assert k.sleep.hang_up_reason() is None and not e.asleep  # the child goes on


def test_no_nap_after_goodbye_in_a_sad_moment(tmp_path):
    e, _ = tired_pet(17.5)
    e.protect()
    assert goodbye(tmp_path, e).sleep.hang_up_reason() is None and not e.asleep


def test_no_nap_after_goodbye_that_would_end_at_once():
    c = Clock()
    e = Energy(talk_minutes=20, recharge_minutes=30, wake_at=0.1, clock=c)
    e.activity()
    talk(e, c, 17.5)
    assert e.state() == "very_tired" and not e.nap_after_goodbye() and not e._asleep
