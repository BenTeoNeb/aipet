"""OpenRouter's text-to-speech (OpenAI-compatible endpoint), with one voice per emotion."""

from __future__ import annotations

from openai import BadRequestError
from pipecat.frames.frames import ErrorFrame, LLMFullResponseStartFrame, TTSAudioRawFrame, TTSSpeakFrame
from pipecat.services.openai.tts import OpenAITTSService

from aipet.emotions import RESP_KEY, EmotionFrame, letter_count


class OpenRouterTTSService(OpenAITTSService):
    """OpenAI-compatible speech endpoint of OpenRouter (e.g. Voxtral). Pipecat's OpenAI TTS only
    accepts OpenAI voice names, so the request is rebuilt here with the provider's own voice.

    `emotion_voices`: the pet's emotion → voice, e.g. joie → fr_marie_happy. Each sentence is
    spoken with the voice of the emotion in force where it starts (same character offsets as the
    face animation); a new reply starts with `base_voice`. Canned lines (TTSSpeakFrame: fillers,
    sleepy lines) always use `base_voice`."""

    def __init__(self, *, base_voice: str, emotion_voices: dict[str, str] | None = None, **kw):
        super().__init__(**kw)
        self.base_voice, self.emotion_voices = base_voice, dict(emotion_voices or {})
        self._emotions: dict[int, list[tuple[int, str]]] = {}  # reply → [(offset, emotion)]
        self._reply, self._spoken, self._speaking_line = 0, 0, False

    def voice_for(self, text: str) -> str:
        """Voice for the next sentence of the current reply (and count its characters)."""
        if self._speaking_line or not self.emotion_voices:  # filler / canned line: base voice
            return self.base_voice
        start, end = self._spoken, self._spoken + letter_count(text)
        self._spoken = end
        current = [e for off, e in self._emotions.get(self._reply, []) if off < end or off == start]
        return self.emotion_voices.get(current[-1], self.base_voice) if current else self.base_voice

    async def process_frame(self, frame, direction):
        if isinstance(frame, EmotionFrame):
            self._emotions.setdefault(frame.response, []).append((frame.offset, frame.emotion))
            for old in [k for k in self._emotions if k < frame.response - 2]:
                del self._emotions[old]
        elif isinstance(frame, LLMFullResponseStartFrame) and RESP_KEY in frame.metadata:
            self._reply, self._spoken = frame.metadata[RESP_KEY], 0
        if isinstance(frame, TTSSpeakFrame):
            self._speaking_line = True
            try:
                await super().process_frame(frame, direction)
            finally:
                self._speaking_line = False
            return
        await super().process_frame(frame, direction)

    async def run_tts(self, text: str, context_id: str):
        params = {"input": text, "model": self._settings.model, "voice": self.voice_for(text)}
        params["response_format"] = "pcm"  # raw 16-bit mono at 24 kHz
        if self._settings.speed and self._settings.speed != 1.0:
            params["speed"] = self._settings.speed
        try:
            async with self._client.audio.speech.with_streaming_response.create(**params) as resp:
                if resp.status_code != 200:
                    yield ErrorFrame(error=f"TTS error {resp.status_code}: {await resp.text()}")
                    return
                await self.start_tts_usage_metrics(text)
                async for chunk in resp.iter_bytes(self.chunk_size):
                    if chunk:
                        await self.stop_ttfb_metrics()
                        yield TTSAudioRawFrame(chunk, self.sample_rate, 1, context_id=context_id)
        except BadRequestError as e:
            yield ErrorFrame(error=f"TTS error: {e}")
