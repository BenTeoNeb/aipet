"""Short replies held while Flocon speaks, "Hmm" when the reply is slow, unfinished-sentence wait."""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseStartFrame,
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
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.1)
        hmm = [f for f, _ in t.out if isinstance(f, TTSSpeakFrame)]
        assert len(hmm) == 1 and hmm[0].append_to_context is False

    asyncio.run(run())


def test_no_filler_when_reply_arrives(make):
    async def run():
        t = make(turns.ThinkingFiller, after=0.1)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("[silence]"), D.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


def test_no_filler_after_the_sleeping_pets_zzz(make):
    """Seen in a real session: asleep, the pet answered « zzz » then « Attends, je réfléchis… »."""

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=0.1)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
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
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.1)
        assert interrupted
        assert any(isinstance(f, TTSSpeakFrame) and f.text == turns.APOLOGY for f, _ in t.out)

    asyncio.run(run())


def test_no_abandon_when_text_arrives(make):
    async def run():
        t = make(turns.ThinkingFiller, after=10, deadline=0.1)
        t.broadcast_interruption = None  # must not be called
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Oh !"), D.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


def test_no_filler_for_a_turn_without_model_call(make):
    """A cough or a dropped transcript ends the child's turn, but the model is never called."""

    async def run():
        t = make(turns.ThinkingFiller, after=0.03, deadline=0.05)
        t.broadcast_interruption = None  # must not be called
        await t.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.1)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


def test_a_tool_loop_cannot_push_the_deadline_back(make):
    from pipecat.frames.frames import FunctionCallInProgressFrame

    async def run():
        t = make(turns.ThinkingFiller, after=10, deadline=0.05, tool_deadline=0.15)
        interrupted = []

        async def bi():
            interrupted.append(True)

        t.broadcast_interruption = bi
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        for i in range(4):  # the model keeps calling (refused) tools
            call = FunctionCallInProgressFrame(function_name="x", tool_call_id=str(i), arguments={})
            await t.process_frame(call, D.DOWNSTREAM)
            await asyncio.sleep(0.03)
            await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.08)
        assert interrupted  # 0.15 s after the turn started, not after the last call

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
        w.child_speaks()
        w.replied()
        await w.on_wait_tag()  # would be ignored without the reset
        assert w.waiting and not w.queued
        w.cancel()

    asyncio.run(run())


def test_a_filler_ending_does_not_cancel_the_wait():
    """« Hmm… » was playing when [attends] came: its end is not a reply."""

    async def run():
        w = make_wait(delay=0.05)
        await w.on_wait_tag()
        w.replied()  # the filler stops speaking
        await asyncio.sleep(0.1)
        assert len(answer_requests(w)) == 1  # the pet still answers

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
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Laisse-moi imaginer…"), D.DOWNSTREAM)  # the waiting phrase
        await t.process_frame(
            FunctionCallInProgressFrame(function_name="story_start", tool_call_id="1", arguments={}), D.DOWNSTREAM
        )
        await t.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        await t.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)  # the waiting phrase was played
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


