"""Background asyncio tasks that are neither lost nor silent.

asyncio keeps only weak references to tasks: a task nobody holds can be garbage-collected mid-run,
and its exception only shows up later as « Task exception was never retrieved ». `BackgroundTasks`
holds them until they finish, logs failures, and cancels what is left on `cancel_all()`.
`start()` is for timers whose owner keeps the reference itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from loguru import logger


def _log_failure(task: asyncio.Task):
    if not task.cancelled() and task.exception():
        logger.opt(exception=task.exception()).error(f"Background task « {task.get_name()} » failed")


def start(coro: Coroutine, name: str) -> asyncio.Task:
    """A task whose failure is logged; the caller keeps the reference (a timer it may cancel)."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    task.add_done_callback(_log_failure)
    return task


class BackgroundTasks:
    def __init__(self):
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine, name: str) -> asyncio.Task:
        task = start(coro, name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel_all(self):
        for task in list(self._tasks):
            task.cancel()

    def __len__(self) -> int:
        return len(self._tasks)
