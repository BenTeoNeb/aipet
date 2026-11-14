"""One conversation with the pet, from start to end: the lifecycle shared by the web page and the Pi.

- `open()`: a new session log, the keeper wakes (current time, « last time we talked », a fresh
  context after a long pause), then the pet greets (unless woken by its wake word: it chimes).
- `pause()` (Pi only: back to waiting for the wake word): the conversation is condensed in the
  background, then its log is closed, unless the pet was woken up again meanwhile. Activities survive
  a short sleep (a child thinking over a quiz question); the keeper ends them after a long pause.
- `close()`: once, at the end — condense what was said, close the log.
- `hang_up()`: the pet ends the conversation by itself (asleep, « bonne nuit », goodbye); what it
  means depends on the mode, so subclasses define it.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import LLMRunFrame

from aipet import session_log
from aipet.config import config_snapshot, load_config
from aipet.pipeline import build_worker
from aipet.prompts import GREETING


class Session:
    mode = ""

    def __init__(self, transport, **build_kwargs):
        self.cfg = load_config()
        self.log = session_log.SessionLog(self.mode)
        self._closed = False
        self.worker, self.context, self.keeper = build_worker(
            transport, session_log=self.log, on_hang_up=self.hang_up, **build_kwargs
        )

    async def hang_up(self, reason: str):
        raise NotImplementedError

    async def open(self, greet: bool = True, **log_info):
        self._closed = False
        self.log.start(config=config_snapshot(self.cfg), **log_info)
        await self.keeper.on_wake()
        if greet:
            self.context.add_message({"role": "developer", "content": GREETING})
            await self.worker.queue_frames([LLMRunFrame()])

    def pause(self):
        self.log.event("sleep")
        self.keeper.on_sleep()
        current = self.log.path

        async def condense_then_close():
            await self.keeper.checkpoint("sleep")
            if self.log.path == current:  # not woken up again in the meantime
                self.log.end("sleep")

        asyncio.get_running_loop().create_task(condense_then_close())

    async def close(self, reason: str, condensation: str = "end of conversation"):
        if self._closed:
            return
        self._closed = True
        self.keeper.cancel_pause()
        self.keeper.worker = None  # no more system-prompt refreshes: the session is ending
        await self.keeper.checkpoint(condensation, trim=False)
        self.log.end(reason)