def test_voice_alone_does_not_cancel_the_reply_on_its_way():
    """Seen on the Pi: a noise or a background voice just after the child spoke cancelled every
    reply before a sound came out (« bip, then nothing »)."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, VADUserStartedSpeakingFrame

    t = [0.0]
    s = turns.QuietVADUserTurnStartStrategy(clock=lambda: t[0])
    started = []

    async def report(*a, **kw):
        started.append(True)

    s.report_user_turn_started = report

    async def run():
        await s.handle_user_turn_stopped()  # the child's turn ends: the reply is on its way
        await s.process_frame(VADUserStartedSpeakingFrame())  # noise: no turn (the transcript decides)
        assert started == []
        await s.process_frame(BotStartedSpeakingFrame())
        await s.process_frame(BotStoppedSpeakingFrame())  # still noisy when the pet stops: as before
        assert len(started) == 1
        await s.handle_user_turn_stopped()
        t[0] += turns.REPLY_PENDING_SECS + 1  # no reply came ([silence]): voice starts turns again
        await s.process_frame(VADUserStartedSpeakingFrame())
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


def test_no_filler_in_the_middle_of_a_narration(make):
    """Seen in a real session: the pet narrated, called story_next, and « Attends, je réfléchis… »
    came between two parts of the story (the next model call waits for the narration to be played)."""
    from pipecat.frames.frames import FunctionCallInProgressFrame

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=1, tool_deadline=1)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("[parle:Zina] [joie] Raya fait frétiller ses nageoires !"), D.DOWNSTREAM)
        await t.process_frame(
            FunctionCallInProgressFrame(function_name="story_next", tool_call_id="1", arguments={}), D.DOWNSTREAM
        )
        await asyncio.sleep(0.15)  # the narration is still being played
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)
        await t.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)  # the follow-up starts at once
        await t.process_frame(LLMTextFrame("Ça alors !"), D.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


def test_filler_after_a_narration_if_the_follow_up_is_slow(make):
    from pipecat.frames.frames import FunctionCallInProgressFrame

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=1, tool_deadline=1)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Hop !"), D.DOWNSTREAM)
        await t.process_frame(
            FunctionCallInProgressFrame(function_name="story_next", tool_call_id="1", arguments={}), D.DOWNSTREAM
        )
        await t.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.1)
        assert [f.text for f, _ in t.out if isinstance(f, TTSSpeakFrame)][0] in turns.FILLERS

    asyncio.run(run())


def test_tags_alone_are_not_a_narration(make):
    """« [parle:Rami] » then a tool: nothing will be played, so the filler is counted at once."""
    from pipecat.frames.frames import FunctionCallInProgressFrame

    async def run():
        t = make(turns.ThinkingFiller, after=0.05, deadline=1, tool_deadline=1)
        await t.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("[parle:"), D.DOWNSTREAM)
        await t.process_frame(LLMTextFrame("Rami] "), D.DOWNSTREAM)
        await t.process_frame(
            FunctionCallInProgressFrame(function_name="story_start", tool_call_id="1", arguments={}), D.DOWNSTREAM
        )
        await asyncio.sleep(0.1)
        assert any(isinstance(f, TTSSpeakFrame) for f, _ in t.out)

    asyncio.run(run())


# ---- an empty reply after a tool is asked again, inside the LLM service ----------------------
class FakeService:
    """Stands for a Pipecat LLM service: each model call plays the next scripted reply."""

    def __init__(self, replies):
        self.replies, self.calls, self.said, self.tools = list(replies), [], [], []

    async def _push_llm_text(self, text):
        self.said.append(text)

    async def run_function_calls(self, function_calls):
        self.tools.extend(function_calls)

    async def _process_context(self, context):
        self.calls.append([m["content"] for m in context.get_messages() if m["role"] == "user"])
        kind, value = self.replies.pop(0) if self.replies else ("text", "")
        if kind == "text":
            await self._push_llm_text(value)
        else:
            await self.run_function_calls([value])


def _service(*replies, skip=lambda: False):
    from aipet.llm_retry import with_empty_reply_retry

    svc = with_empty_reply_retry(FakeService)(replies)
    svc.empty_reply_skip, svc.empty_reply_slog = skip, FakeLog()
    return svc


def _after_tool_context():
    from pipecat.processors.aggregators.llm_context import LLMContext

    return LLMContext(
        [
            {"role": "user", "content": "la suite !"},
            {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "story_next", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "{}"},
        ]
    )  # fmt: skip


def test_empty_reply_after_a_tool_is_asked_again_once():
    """Seen in a real session: after the last story_next the model said nothing, no ending told."""
    from aipet.llm_retry import EmptyReplyRetry

    async def run():
        svc = _service(("text", ""), ("text", ""))
        await svc._process_context(_after_tool_context())
        assert len(svc.calls) == 2 and svc.calls[1][-1] == EmptyReplyRetry.NOTE  # within the same response
        assert svc.empty_reply_slog.events == ["empty_reply_retry"]  # the retry was empty too: never a loop

    asyncio.run(run())


def test_a_reply_with_words_or_a_tool_call_is_not_empty():
    """Seen on 21/11/2026: the reply after the winning move was only a call to adventure_end; it
    looked empty, was asked again, and the ending was told twice."""

    async def run():
        for reply in (("text", "[silence]"), ("text", "Bravo !"), ("tool", "adventure_end")):
            svc = _service(reply)
            await svc._process_context(_after_tool_context())
            assert len(svc.calls) == 1, reply

    asyncio.run(run())


def test_no_retry_outside_a_tool_follow_up_or_after_goodbye():
    from pipecat.processors.aggregators.llm_context import LLMContext

    async def run():
        svc = _service(("text", ""))
        await svc._process_context(LLMContext([{"role": "user", "content": "euh"}]))  # the child's turn
        svc2 = _service(("text", ""), skip=lambda: True)  # the pet said goodbye: its silence is wanted
        await svc2._process_context(_after_tool_context())
        assert len(svc.calls) == len(svc2.calls) == 1

    asyncio.run(run())


# ---- the turn guard: our own model runs wait until the pet is idle --------------------------
@pytest.fixture
def quick_guard(monkeypatch):
    monkeypatch.setattr(turns.TurnGuard, "POLL_SECS", 0.005)
    monkeypatch.setattr(turns.TurnGuard, "SETTLE_SECS", 0.02)


def _calls(*ids, asynchronous=()):
    from pipecat.frames.frames import FunctionCallsStartedFrame
    from pipecat.services.llm_service import FunctionCallFromLLM

    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name="f", tool_call_id=i, arguments={}, context=None) for i in ids]
    )


def test_guard_waits_for_the_reply_and_the_tool(make, quick_guard):
    """The pet narrates then calls story_next: a request made meanwhile waits for the tool, its
    follow-up reply and its speech (it used to run at once: two replies in a row)."""
    from pipecat.frames.frames import FunctionCallResultFrame, LLMFullResponseEndFrame

    async def run():
        g = make(turns.TurnGuard)
        sent = []

        async def send():
            sent.append(asyncio.get_running_loop().time())

        await g.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await g.process_frame(LLMTextFrame("Il était une fois…"), D.DOWNSTREAM)
        await g.process_frame(_calls("1"), D.DOWNSTREAM)
        await g.process_frame(LLMFullResponseEndFrame(), D.DOWNSTREAM)
        task = asyncio.ensure_future(g.run_when_idle(send, "test"))
        await asyncio.sleep(0.05)
        assert g.busy() == "tool running" and not sent
        await g.process_frame(FunctionCallResultFrame("f", "1", {}, {}), D.DOWNSTREAM)
        assert g.busy() == "words to speak"  # the narration is not spoken yet
        await g.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        await g.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await g.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)  # the follow-up starts
        await asyncio.sleep(0.05)
        assert not sent
        await g.process_frame(LLMFullResponseEndFrame(), D.DOWNSTREAM)  # silent follow-up ([silence])
        assert await task is True and len(sent) == 1
        assert g.busy() == "model requested"  # a second request waits for this one to start

    asyncio.run(run())


def test_guard_request_can_be_withdrawn_and_is_dropped_when_too_late(make, quick_guard, monkeypatch):
    async def run():
        g = make(turns.TurnGuard, slog=FakeLog())
        sent = []

        async def send():
            sent.append(1)

        await g.process_frame(UserStartedSpeakingFrame(), D.DOWNSTREAM)
        task = asyncio.ensure_future(g.run_when_idle(send, "test"))
        await asyncio.sleep(0.02)
        task.cancel()  # e.g. the child's own turn makes it pointless
        await g.process_frame(UserStoppedSpeakingFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.05)
        assert not sent
        monkeypatch.setattr(turns.TurnGuard, "MAX_WAIT_SECS", 0.03)
        await g.process_frame(UserStartedSpeakingFrame(), D.DOWNSTREAM)
        assert await g.run_when_idle(send, "test") is False and not sent
        assert g._slog.events == ["run_dropped"]

    asyncio.run(run())


def test_an_asynchronous_preparation_does_not_make_the_pet_busy(make):
    from pipecat.frames.frames import FunctionCallInProgressFrame, FunctionCallResultFrame

    async def run():
        g = make(turns.TurnGuard)
        await g.process_frame(_calls("p"), D.DOWNSTREAM)
        await g.process_frame(
            FunctionCallInProgressFrame(
                function_name="story_start", tool_call_id="p", arguments={}, cancel_on_interruption=False
            ),
            D.DOWNSTREAM,
        )
        assert g.busy() == "" and g.preparing()
        await g.process_frame(FunctionCallResultFrame("story_start", "p", {}, {}), D.DOWNSTREAM)
        assert not g.preparing()

    asyncio.run(run())


def test_short_reply_waits_for_the_tool_follow_up(make, quick_guard):
    """« vert » said during the narration before story_next: answered after the tool's reply, not
    alongside it."""
    from pipecat.frames.frames import FunctionCallResultFrame

    async def run():
        g = make(turns.TurnGuard)
        k = make(turns.ShortReplyKeeper, 3, delay=0.01, guard=g)
        await g.process_frame(_calls("1"), D.DOWNSTREAM)
        await k.process_frame(BotStartedSpeakingFrame(), D.UPSTREAM)
        await k.process_frame(tr("vert"), D.DOWNSTREAM)
        await k.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.06)
        assert not any(isinstance(f, LLMMessagesAppendFrame) for f, _ in k.out)
        await g.process_frame(FunctionCallResultFrame("f", "1", {}, {}), D.DOWNSTREAM)
        await asyncio.sleep(0.06)
        replayed = [f for f, _ in k.out if isinstance(f, LLMMessagesAppendFrame)]
        assert len(replayed) == 1 and replayed[0].messages[0]["content"] == "vert"

    asyncio.run(run())


def test_silence_nudge_not_while_the_pet_is_busy(make):
    async def run():
        n = make(turns.SilenceNudge, lambda: True, after=0.02, busy=lambda: "tool running")
        await n.process_frame(BotStoppedSpeakingFrame(), D.UPSTREAM)
        await asyncio.sleep(0.05)
        assert not any(isinstance(f, TTSSpeakFrame) for f, _ in n.out)

    asyncio.run(run())


def test_answer_now_waits_for_the_attends_reply_to_end(make, quick_guard):
    """Too many [attends]: « answer now » is asked while that reply is still being written; it is
    sent after it (it used to land in the context before the pet's own message)."""
    from pipecat.frames.frames import LLMFullResponseEndFrame

    async def run():
        g = make(turns.TurnGuard)
        w = make_wait(delay=10, enabled=False)
        w.guard = g
        await g.process_frame(LLMFullResponseStartFrame(), D.DOWNSTREAM)
        await w.on_wait_tag()
        await asyncio.sleep(0.05)
        assert not answer_requests(w)
        await g.process_frame(LLMFullResponseEndFrame(), D.DOWNSTREAM)
        await asyncio.sleep(0.06)
        assert len(answer_requests(w)) == 1

    asyncio.run(run())


def test_a_voice_pauses_the_wait():
    """The child resumes their sentence at 2 s: no answer on half a sentence. Noise: the wait starts over."""

    async def run():
        w = make_wait(delay=0.05)
        await w.on_wait_tag()
        w.voice_started()
        await asyncio.sleep(0.08)
        assert not answer_requests(w)  # paused while the voice lasts
        w.voice_stopped()  # it was noise: the countdown starts over
        await asyncio.sleep(0.02)
        assert not answer_requests(w)
        await asyncio.sleep(0.06)
        assert len(answer_requests(w)) == 1
        w2 = make_wait(delay=0.05)
        await w2.on_wait_tag()
        w2.voice_started()
        w2.child_speaks()  # it was the child: their turn
        w2.voice_stopped()
        await asyncio.sleep(0.08)
        assert not w2.queued and w2.slog.events == ["wait", "wait_useful"]

    asyncio.run(run())
