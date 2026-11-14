"""Turn-taking responsiveness.

- `ShortReplyKeeper` (between STT and the user aggregator): while the pet speaks, N words are needed
  to interrupt it (noise protection). A shorter answer ("bébé robot", "vert") does not cut it off,
  but is no longer lost: it is held and processed as soon as the pet stops speaking.
  Only use it when the microphone stays open while the pet speaks (browser, Jabra): with a speaker
  without echo cancellation, the pet would hear itself.
- `ThinkingFiller` (right after the LLM): if the reply is slow (slow model, tool call), the pet says
  a short "Hmm, voyons…" so the child knows they were heard. Once per turn, kept out of the context.
- `UnfinishedSentenceWait`: the [attends] tag, when the child stopped mid-sentence.
- `SilenceNudge` (next to `ShortReplyKeeper`): during an activity, if nothing is heard after the pet
  finished speaking, it asks once whether the child answered (a quiet « non » can be missed).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
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

# Spoken lines (French content): neutral defaults, each character pack has its own ([lines]).
TOOL_SECOND_FILLER_SECS = 9
STILL_WORKING = "Encore un tout petit instant…"
FILLERS = ["Hmm, voyons…", "Attends, je réfléchis…", "Mmmh…", "Voyons voir…", "Hmm, laisse-moi chercher…"]


class ShortReplyKeeper(FrameProcessor):
    def __init__(self, min_words: int, delay: float = 0.6, slog=None, **kwargs):
        super().__init__(**kwargs)
        self._min_words = min_words
        self._delay = delay
        self._slog = slog or NO_LOG
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
            if self._held and not self._bot_speaking and not self._user_speaking:
                text = " ".join(f.text.strip() for f in self._held)
                self._held = []
                logger.info(f"↩️  Short reply held while the pet was speaking: {text!r}")
                self._slog.event("short_reply_replayed", text=text)
                await self.push_frame(
                    LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True)
                )
        except asyncio.CancelledError:
            pass

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._cancel()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._held:
                self._cancel()
                self._task = asyncio.create_task(self._release_later())
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

    def __init__(self, active: Callable[[], bool], after: float = 12, slog=None, lines: list[str] = NUDGES, **kwargs):
        super().__init__(**kwargs)
        self._active, self._after, self._slog, self._lines = active, after, slog or NO_LOG, lines
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
                self._task = asyncio.create_task(self._nudge_later())
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
        self._last = ""

    def _cancel(self):
        for t in (self._task, self._deadline_task):
            if t and not t.done():
                t.cancel()
        self._task = self._deadline_task = None

    def _start(self, tool: bool = False):
        self._cancel()
        self._task = asyncio.create_task(self._fill_later(tool))
        self._deadline_task = asyncio.create_task(self._give_up_later(self._tool_deadline if tool else self._deadline))

    async def _give_up_later(self, deadline: float):
        """Maximum duration of a turn: no text from the model (tool loop, network…) → give up
        on this turn (interruption) and the pet apologizes, instead of staying silent."""
        try:
            await asyncio.sleep(deadline)
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
        if isinstance(frame, UserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            self._start()
        elif isinstance(frame, LLMMessagesAppendFrame) and frame.run_llm:
            self._start()
        elif isinstance(frame, FunctionCallInProgressFrame) and direction == FrameDirection.DOWNSTREAM:
            self._start(tool=True)  # the waiting phrase was said; the tool may take a while
        elif isinstance(frame, TTSSpeakFrame) and direction == FrameDirection.DOWNSTREAM:
            # Someone upstream already answers this turn without the model (the sleeping pet's
            # « zzz », an apology): no « Hmm, je réfléchis… » after it. Our own lines never come
            # through here (pushed from this processor).
            self._cancel()
        elif isinstance(frame, (LLMTextFrame, UserStartedSpeakingFrame, InterruptionFrame)):
            # The reply is arriving (even "[silence]"), or the child speaks again. Not on
            # BotStartedSpeaking: our own "Hmm" must not cancel the deadline.
            self._cancel()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._cancel()
        await super().cleanup()


class UnfinishedSentenceWait:
    """The [attends] tag: the child stopped in the middle of a sentence (searching for words).

    The pet says nothing and gives them time to finish. Safeguards so normal exchanges are never
    slowed down:
    - zero cost when the tag is not used (the reply goes straight out);
    - the wait is short (`delay`) and stops as soon as the child speaks again;
    - if nothing comes, the pet answers what was said (the model is re-run, without [attends]);
    - at most `max_in_a_row` waits in a row: beyond that, answer right away;
    - if the model still wants to wait after "answer now", the pet says "Je t'écoute".
    Every wait is logged (useful = the child continued, expired = they had finished) so
    analyze.py can check that waits stay rare and justified.
    """

    # Model-facing / spoken texts (French content).
    NOTE = "⟦système : l'enfant n'a rien ajouté. Réponds maintenant à ce qu'il a dit, sans [attends].⟧"
    NUDGE = "Oui ? Je t'écoute."

    def __init__(self, delay: float = 2.5, max_in_a_row: int = 2, enabled: bool = True, slog=None, queue=None):
        self.delay, self.max_in_a_row, self.enabled, self.slog = delay, max_in_a_row, enabled, slog or NO_LOG
        self.queue = queue  # async (frames) -> None: queues frames into the running pipeline
        self._task: asyncio.Task | None = None
        self._in_a_row = 0  # consecutive waits for the same sentence
        self._nudged = False  # we just asked the model to "answer now"

    @property
    def waiting(self) -> bool:
        return bool(self._task and not self._task.done())

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    def _cancel(self):
        if self.waiting:
            self._task.cancel()
        self._task = None

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
        self._task = asyncio.create_task(self._later())

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
        await self.queue([LLMMessagesAppendFrame(messages=[{"role": "user", "content": self.NOTE}], run_llm=True)])

    def child_speaks(self):
        """The child speaks again: the wait was useful."""
        if self.waiting:
            self._cancel()
            logger.info("⏸️  The child continues their sentence")
            self._ev("wait_useful")

    def replied(self):
        """The pet replied normally (or [silence]): start over."""
        self._cancel()
        self._in_a_row, self._nudged = 0, False

    def cancel(self):
        self._cancel()


class QuietVADUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """The child's turn starts when they start speaking, but only while the pet is silent.

    While the pet speaks, interrupting still takes a few transcribed words (MinWords strategy, next
    in the list), so noise never cuts it off. Starting the turn on voice matters with a segmented
    STT (one transcript per utterance, after the child stopped): a turn started by that transcript
    makes Pipecat forget the end of speech it already saw, and wait a fixed safety delay (the STT's
    p99 latency minus the VAD silence, 1.8 s for OpenAI) instead of asking Smart Turn whether the
    sentence is finished.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bot_speaking = False
        self._user_speaking = False

    async def process_frame(self, frame):
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._user_speaking:  # the child was already talking when the pet finished
                await self.report_user_turn_started()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if not self._bot_speaking:
                await self.report_user_turn_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
