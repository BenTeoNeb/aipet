"""Retries inside the LLM service.

`EmptyReplyRetry` (every provider): after a tool result, the model sometimes says nothing at all (the
end of a story was never told). The service knows for sure whether a reply had words or a tool call,
so it asks once more, within the same response: downstream never sees an empty reply. (A processor
after the LLM used to guess this from frames, and a reply that was only a tool call, announced after
the end of the response, looked empty: the ending of an adventure was told twice, 21/11/2026.)

`FirstChunkRetry`: retry a streamed chat completion that sends no first chunk in time (OpenAI-compatible APIs).

Pipecat's `retry_on_timeout` only bounds the request until the HTTP headers come back. OpenRouter
sends them right away (then keep-alive comments while the model thinks), so a model that takes
8 to 17 s before its first word was never retried. Here the first chunk itself is awaited with the
same timeout; on timeout the stream is closed and the request sent once more (without a limit,
like Pipecat's own retry)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService


class _Peeked:
    """A chunk stream whose first chunk was already read."""

    def __init__(self, first, iterator, stream):
        self._first, self._iterator, self._stream = first, iterator, stream

    def __aiter__(self):
        return self._chunks()

    async def _chunks(self):
        if self._first is not None:
            yield self._first
        async for chunk in self._iterator:
            yield chunk

    async def close(self):
        await _close(self._stream)


async def _close(stream) -> None:
    try:
        if hasattr(stream, "close"):
            await stream.close()
        elif hasattr(stream, "aclose"):
            await stream.aclose()
    except Exception:  # closing an abandoned request: nothing to do about it
        logger.debug("Abandoned completion stream did not close cleanly")


class FirstChunkRetry:
    """Mixin for Pipecat's OpenAI-compatible LLM services."""

    async def get_chat_completions(self, context) -> Any:
        stream = await super().get_chat_completions(context)  # type: ignore[misc]
        me: Any = self
        if not me._retry_on_timeout:
            return stream
        iterator = stream.__aiter__()
        try:
            first = await asyncio.wait_for(iterator.__anext__(), timeout=me._retry_timeout_secs)
        except TimeoutError:
            logger.info(f"No first word after {me._retry_timeout_secs:.0f} s: asking the model again")
            await _close(stream)
            return await super().get_chat_completions(context)  # type: ignore[misc]
        except StopAsyncIteration:
            first = None
        return _Peeked(first, iterator, stream)


def _after_tool(context) -> bool:
    """This model call follows a tool result (instructions added since do not count)."""
    for m in reversed(context.get_messages()):
        role = m.get("role") if isinstance(m, dict) else None
        if role in ("system", "developer"):
            continue
        return role == "tool"
    return False


class EmptyReplyRetry:
    """Mixin for Pipecat's LLM services: an empty reply after a tool is asked once more."""

    # Model-facing text (French content).
    NOTE = (
        "⟦système : après l'outil, tu n'as rien dit. Continue maintenant : raconte la suite (ou la fin de "
        "l'histoire) et pose ta question à l'enfant. Si tu avais déjà tout dit et que tu attends sa réponse, "
        "réponds seulement [silence].⟧"
    )
    # Set by the pipeline: the pet said goodbye or sleeps (its silence is wanted); the session log.
    empty_reply_skip: Callable[[], bool] = staticmethod(lambda: False)
    empty_reply_slog: Any = None
    _replied = False

    async def _push_llm_text(self, text: str):
        if text.strip():
            self._replied = True
        await super()._push_llm_text(text)  # type: ignore[misc]

    async def run_function_calls(self, function_calls):
        if function_calls:
            self._replied = True
        await super().run_function_calls(function_calls)

    async def _process_context(self, context):
        self._replied = False
        await super()._process_context(context)
        if self._replied or not _after_tool(context) or self.empty_reply_skip():
            return
        logger.info("🔁 Empty reply after a tool: asking the model again")
        if self.empty_reply_slog:
            self.empty_reply_slog.event("empty_reply_retry")
        context.add_message({"role": "user", "content": self.NOTE})
        await super()._process_context(context)  # once: never a loop


_RETRYING: dict[type, type] = {}


def with_empty_reply_retry(cls: type) -> type:
    """`cls` (a Pipecat LLM service) that asks an empty reply after a tool again."""
    if cls not in _RETRYING:
        _RETRYING[cls] = type(cls.__name__, (EmptyReplyRetry, cls), {})
    return _RETRYING[cls]


class RetryingOpenRouterLLMService(EmptyReplyRetry, FirstChunkRetry, OpenRouterLLMService):
    pass


class RetryingOpenAILLMService(EmptyReplyRetry, FirstChunkRetry, OpenAILLMService):
    pass
