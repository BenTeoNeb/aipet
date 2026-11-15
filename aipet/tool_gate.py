"""Which tools the model receives, and how many it may call per turn.

Every tool is registered and checked at startup; the model only receives the base tools plus those
of the running activity (declared in its SKILL.md), to keep each request light and choices clear.
At most MAX_TOOLS_PER_TURN calls per child message: avoids slow loops.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from aipet.session_log import NO_LOG

MAX_TOOLS_PER_TURN = 4
# Model-facing (French on purpose).
TOOLS_EXHAUSTED = {"error": "Tu as déjà beaucoup cherché pour ce tour : réponds maintenant avec ce que tu sais."}


def tool_handler(
    call: Callable[[dict], Any | Awaitable[Any]], budget_exhausted: Callable[[], bool] | None = None
) -> Callable:
    """A Pipecat function handler from `call(arguments) -> result` (sync or async). With
    `budget_exhausted`, each call counts against the per-turn tool budget and is refused past it.
    (A handler that raises is answered with an error by Pipecat itself.)"""

    async def handler(params):
        if budget_exhausted and budget_exhausted():
            await params.result_callback(TOOLS_EXHAUSTED)
            return
        result = call(params.arguments)
        if inspect.isawaitable(result):
            result = await result
        await params.result_callback(result)

    return handler


class ToolGate:
    def __init__(self, context, slog=None):
        self.context = context  # the LLM context, which carries the tools sent to the model
        self.slog = slog or NO_LOG
        self.schemas: dict = {}  # every tool, by name (FunctionSchema)
        self.base: set[str] = set()  # always sent
        self.calls = 0  # during the current turn

    def register(self, schemas: list, base: set[str]):
        self.schemas = {t.name: t for t in schemas}
        self.base = set(base)
        self.apply(())

    def apply(self, activity_tools) -> list[str]:
        """Send the model the base tools plus the running activity's (only existing tools)."""
        if not self.schemas:
            return []
        from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

        unknown = [t for t in activity_tools if t not in self.schemas]
        if unknown:
            logger.warning(f"Activity names unknown tools, ignored: {unknown}")
        names = [n for n in self.schemas if n in self.base or n in activity_tools]
        self.context.set_tools(ToolsSchema(standard_tools=[self.schemas[n] for n in names]))
        logger.info(f"🧰 Tools sent to the model: {len(names)} ({', '.join(sorted(set(names) - self.base)) or 'base'})")
        return names

    def new_turn(self):
        self.calls = 0

    def count_call(self) -> bool:
        """Counts a tool call; True when this turn already used its budget (the call is refused)."""
        self.calls += 1
        if self.calls > MAX_TOOLS_PER_TURN:
            logger.warning(f"Tool budget exhausted ({self.calls - 1} calls this turn)")
            self.slog.event("tool_budget_exhausted", calls=self.calls - 1)
            return True
        return False
