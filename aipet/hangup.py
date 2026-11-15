"""Hanging up: the pet ends the conversation by itself.

It hangs up a few seconds after its last words when it has a reason to (`reason()`): it sleeps
('nap', 'night'), has just said the evening « bonne nuit » ('night'), or the child said goodbye
('goodbye', the [fin] tag). Someone speaking cancels it; the reason is checked again when they stop.
After a sleepy « zzz » (`SleepyReplyFrame`) it hangs up at once: the pet is already asleep.

What hanging up means depends on the mode (`on_hang_up`): the web page closes the call, the Pi goes
back to waiting for its wake word.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from aipet.session_log import NO_LOG
from aipet.tasks import start

AFTER_SLEEPY_REPLY_SECS = 1.0


class SleepyReplyFrame(TTSSpeakFrame):
    """A sleepy line said instead of a model reply (see energy.SleepGate)."""


class HangUp(FrameProcessor):
    """Placed after the SleepGate: sees the child start speaking (downstream), the pet start and stop
    speaking (pushed upstream by the output) and the sleepy replies."""

    def __init__(
        self,
        reason: Callable[[], str | None],
        on_hang_up: Callable[[str], Awaitable[None]],
        after: float = 8.0,
        slog=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._reason, self._on_hang_up, self._after = reason, on_hang_up, after
        self._slog = slog or NO_LOG
        self._asleep_reply = False
        self._task: asyncio.Task | None = None

    def _cancel(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _later(self, delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        reason = self._reason()
        if not reason:
            return  # woken up meanwhile (recharged by a parent, morning)
        logger.info(f"📞 Hanging up ({reason})")
        self._slog.event("hang_up", reason=reason)
        self._asleep_reply = False
        await self._on_hang_up(reason)

    async def cleanup(self):
        self._cancel()
        await super().cleanup()

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, SleepyReplyFrame):
            self._asleep_reply = True
        elif isinstance(frame, (UserStartedSpeakingFrame, BotStartedSpeakingFrame)):
            self._cancel()  # someone speaks: decide again when it ends
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._reason():
            self._cancel()
            delay = AFTER_SLEEPY_REPLY_SECS if self._asleep_reply else self._after
            self._task = start(self._later(delay), "hang up")
        await self.push_frame(frame, direction)
