"""Short replies held while Flocon speaks, "Hmm" when the reply is slow, unfinished-sentence wait."""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection as D
from pipecat.processors.frame_processor import FrameProcessor

from aipet import turns


@pytest.fixture
def make(monkeypatch):
    async def noop(self, frame, direction):
        return None

    monkeypatch.setattr(FrameProcessor, "process_frame", noop)

    def build(cls, *a, **kw):
        p = cls(*a, **kw)
        p.out = []

        async def push(frame, direction=D.DOWNSTREAM):
            p.out.append((frame, direction))

        p.push_frame = push
        return p

    return build


def tr(text):
    return TranscriptionFrame(text=text, user_id="", timestamp="")


def test_short_reply_held_then_replayed(make):
    async def run():
        k = make(turns.ShortReplyKeeper, 3, delay=0.05)
        await k.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        await k.process_frame(tr("bébé robot"), D.DOWNSTREAM)
        assert not any(isinstance(f, TranscriptionFrame) for f, _ in k.out)  # does not interrupt Flocon
        await k.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        replayed = [f for f, _ in k.out if isinstance(f, LLMMessagesAppendFrame)]
        assert replayed and replayed[0].messages[0]["content"] == "bébé robot" and replayed[0].run_llm

    asyncio.run(run())


def test_long_sentence_goes_through_immediately(make):
    async def run():
        k = make(turns.ShortReplyKeeper, 3)
        await k.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        f = tr("non attends je veux un dragon")
        await k.process_frame(f, D.DOWNSTREAM)
        assert (f, D.DOWNSTREAM) in k.out

    asyncio.run(run())


def test_short_reply_joins_the_turn_if_the_child_speaks_again(make):
    async def run():
        k = make(turns.ShortReplyKeeper, 3, delay=0.05)
        await k.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        f = tr("vert")
        await k.process_frame(f, D.DOWNSTREAM)
        await k.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await k.process_frame(UserStartedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        assert (f, D.DOWNSTREAM) in k.out
        assert not any(isinstance(x, LLMMessagesAppendFrame) for x, _ in k.out)

    asyncio.run(run())


def test_filler_when_reply_is_slow(make):
    async def run():
        t = make(turns.ThinkingFiller, after=0.05)
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.1)
        hmm = [f for f, _ in t.out if isinstance(f, TTSSpeakFrame)]
        assert len(hmm) == 1 and hmm[0].append_to_context is False

    asyncio.run(run())


def test_no_filler_when_reply_arrives(make):
    async def run():
        t = make(turns.ThinkingFiller, after=0.1)
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("[silence]"), D.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


def test_no_filler_after_the_sleeping_pets_zzz(make):
    """Seen in a real session: asleep, the pet answered « zzz » then « Attends, je réfléchis… »."""

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=0.1)
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await t.process_frame(TTSSpeakFrame("Zzz… Flocon fait dodo…", append_to_context=False), D.DOWNSTREAM)
        await asyncio.sleep(0.2)
        spoken = [f.text for f, _ in t.out if isinstance(f, TTSSpeakFrame)]
        assert spoken == ["Zzz… Flocon fait dodo…"]  # passed on, nothing added (no filler, no apology)

    asyncio.run(run())


def test_turn_abandoned_when_nothing_arrives(make):
    async def run():
        t = make(turns.ThinkingFiller, after=10, deadline=0.05)
        interrupted = []

        async def bi():
            interrupted.append(True)

        t.broadcast_interruption = bi
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.1)
        assert interrupted
        assert any(isinstance(f, TTSSpeakFrame) and f.text == turns.APOLOGY for f, _ in t.out)

    asyncio.run(run())


def test_no_abandon_when_text_arrives(make):
    async def run():
        t = make(turns.ThinkingFiller, after=10, deadline=0.1)
        t.broadcast_interruption = None  # must not be called
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Oh !"), D.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


# --- UnfinishedSentenceWait ([attends]) ----------------------------------------------------------
class FakeLog:
    def __init__(self):
        self.events = []

    def event(self, type_, **kw):
        self.events.append(type_)


def make_wait(**kw):
    w = turns.UnfinishedSentenceWait(slog=FakeLog(), **kw)
    w.queued = []

    async def queue(frames):
        w.queued.extend(frames)

    w.queue = queue
    return w


def answer_requests(w):
    return [
        f
        for f in w.queued
        if isinstance(f, LLMMessagesAppendFrame)
        and f.messages[0]["content"] == turns.UnfinishedSentenceWait.NOTE
        and f.run_llm
    ]


def test_wait_disabled_answers_immediately():
    async def run():
        w = make_wait(delay=10, enabled=False)
        await w.on_wait_tag()
        assert len(answer_requests(w)) == 1 and not w.waiting
        assert w.slog.events == ["wait_ignored"]

    asyncio.run(run())


def test_wait_then_expiry_requests_an_answer():
    async def run():
        w = make_wait(delay=0.05)
        await w.on_wait_tag()
        assert w.waiting and not w.queued  # nothing said, nothing asked yet
        await asyncio.sleep(0.1)
        assert len(answer_requests(w)) == 1 and not w.waiting
        assert w.slog.events == ["wait", "wait_expired"]

    asyncio.run(run())


def test_child_speaking_cancels_the_wait():
    async def run():
        w = make_wait(delay=0.05)
        await w.on_wait_tag()
        w.child_speaks()
        await asyncio.sleep(0.1)
        assert not w.queued and w.slog.events == ["wait", "wait_useful"]
        w.child_speaks()  # no wait in progress: nothing logged
        assert w.slog.events == ["wait", "wait_useful"]

    asyncio.run(run())


