"""Turn-taking responsiveness.

- `ShortReplyKeeper` (between STT and the user aggregator): while the pet speaks, N words are needed
  to interrupt it (noise protection). A shorter answer ("bébé robot", "vert") does not cut it off,
  but is no longer lost: it is held and processed as soon as the pet stops speaking.
  Only use it when the microphone stays open while the pet speaks (browser, Jabra): with a speaker
  without echo cancellation, the pet would hear itself.
- `ThinkingFiller` (right after the LLM): if the reply is slow (slow model, tool call), the pet says
  a short "Hmm, voyons…" so the child knows they were heard. Once per turn, kept out of the context.
- `TurnGuard` (right after the LLM): knows whether the pet is busy (the model writing, a tool
  running, words not yet spoken, the child speaking). Our own processors run the model only through
  it, once the pet is idle: two requests for the same turn used to give two replies in a row.
  (An empty reply after a tool is asked again inside the LLM service: see llm_retry.)
- `UnfinishedSentenceWait`: the [attends] tag, when the child stopped mid-sentence.
- `SilenceNudge` (next to `ShortReplyKeeper`): during an activity, if nothing is heard after the pet
  finished speaking, it asks once whether the child answered (a quiet « non » can be missed).
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy

from aipet.session_log import NO_LOG
from aipet.tasks import start

# Spoken lines (French content): neutral defaults, each character pack has its own ([lines]).
TOOL_SECOND_FILLER_SECS = 9
STILL_WORKING = "Encore un tout petit instant…"
_TAGS = re.compile(r"\[[^\]]*(\]|$)")  # emotion / control tags: never spoken
_SPEAKABLE = re.compile(r"\w")
FILLERS = ["Hmm, voyons…", "Attends, je réfléchis…", "Mmmh…", "Voyons voir…", "Hmm, laisse-moi chercher…"]


class ShortReplyKeeper(FrameProcessor):
    def __init__(self, min_words: int, delay: float = 0.6, slog=None, guard: TurnGuard | None = None, **kwargs):
        super().__init__(**kwargs)
        self._min_words = min_words
        self._delay = delay
        self._slog = slog or NO_LOG
        self._guard = guard  # the held words are answered once the pet is idle (not during a tool)
        self._bot_speaking = False
        self._user_speaking = False
        self._held: list[TranscriptionFrame] = []
        self._task: asyncio.Task | None = None

    def _cancel(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _release_later(self):
        try:
            await asyncio.sleep(self._delay)
            if not self._held or self._bot_speaking or self._user_speaking:
                return
            if self._guard:
                # The pet narrated then called a tool (story_next): its reply after the tool is still
                # to come. The held words wait for it (the pet starting to speak cancels this task).
                await self._guard.run_when_idle(self._replay, "short reply held")
            else:
                await self._replay()
        except asyncio.CancelledError:
            pass

    async def _replay(self):
        if not self._held:
            return
        text = " ".join(f.text.strip() for f in self._held)
        self._held = []
        logger.info(f"↩️  Short reply held while the pet was speaking: {text!r}")
        self._slog.event("short_reply_replayed", text=text)
        await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True))

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._cancel()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._held:
                self._cancel()
                self._task = start(self._release_later(), "short reply release")
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            self._cancel()
            if self._held and direction == FrameDirection.UPSTREAM:
                # The child speaks again: what we were holding joins their new turn.
                held, self._held = self._held, []
                for f in held:
                    await self.push_frame(f, FrameDirection.DOWNSTREAM)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
        elif (
            isinstance(frame, TranscriptionFrame)
            and direction == FrameDirection.DOWNSTREAM
            and self._bot_speaking
            and len(frame.text.split()) < self._min_words
        ):
            self._held.append(frame)  # too short to interrupt the pet: keep it for later
            return
        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._cancel()
        await super().cleanup()


NUDGES = [
    "Tu m'as répondu ? Je n'ai pas bien entendu, tu peux le redire un peu plus fort ?",
    "Je n'ai rien entendu… tu peux me redire ta réponse ?",
]


class SilenceNudge(FrameProcessor):
    """During an activity, a silence after the pet's turn usually means the child's answer was
    missed: ask once (kept out of the context), then wait quietly."""

    def __init__(
        self,
        active: Callable[[], bool],
        after: float = 12,
        slog=None,
        lines: list[str] = NUDGES,
        busy: Callable[[], str] = lambda: "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._active, self._after, self._slog, self._lines = active, after, slog or NO_LOG, lines
        self._busy = busy  # TurnGuard.busy: a tool preparing the next step is not a silence
        self._task: asyncio.Task | None = None
        self._nudging = False  # our own line is being spoken: no nudge after it
        self._last = ""

    def _cancel(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _nudge_later(self):
        try:
            await asyncio.sleep(self._after)
            if not self._active():
                return
            if why := self._busy():
                logger.info(f"🔇 No nudge: the pet is busy ({why})")
                return
            line = random.choice([x for x in self._lines if x != self._last] or self._lines)
            self._last, self._nudging = line, True
            logger.info(f"🔇 Nothing heard for {self._after:.0f}s during an activity: nudge")
            self._slog.event("silence_nudge", after_s=self._after)
            await self.push_frame(TTSSpeakFrame(line, append_to_context=False))
        except asyncio.CancelledError:
            pass

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._cancel()
            if self._nudging:
                self._nudging = False  # one nudge per silence
            else:
                self._task = start(self._nudge_later(), "silence nudge")
        elif isinstance(
            frame,
            (BotStartedSpeakingFrame, UserStartedSpeakingFrame, TranscriptionFrame, InterimTranscriptionFrame),
        ):
            self._cancel()
            if not isinstance(frame, BotStartedSpeakingFrame):
                self._nudging = False
        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._cancel()
        await super().cleanup()


APOLOGY = "Oups, je me suis embrouillé ! Tu peux me redire ?"


class ThinkingFiller(FrameProcessor):
    """Says "Hmm…" if the reply is slow; cleanly gives up on the turn if nothing comes."""

    def __init__(
        self,
        after: float = 3.5,
        deadline: float = 25,
        slog=None,
        tool_deadline: float = 60,
        fillers: list[str] = FILLERS,
        still_working: str = STILL_WORKING,
        apology: str = APOLOGY,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fillers, self._still_working, self._apology = fillers, still_working, apology
        self._after = after
        self._deadline = deadline
        self._tool_deadline = tool_deadline  # a tool (story or lesson plan) may take longer
        self._slog = slog or NO_LOG
        self._task: asyncio.Task | None = None
        self._deadline_task: asyncio.Task | None = None
        self._turn_t0: float | None = None  # loop time of the turn's first model call
        self._tool_seen = False
        self._last = ""
        self._text = ""  # this model response so far
        self._spoke = False  # it already has words to say (not just tags)
        self._fill_after_speech = False  # a tool called after some narration: filler counted from its end

    def _cancel(self):
        for t in (self._task, self._deadline_task):
            if t and not t.done():
                t.cancel()
        self._task = self._deadline_task = None
        self._turn_t0 = None
        self._fill_after_speech = False

    def _start(self, tool: bool = False, filler: bool = True):
        """The model starts working (again, after a tool). The filler restarts; the deadline counts
        from the first call of the turn, so a model looping on tools cannot push it back forever."""
        loop_time = asyncio.get_running_loop().time()
        first = self._turn_t0 is None
        if self._task and not self._task.done():
            self._task.cancel()
        if self._deadline_task and not self._deadline_task.done() and (tool or not first):
            self._deadline_task.cancel()
        if first:
            self._turn_t0 = loop_time
        self._tool_seen = tool or (not first and self._tool_seen)
        self._task = start(self._fill_later(tool), "thinking filler") if filler else None
        deadline = self._tool_deadline if self._tool_seen else self._deadline
        assert self._turn_t0 is not None
        self._deadline_task = start(
            self._give_up_later(max(0.0, self._turn_t0 + deadline - loop_time), deadline), "thinking deadline"
        )

    async def _give_up_later(self, wait: float, deadline: float):
        """Maximum duration of a turn: no text from the model (tool loop, network…) → give up
        on this turn (interruption) and the pet apologizes, instead of staying silent."""
        try:
            await asyncio.sleep(wait)
            logger.warning(f"Turn abandoned: no reply after {deadline:.0f} s")
            self._slog.event("turn_abandoned", after_s=deadline)
            await self.broadcast_interruption()
            await self.push_frame(TTSSpeakFrame(self._apology, append_to_context=False))
        except asyncio.CancelledError:
            pass

    async def _fill_later(self, tool: bool = False):
        try:
            await asyncio.sleep(self._after)
            text = random.choice([f for f in self._fillers if f != self._last] or self._fillers)
            self._last = text
            logger.info(f"⏳ Slow reply: « {text} »")
            self._slog.event("filler_spoken", text=text, after_s=self._after)
            await self.push_frame(TTSSpeakFrame(text, append_to_context=False))
            if tool:  # a long tool (story or lesson plan): a second word if it still lasts
                await asyncio.sleep(TOOL_SECOND_FILLER_SECS)
                logger.info(f"⏳ Still preparing: « {self._still_working} »")
                await self.push_frame(TTSSpeakFrame(self._still_working, append_to_context=False))
        except asyncio.CancelledError:
            pass

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame) and direction == FrameDirection.DOWNSTREAM:
            # The model was really called (not on the child's stop: a cough or a dropped transcript
            # ends a turn without any model call, and must not get « Hmm… » then an apology).
            self._text, self._spoke = "", False
            self._start()
        elif isinstance(frame, FunctionCallInProgressFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._spoke:
                # The model narrated, then called a tool (story_next): the next model call only
                # starts once that narration has been played. Counting the filler from now would
                # put « Attends, je réfléchis… » right in the middle of the story: count it from
                # the end of the speech instead (the deadline still runs).
                self._start(tool=True, filler=False)
                self._fill_after_speech = True
            else:
                self._start(tool=True)  # the waiting phrase was said; the tool may take a while
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._fill_after_speech:
            self._fill_after_speech = False
            if self._turn_t0 is not None:  # still waiting for the model
                self._task = start(self._fill_later(tool=True), "thinking filler")
        elif isinstance(frame, TTSSpeakFrame) and direction == FrameDirection.DOWNSTREAM:
            # Someone upstream already answers this turn without the model (the sleeping pet's
            # « zzz », an apology): no « Hmm, je réfléchis… » after it. Our own lines never come
            # through here (pushed from this processor).
            self._cancel()
        elif isinstance(frame, (LLMTextFrame, UserStartedSpeakingFrame, InterruptionFrame)):
            if isinstance(frame, LLMTextFrame):
                self._text += frame.text  # streamed: a tag may come in pieces
                self._spoke = bool(_SPEAKABLE.search(_TAGS.sub("", self._text)))
            # The reply is arriving (even "[silence]"), or the child speaks again. Not on
            # BotStartedSpeaking: our own "Hmm" must not cancel the deadline.
            self._cancel()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._cancel()
        await super().cleanup()


class TurnGuard(FrameProcessor):
    """Right after the LLM: tells whether the pet is busy, and is the only way our own processors run
    the model (`run_when_idle`).

    Pipecat runs the model on the child's turn and after a tool result. Our processors add their own
    requests (a short reply held while the pet spoke, « answer now » after [attends]); sent straight
    into the pipeline, they ran at once, even while a reply or a tool was on its way, and the child
    heard two replies in a row. Here a request waits until the pet is idle: nothing being written,
    no tool running, nothing left to say, nobody speaking, for a short moment. One request at a time.
    """

    POLL_SECS = 0.05
    SETTLE_SECS = 0.4  # idle this long: a reply about to start (child's turn just ended) shows up first
    MAX_WAIT_SECS = 60  # then the request is dropped (it is stale by then)
    SPEECH_GRACE_SECS = 20  # words written but never heard (voice failure): not busy forever
    REQUEST_GRACE_SECS = 5  # a request sent that never started the model (the sleeping pet answered)
    TOOL_GRACE_SECS = 120  # a tool whose end was missed

    def __init__(self, slog=None, clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._slog = slog or NO_LOG
        self._clock = clock
        self._lock = asyncio.Lock()
        self._writing = False  # between the start and the end of a model response
        self._tools: dict[str, float] = {}  # running tools (not the asynchronous ones), by call id
        self._preparing: dict[str, float] = {}  # asynchronous tools (a long preparation): the pet goes on
        self._text = ""
        self._unspoken_since: float | None = None  # words written (or a line queued), not yet spoken
        self._bot_speaking = False
        self._user_speaking = False
        self._requested_at: float | None = None

    def busy(self) -> str:
        """Why the pet is busy (empty: idle)."""
        now = self._clock()
        if self._user_speaking:
            return "child speaking"
        if self._writing:
            return "model writing"
        if any(now - t0 < self.TOOL_GRACE_SECS for t0 in self._tools.values()):
            return "tool running"
        if self._bot_speaking:
            return "pet speaking"
        if self._unspoken_since is not None and now - self._unspoken_since < self.SPEECH_GRACE_SECS:
            return "words to speak"
        if self._requested_at is not None and now - self._requested_at < self.REQUEST_GRACE_SECS:
            return "model requested"
        return ""

    def preparing(self) -> bool:
        """An asynchronous tool is preparing something (a story, an adventure): not busy, but the
        child may well be waiting for it."""
        now = self._clock()
        return any(now - t0 < self.TOOL_GRACE_SECS for t0 in self._preparing.values())

    async def _idle(self) -> bool:
        deadline = self._clock() + self.MAX_WAIT_SECS
        quiet_since: float | None = None
        while True:
            now = self._clock()
            if self.busy():
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= self.SETTLE_SECS:
                return True
            if now >= deadline:
                return False
            await asyncio.sleep(self.POLL_SECS)

    async def run_when_idle(self, send: Callable[[], Awaitable[None]], reason: str) -> bool:
        """Wait until the pet is idle, then `send()` (frames that run the model). Cancel the calling
        task to withdraw the request. False if the pet stayed busy too long (nothing sent)."""
        async with self._lock:
            if why := self.busy():
                logger.info(f"⏳ {reason}: waiting, the pet is busy ({why})")
            if not await self._idle():
                logger.warning(f"{reason}: dropped, the pet stayed busy ({self.busy()})")
                self._slog.event("run_dropped", reason=reason, busy=self.busy())
                return False
            self._requested_at = self._clock()
            await send()
            return True

    def _track(self, frame) -> None:
        now = self._clock()
        if isinstance(frame, LLMFullResponseStartFrame):
            self._writing, self._text, self._requested_at = True, "", None
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._writing = False
        elif isinstance(frame, LLMTextFrame):
            self._text += frame.text
            if self._unspoken_since is None and _SPEAKABLE.search(_TAGS.sub("", self._text)):
                self._unspoken_since = now
        elif isinstance(frame, TTSSpeakFrame):
            self._unspoken_since = self._unspoken_since or now
        elif isinstance(frame, FunctionCallsStartedFrame):
            for call in frame.function_calls:
                self._tools[call.tool_call_id] = now
        elif isinstance(frame, FunctionCallInProgressFrame):
            if not frame.cancel_on_interruption:  # asynchronous (a long preparation): the pet goes on
                self._tools.pop(frame.tool_call_id, None)
                self._preparing[frame.tool_call_id] = now
            elif frame.tool_call_id not in self._tools:
                self._tools[frame.tool_call_id] = now
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            if not isinstance(frame, FunctionCallResultFrame) or not frame.properties or frame.properties.is_final:
                self._tools.pop(frame.tool_call_id, None)
                self._preparing.pop(frame.tool_call_id, None)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking, self._unspoken_since = False, None
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
        elif isinstance(frame, InterruptionFrame):
            # The reply in progress is dropped (its end frame may never come); tools are cancelled
            # by their own frames.
            self._writing, self._unspoken_since, self._bot_speaking = False, None, False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self._track(frame)
        await self.push_frame(frame, direction)


class UnfinishedSentenceWait:
    """The [attends] tag: the child stopped in the middle of a sentence (searching for words).

    The pet says nothing and gives them time to finish. Safeguards so normal exchanges are never
    slowed down:
    - zero cost when the tag is not used (the reply goes straight out);
    - the wait is short (`delay`) and stops as soon as the child speaks again (it pauses while a
      voice is heard, which may be noise: it starts over when the voice stops, unless the child's
      turn started);
    - if nothing comes, the pet answers what was said (the model is re-run, without [attends]);
    - at most `max_in_a_row` waits in a row: beyond that, answer right away;
    - if the model still wants to wait after "answer now", the pet says "Je t'écoute".
    Every wait is logged (useful = the child continued, expired = they had finished) so
    analyze.py can check that waits stay rare and justified.
    """

    # Model-facing / spoken texts (French content).
    NOTE = "⟦système : l'enfant n'a rien ajouté. Réponds maintenant à ce qu'il a dit, sans [attends].⟧"
    NUDGE = "Oui ? Je t'écoute."

    def __init__(
        self,
        delay: float = 2.5,
        max_in_a_row: int = 2,
        enabled: bool = True,
        slog=None,
        queue=None,
        guard: TurnGuard | None = None,
    ):
        self.delay, self.max_in_a_row, self.enabled, self.slog = delay, max_in_a_row, enabled, slog or NO_LOG
        self.queue = queue  # async (frames) -> None: queues frames into the running pipeline
        self.guard = guard  # « answer now » is sent once the pet is idle (not during the [attends] reply)
        self._task: asyncio.Task | None = None
        self._answer_task: asyncio.Task | None = None
        self._paused = False  # a voice is heard during the wait (the child, or noise)
        self._in_a_row = 0  # consecutive waits for the same sentence
        self._nudged = False  # we just asked the model to "answer now"

    @property
    def waiting(self) -> bool:
        return bool(self._task and not self._task.done())

    @property
    def _answering(self) -> bool:
        """« Answer now » is waiting for the pet to be idle."""
        return bool(self._answer_task and not self._answer_task.done())

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    def _cancel(self):
        if self.waiting:
            self._task.cancel()
        if self._answer_task and not self._answer_task.done():
            self._answer_task.cancel()
        self._task = self._answer_task = None
        self._paused = False

    async def on_wait_tag(self):
        if self._nudged:  # it still wants to wait although we told it to answer
            self._nudged, self._in_a_row = False, 0
            logger.info("⏸️  [attends] after the nudge: « Je t'écoute »")
            self._ev("wait_nudge")
            await self.queue([TTSSpeakFrame(self.NUDGE, append_to_context=False)])
            return
        self._in_a_row += 1
        if not self.enabled or self._in_a_row > self.max_in_a_row:
            logger.info(f"⏸️  [attends] ignored ({'disabled' if not self.enabled else 'too many in a row'}): answering")
            self._ev("wait_ignored", in_a_row=self._in_a_row)
            await self._answer_now()
            return
        logger.info(f"⏸️  [attends]: the child has not finished, waiting {self.delay:.1f} s")
        self._ev("wait", in_a_row=self._in_a_row, delay_s=self.delay)
        self._cancel()
        self._task = start(self._later(), "unfinished sentence wait")

    async def _later(self):
        try:
            await asyncio.sleep(self.delay)
            self._task = None
            logger.info("⏸️  Wait expired: the pet answers")
            self._ev("wait_expired", after_s=self.delay)
            await self._answer_now()
        except asyncio.CancelledError:
            pass

    async def _answer_now(self):
        self._nudged = True
        if self.guard is None:
            await self._send_answer()
        else:
            # Maybe called while the [attends] reply is still being written: the note must come after it.
            guard = self.guard
            self._answer_task = start(guard.run_when_idle(self._send_answer, "answer now"), "answer now")

    async def _send_answer(self):
        await self.queue([LLMMessagesAppendFrame(messages=[{"role": "user", "content": self.NOTE}], run_llm=True)])

    def child_speaks(self):
        """The child speaks again (their turn started): the wait was useful."""
        if self.waiting or self._paused:
            self._cancel()
            logger.info("⏸️  The child continues their sentence")
            self._ev("wait_useful")
        elif self._answering:
            self._cancel()  # their new turn will be answered

    def voice_started(self):
        """A voice during the wait: maybe the child going on, maybe noise. The countdown pauses (a
        child resuming at 2 s must not be answered on half a sentence)."""
        if self.waiting:
            self._task.cancel()
            self._task, self._paused = None, True

    def voice_stopped(self):
        """The voice stopped without starting the child's turn (noise, a word too short): the wait
        starts over."""
        if self._paused:
            self._paused = False
            self._task = start(self._later(), "unfinished sentence wait")

    def replied(self):
        """The pet stopped speaking: it replied normally (or [silence]), start over. Not while
        waiting: the model only said [attends], so what just ended was a filler (« Hmm… ») or a
        canned line, and the wait must still end with an answer."""
        if self.waiting or self._paused or self._answering:
            return
        self._in_a_row, self._nudged = 0, False

    def cancel(self):
        self._cancel()


REPLY_PENDING_SECS = 10  # the pet's reply is awaited this long after the child's turn (then voice starts a turn again)


class QuietVADUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """The child's turn starts when they start speaking, but only while the pet is silent.

    While the pet speaks, interrupting still takes a few transcribed words (MinWords strategy, next
    in the list), so noise never cuts it off. Starting the turn on voice matters with a segmented
    STT (one transcript per utterance, after the child stopped): a turn started by that transcript
    makes Pipecat forget the end of speech it already saw, and wait a fixed safety delay (the STT's
    p99 latency minus the VAD silence, 1.8 s for OpenAI) instead of asking Smart Turn whether the
    sentence is finished.

    Between the end of the child's turn and the end of the pet's reply, voice alone does not start
    a turn either: in a lively room (seen on the Pi), a noise or a voice in the background just after
    the child spoke cancelled every reply before a sound came out. The child who goes on talking
    still cuts in, once the transcript has words (MinWords strategy, 1 word while the pet is silent).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._bot_speaking = False
        self._user_speaking = False
        self._clock = clock
        self._reply_until = 0.0  # a reply is on its way until then (or until the pet stops speaking)

    async def handle_user_turn_stopped(self):
        self._reply_until = self._clock() + REPLY_PENDING_SECS

    async def process_frame(self, frame):
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking, self._reply_until = False, 0.0
            if self._user_speaking:  # the child was already talking when the pet finished
                await self.report_user_turn_started()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if not self._bot_speaking and self._clock() >= self._reply_until:
                await self.report_user_turn_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
