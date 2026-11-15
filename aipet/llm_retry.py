"""Retry a streamed chat completion that sends no first chunk in time (OpenAI-compatible APIs).

Pipecat's `retry_on_timeout` only bounds the request until the HTTP headers come back. OpenRouter
sends them right away (then keep-alive comments while the model thinks), so a model that takes
8 to 17 s before its first word was never retried. Here the first chunk itself is awaited with the
same timeout; on timeout the stream is closed and the request sent once more (without a limit,
like Pipecat's own retry)."""

from __future__ import annotations

import asyncio
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


class RetryingOpenRouterLLMService(FirstChunkRetry, OpenRouterLLMService):
    pass


class RetryingOpenAILLMService(FirstChunkRetry, OpenAILLMService):
    pass