def test_too_many_waits_in_a_row_answers_immediately():
    async def run():
        w = make_wait(delay=10, max_in_a_row=2)
        await w.on_wait_tag()
        w.child_speaks()
        await w.on_wait_tag()
        w.child_speaks()
        assert not w.queued
        await w.on_wait_tag()  # third in a row
        assert len(answer_requests(w)) == 1
        assert w.slog.events[-1] == "wait_ignored"

    asyncio.run(run())


def test_wait_tag_after_the_nudge_speaks_the_nudge():
    async def run():
        w = make_wait(delay=0.05)
        await w.on_wait_tag()
        await asyncio.sleep(0.1)  # expired: "answer now" requested
        await w.on_wait_tag()  # the model still wants to wait
        spoken = [f for f in w.queued if isinstance(f, TTSSpeakFrame)]
        assert len(spoken) == 1 and spoken[0].text == turns.UnfinishedSentenceWait.NUDGE
        assert spoken[0].append_to_context is False and w.slog.events[-1] == "wait_nudge"
        await w.on_wait_tag()  # counters were reset: a normal wait again
        assert w.waiting
        w.cancel()

    asyncio.run(run())


def test_replied_resets_the_counters():
    async def run():
        w = make_wait(delay=10, max_in_a_row=1)
        await w.on_wait_tag()
        w.replied()
        assert not w.waiting
        await w.on_wait_tag()  # would be ignored without the reset
        assert w.waiting and not w.queued
        w.cancel()

    asyncio.run(run())


# --- ThinkingFiller during tool calls --------------------------------------------------------
def test_filler_and_deadline_rearmed_during_a_tool_call(make, monkeypatch):
    from pipecat.frames.frames import FunctionCallInProgressFrame

    monkeypatch.setattr(turns, "TOOL_SECOND_FILLER_SECS", 0.05)

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=0.08, tool_deadline=0.3)
        interrupted = []

        async def bi():
            interrupted.append(True)

        t.broadcast_interruption = bi
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Laisse-moi imaginer…"), D.DOWNSTREAM)  # the waiting phrase
        await t.process_frame(
            FunctionCallInProgressFrame(function_name="story_start", tool_call_id="1", arguments={}), D.DOWNSTREAM
        )
        await t.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)  # our own "Hmm": must not cancel
        await asyncio.sleep(0.15)
        spoken = [f.text for f, _ in t.out if isinstance(f, TTSSpeakFrame)]
        assert spoken[0] in turns.FILLERS and spoken[1] == turns.STILL_WORKING
        assert not interrupted  # the normal 0.08 s deadline does not apply to a tool
        await asyncio.sleep(0.25)
        assert interrupted  # but the tool deadline does

    asyncio.run(run())


def test_silence_nudge_once_during_an_activity(make):
    async def run():
        active = {"on": True}
        n = make(turns.SilenceNudge, lambda: active["on"], after=0.05)
        await n.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        nudges = [f for f, _ in n.out if isinstance(f, TTSSpeakFrame)]
        assert len(nudges) == 1 and not nudges[0].append_to_context
        await n.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)  # the nudge is spoken…
        await n.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        assert len([f for f, _ in n.out if isinstance(f, TTSSpeakFrame)]) == 1  # …then quiet
        await n.process_frame(tr("non"), D.DOWNSTREAM)  # the child answers: a new cycle
        await n.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await n.process_frame(UserStartedSpeakingFrame(), D.UPSTREAM)  # heard in time
        await asyncio.sleep(0.1)
        assert len([f for f, _ in n.out if isinstance(f, TTSSpeakFrame)]) == 1
        active["on"] = False  # no activity: no nudge
        await n.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        assert len([f for f, _ in n.out if isinstance(f, TTSSpeakFrame)]) == 1

    asyncio.run(run())


# ---- the child's turn starts on voice while the pet is silent -------------------------------
def test_turn_starts_on_voice_only_while_the_pet_is_silent():
    """With a segmented STT, a turn started by the transcript waited a fixed 1.8 s (seen in every
    session since the switch to GPT Transcribe): the turn must start when the child starts talking."""
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )

    s = turns.QuietVADUserTurnStartStrategy()
    started = []

    async def report(*a, **kw):
        started.append(True)

    s.report_user_turn_started = report

    async def run():
        await s.process_frame(VADUserStartedSpeakingFrame())  # the pet is silent: the turn starts
        assert len(started) == 1
        await s.process_frame(VADUserStoppedSpeakingFrame())
        await s.process_frame(BotStartedSpeakingFrame())
        await s.process_frame(VADUserStartedSpeakingFrame())  # noise or voice over the pet: not on its own
        assert len(started) == 1
        await s.process_frame(BotStoppedSpeakingFrame())  # still talking when the pet stops: now
        assert len(started) == 2
        await s.process_frame(VADUserStoppedSpeakingFrame())
        await s.process_frame(BotStartedSpeakingFrame())
        await s.process_frame(BotStoppedSpeakingFrame())  # silence after the pet: nothing
        assert len(started) == 2

    asyncio.run(run())


def test_pipeline_uses_the_voice_start_unless_disabled(monkeypatch, tmp_path):
    from aipet import pipeline

    seen = {}
    real = pipeline.UserTurnStrategies

    def spy(**kw):
        seen["start"] = [type(x).__name__ for x in kw["start"]]
        return real(**kw)

    monkeypatch.setattr(pipeline, "UserTurnStrategies", spy)

    class T:
        def input(self):
            return FrameProcessor()

        def output(self):
            return FrameProcessor()

    monkeypatch.setenv("PET_HOME", str(tmp_path))
    pipeline.build_worker(T(), mute_while_speaking=False)
    assert seen["start"] == ["QuietVADUserTurnStartStrategy", "MinWordsUserTurnStartStrategy"]
