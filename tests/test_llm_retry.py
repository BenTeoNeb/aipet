"""The model's first word is awaited with a timeout (OpenRouter answers the headers at once)."""

import asyncio

from aipet.llm_retry import FirstChunkRetry


class Stream:
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        await asyncio.sleep(self.delay)
        for c in self.chunks:
            yield c

    async def close(self):
        self.closed = True


class Base:
    def __init__(self, streams):
        self.streams, self.calls = streams, 0
        self._retry_on_timeout, self._retry_timeout_secs = True, 0.05

    async def get_chat_completions(self, context):
        self.calls += 1
        return self.streams.pop(0)


class Service(FirstChunkRetry, Base):
    pass


async def collect(stream):
    return [c async for c in stream]


def test_a_silent_stream_is_asked_again():
    slow, fast = Stream(["late"], delay=1), Stream(["Bonjour", " !"])
    s = Service([slow, fast])
    out = asyncio.run(asyncio.wait_for(s.get_chat_completions(None), 0.5))
    assert s.calls == 2 and slow.closed and out is fast


def test_a_quick_stream_keeps_its_first_chunk():
    s = Service([Stream(["Bon", "jour"])])

    async def run():
        return await collect(await s.get_chat_completions(None))

    assert asyncio.run(run()) == ["Bon", "jour"] and s.calls == 1


def test_no_retry_when_disabled():
    stream = Stream(["x"], delay=0.1)
    s = Service([stream])
    s._retry_on_timeout = False
    assert asyncio.run(s.get_chat_completions(None)) is stream
