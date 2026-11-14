"""The pet's energy: a pet gets tired, takes naps, and sleeps at night.

Energy drains during active conversation and recharges while nobody talks. Below thresholds a hint
is added to each user message (the pet yawns, then says goodbye); at zero the pet really naps until
recharged. At night (see `rhythm.Rhythm`) he sleeps until morning. While asleep, `SleepGate`
answers with a short sleepy line without calling the model.

Rules: energy never runs out in the middle of a sad or comforting moment (`protect`), and parents
can recharge the pet from the debug panel.
"""

from __future__ import annotations

import asyncio
import json
import random
import time as _time
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMContextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

ACTIVE_GAP = 90  # seconds: two exchanges closer than this count as continuous conversation
PROTECT_SECS = 180  # a sad/comforting moment holds off the nap for this long
TIRED, VERY_TIRED = 0.4, 0.15

# Said while asleep (French, spoken; no model call). Neutral defaults: each character pack has its
# own ([lines].nap / .night).
NAP_LINES = ["Zzz… je fais une petite sieste… reviens tout à l'heure… zzz…"]
NIGHT_LINES = ["Chut… zzz… je dors… on se parle demain…"]


class Energy:
    def __init__(
        self,
        talk_minutes: float = 20,
        recharge_minutes: float = 30,
        wake_at: float = 0.5,
        state_file: Path | None = None,
        clock: Callable[[], float] = _time.time,
        slog=None,
    ):
        self.drain = 1 / (talk_minutes * 60)
        self.charge = 1 / (recharge_minutes * 60)
        self.wake_at = wake_at
        self.state_file = state_file
        self.clock = clock
        self.slog = slog
        self._level = 1.0
        self._asleep = False
        self._last_activity: float | None = None
        self._protect_until = 0.0
        self._last_state = "normal"
        self._load()

    # ---------------------------------------------------------------- state
    def level(self) -> float:
        """Current level, counting the recharge since the last activity."""
        now = self.clock()
        idle = 0.0 if self._last_activity is None else max(0.0, now - self._last_activity - ACTIVE_GAP)
        level = min(1.0, self._level + idle * self.charge)
        if self._asleep and level >= self.wake_at:
            self._asleep, self._level, self._last_activity = False, level, now
            self._log("woke_up")
        return level

    @property
    def asleep(self) -> bool:
        self.level()  # may wake him up
        return self._asleep

    def state(self) -> str:
        if self.asleep:
            return "asleep"
        lv = self.level()
        return "very_tired" if lv < VERY_TIRED else "tired" if lv < TIRED else "normal"

    def snapshot(self) -> dict:
        return {"level": round(self.level(), 3), "state": self.state()}

    # ---------------------------------------------------------------- events
    def activity(self):
        """Someone spoke (child or the pet)."""
        if self._asleep:
            return  # talking to a sleeping pet neither drains nor resets its nap
        now = self.clock()
        if self._last_activity is not None:
            gap = now - self._last_activity
            if gap <= ACTIVE_GAP:
                self._level -= gap * self.drain
            else:
                self._level = min(1.0, self._level + (gap - ACTIVE_GAP) * self.charge)
        self._last_activity = now
        if self._level <= 0:
            if now < self._protect_until:
                self._level = 0.02  # never fall asleep in the middle of a sad moment
            else:
                self._level, self._asleep = 0.0, True
                self._log("fell_asleep")
        self._check_state()
        self._save()

    def protect(self):
        """A sad or comforting moment: postpone the nap."""
        self._protect_until = self.clock() + PROTECT_SECS

    def recharge(self):
        """Parents' button: full energy, awake."""
        self._level, self._asleep, self._last_activity = 1.0, False, self.clock()
        self._log("recharged")
        self._save()

    # ---------------------------------------------------------------- internals
    def _check_state(self):
        s = self.state()
        if s != self._last_state:
            self._last_state = s
            self._log("energy_state", state=s)

    def _log(self, type_: str, **kw):
        logger.info(f"🔋 {type_} ({self._level:.0%})")
        if self.slog:
            self.slog.event(type_, level=round(self._level, 3), **kw)

    def _save(self):
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps({"level": self._level, "asleep": self._asleep, "last_activity": self._last_activity})
            )
        except OSError as e:
            logger.warning(f"Energy state not saved: {e}")

    def _load(self):
        if not self.state_file or not self.state_file.exists():
            return
        try:
            d = json.loads(self.state_file.read_text())
            self._level = float(d.get("level", 1.0))
            self._asleep = bool(d.get("asleep", False))
            self._last_activity = d.get("last_activity")
        except (OSError, ValueError, TypeError):
            pass


class SleepGate(FrameProcessor):
    """Between the memory processor and the LLM: while the pet sleeps, a turn gets a sleepy line
    instead of a model call. Also records activity for the energy counter."""

    def __init__(
        self,
        sleep_reason: Callable[[], str | None],
        energy: Energy | None,
        slog=None,
        nap_lines: list[str] = NAP_LINES,
        night_lines: list[str] = NIGHT_LINES,
        on_hang_up: Callable[[str], Awaitable[None]] | None = None,
        hang_up_reason: Callable[[], str | None] | None = None,
        hang_up_after: float = 8.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._nap_lines, self._night_lines = nap_lines, night_lines
        self._sleep_reason = sleep_reason
        self._energy = energy
        self._slog = slog
        self._last_line = ""
        # Hanging up: once the pet sleeps (or has just said good night, or the child said goodbye),
        # the conversation ends a few seconds after it last spoke — at once after a sleepy « zzz ».
        self._on_hang_up = on_hang_up
        self._hang_up_reason = hang_up_reason or sleep_reason
        self._hang_up_after = hang_up_after
        self._answered_asleep = False
        self._hang_up_task: asyncio.Task | None = None

    def _cancel_hang_up(self):
        if self._hang_up_task and not self._hang_up_task.done():
            self._hang_up_task.cancel()
        self._hang_up_task = None

    async def _hang_up_later(self, delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        reason = self._hang_up_reason()
        if not reason:
            return  # woken up meanwhile (recharged by a parent, morning)
        logger.info(f"📞 Hanging up ({reason})")
        if self._slog:
            self._slog.event("hang_up", reason=reason)
        self._answered_asleep = False
        await self._on_hang_up(reason)

    async def cleanup(self):
        self._cancel_hang_up()
        await super().cleanup()

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (UserStartedSpeakingFrame, BotStartedSpeakingFrame)) and self._energy:
            self._energy.activity()
        if self._on_hang_up:
            if isinstance(frame, (UserStartedSpeakingFrame, BotStartedSpeakingFrame)):
                self._cancel_hang_up()  # someone speaks: decide again when it ends
            elif isinstance(frame, BotStoppedSpeakingFrame) and self._hang_up_reason():
                self._cancel_hang_up()
                delay = 1.0 if self._answered_asleep else self._hang_up_after
                self._hang_up_task = asyncio.create_task(self._hang_up_later(delay))
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            reason = self._sleep_reason()
            if reason:
                lines = self._night_lines if reason == "night" else self._nap_lines
                line = random.choice([x for x in lines if x != self._last_line] or lines)
                self._last_line = line
                logger.info(f"😴 Asleep ({reason}): sleepy reply")
                if self._slog:
                    self._slog.event("sleepy_reply", reason=reason, text=line)
                self._answered_asleep = True
                await self.push_frame(TTSSpeakFrame(line, append_to_context=False))
                return
        await self.push_frame(frame, direction)
