"""MemoryKeeper: condenses the conversation into long-term memory as it goes, and keeps the context
short, so the pet can run indefinitely.

Condensation runs after a pause (`arm_pause`), every few child messages (`note_child_message`), when
the pet goes back to sleep and at the end of a session. Condensed messages are then trimmed from
the context (they are in memory). After repeated failures the raw transcript is kept instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from aipet.memory.condensation import condense, turns_from_messages
from aipet.memory.store import Memory
from aipet.session_log import NO_LOG
from aipet.tasks import start
from aipet.timefmt import now

MAX_CONDENSE_FAILURES = 3  # then the raw transcript is kept and the context moves on


async def _nothing():
    pass


class MemoryKeeper:
    def __init__(
        self,
        mem: Memory,
        cfg: dict,
        context,
        *,
        pause_secs: float = 25,
        max_turns: int = 8,
        keep: int = 12,
        allowed_names: Callable[[], set[str]] = set,
        on_condensed: Callable[[], Awaitable[None]] = _nothing,
        slog=None,
    ):
        self.mem, self.cfg, self.context = mem, cfg, context
        self.pause_secs, self.max_turns, self.keep = pause_secs, max_turns, keep
        self.allowed_names = allowed_names  # names a new memory card may be created for
        self.on_condensed = on_condensed  # e.g. refresh the system prompt (it shows the memory)
        self.slog = slog or NO_LOG
        self.done = 0  # messages already condensed (index in the context)
        self.segment_start: datetime | None = None  # time of the first non-condensed message
        self.last: dict[str, Any] = {"when": None, "reason": None, "changes": [], "error": None}
        self._failures = 0  # condensation failures in a row (backoff)
        self._retry_after: datetime | None = None  # condensation failures (backoff)
        self._timer: asyncio.Task | None = None
        self._running: asyncio.Task | None = None
        self._ck_lock = asyncio.Lock()
        self._generation = 0  # changes when the context is cleared (reset)

    # --- triggers --------------------------------------------------------
    def note_child_message(self):
        """A child message was added to the context: condense every `max_turns` of them."""
        if self.segment_start is None:
            self.segment_start = now()
        if self._new_turns() >= self.max_turns:
            self.trigger("turns")

    def _new_turns(self) -> int:
        return sum(
            1 for m in self.context.get_messages()[self.done :] if isinstance(m, dict) and m.get("role") == "user"
        )

    def arm_pause(self):
        self.cancel_pause()
        self._timer = start(self._pause_then_checkpoint(), "condense after a pause")

    def cancel_pause(self):
        if self._timer and not self._timer.done():
            self._timer.cancel()

    async def _pause_then_checkpoint(self):
        try:
            await asyncio.sleep(self.pause_secs)
            self.trigger("pause")
        except asyncio.CancelledError:
            pass

    def trigger(self, reason: str):
        if self._running and not self._running.done():
            return
        if self._retry_after and now() < self._retry_after and reason not in ("manual", "sleep", "shutdown"):
            return  # the last condensation failed: back off
        self._running = start(self.checkpoint(reason), "condensation")

    async def checkpoint(self, reason: str = "manual", trim: bool = True) -> list[str]:
        async with self._ck_lock:  # never two condensations of the same chunk at once
            msgs = self.context.get_messages()
            start, end, gen = self.done, len(msgs), self._generation
            turns = turns_from_messages(msgs[start:end])
            if not any(t["role"] == "user" for t in turns):
                return []
            when = self.segment_start or now()
            logger.info(f"🧠 Condensation ({reason}): {len(turns)} message(s)")
            t_ck = now()
            try:
                changed = await condense(self.mem, self.cfg, turns, when, allowed_new_names=self.allowed_names())
                self._failures, self._retry_after = 0, None
                self.last = {"when": f"{now():%H:%M:%S}", "reason": reason, "changes": changed, "error": None}
                self.slog.event(
                    "condensation",
                    reason=reason,
                    messages=len(turns),
                    files=changed,
                    duration_s=round((now() - t_ck).total_seconds(), 1),
                )
            except Exception as e:
                logger.exception("Condensation failed")
                self.slog.event("condensation_error", reason=reason, error=f"{type(e).__name__}: {e}")
                self.last = {
                    "when": f"{now():%H:%M:%S}",
                    "reason": reason,
                    "changes": [],
                    "error": f"{type(e).__name__}: {e}",
                }
                self._failures += 1
                if self._failures < MAX_CONDENSE_FAILURES:  # wait before retrying (not at every message)
                    self._retry_after = now() + timedelta(minutes=2**self._failures)
                    return []
                # Repeated failures: keep the raw transcript, move on, and let the context shrink.
                logger.warning(f"Condensation given up after {self._failures} failures: raw transcript kept")
                await asyncio.to_thread(self.mem.save_transcript, when, turns)
                self.slog.event("condensation_given_up", messages=len(turns))
                self._failures, self._retry_after = 0, None
                changed = []
            if gen == self._generation:  # the context was not cleared meanwhile
                self.done = end
                self.segment_start = None
                if trim:
                    self._trim()
            await self.on_condensed()
            return changed

    def _trim(self, keep: int | None = None):
        """Drop the oldest already-condensed messages (they are in memory), always starting
        the context on a real user message."""
        keep = self.keep if keep is None else keep
        msgs = self.context.get_messages()
        limit = min(len(msgs) - keep, self.done)  # never beyond what is condensed
        if limit <= 0:
            return
        cut = None
        for i in range(limit, 0, -1):  # the latest cut point that lands on a user message
            m = msgs[i] if i < len(msgs) else None
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), (str, list)):
                cut = i
                break
        if limit >= len(msgs):
            cut = len(msgs)  # everything is condensed: everything can go
        if not cut:
            return
        self.context.set_messages(msgs[cut:])
        self.done -= cut
        logger.info(f"✂️  Context trimmed: {cut} old message(s) removed")

    def forget_condensed(self):
        """After a long pause: drop everything already condensed (not during a condensation: its
        indices would be wrong)."""
        if self.done and not self._ck_lock.locked():
            self._trim(keep=0)

    def reset(self):
        """The context was cleared: nothing left to condense (a running condensation is discarded)."""
        self._generation += 1
        self.done, self.segment_start = 0, None
