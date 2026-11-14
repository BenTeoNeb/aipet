"""Wake word (openWakeWord) for headless mode.

The gate sits right after the microphone. While the assistant sleeps, audio only
goes to the local detector: nothing is sent to the cloud STT. Once awake, audio
flows normally; it goes back to sleep after `sleep_after_secs` seconds of silence
once the assistant has finished speaking.
"""

import asyncio
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

CHUNK = 1280  # 80 ms at 16 kHz, the size openWakeWord expects


def load_wake_model(name: str):
    """Load a built-in model (e.g. "hey_jarvis") or a custom .onnx file."""
    from openwakeword import utils
    from openwakeword.model import Model

    if Path(name).suffix == ".onnx":
        return Model(wakeword_models=[name], inference_framework="onnx")
    utils.download_models([name])  # no-op if already present
    return Model(wakeword_models=[name], inference_framework="onnx")


TOOL_WAIT_SECS = 60  # max wait for a running tool before going back to sleep


class WakeWordGate(FrameProcessor):
    def __init__(
        self,
        *,
        model: str,
        threshold: float,
        idle_timeout: float,
        ack: str = "sound",
        max_awake: float = 120,
        on_sleep=None,
        on_wake=None,
    ):
        super().__init__()
        self._oww = load_wake_model(model)
        self._threshold = threshold
        self._idle_timeout = idle_timeout
        self._ack = ack.strip()
        self._on_sleep = on_sleep
        self._on_wake = on_wake
        # Safety net: if nobody speaks for max_awake seconds, go back to sleep no matter
        # what (even if speech synthesis crashed and never finished).
        self._max_awake = max_awake
        self._watchdog: asyncio.Task | None = None
        self._out_rate = 24000
        self._pending = False  # the child has finished speaking, the reply has not started yet
        self._tools: set[str] = set()  # tool calls in progress (a story plan can take 10+ s)
        self._last_error_sound = 0.0
        self._last_watchdog_arm = 0.0
        self._awake = False
        self._buf = np.zeros(0, dtype=np.int16)
        self._idle_task: asyncio.Task | None = None
        logger.info(f"Wake word ready ({model}, threshold {threshold}) — asleep")

    @property
    def awake(self) -> bool:
        return self._awake

    # --- sleep / wake ----------------------------------------------------
    async def _wake(self, score: float):
        logger.info(f"Awake! (score {score:.2f})")
        self.last_score = score
        self._awake = True
        self._buf = np.zeros(0, dtype=np.int16)
        # Timers first: whatever happens next, the pet will eventually go back to sleep.
        self._arm_idle()
        self._arm_watchdog()
        if self._on_wake:
            try:
                await self._on_wake()
            except Exception:
                logger.exception("on_wake failed (continuing)")
        if self._ack == "sound":
            await self.push_frame(OutputAudioRawFrame(audio=self._chime(), sample_rate=self._out_rate, num_channels=1))
        elif self._ack:
            await self.push_frame(TTSSpeakFrame(self._ack))

    def _chime(self, falling: bool = False) -> bytes:
        """Short 0.18 s "bloop" (rising = awake, falling = "oops, something went wrong")."""
        sr = self._out_rate
        t = np.arange(int(sr * 0.18)) / sr
        freq = np.where(t < 0.08, 990, 520) if falling else np.where(t < 0.08, 660, 990)
        env = np.minimum(1, t / 0.01) * np.exp(-t * 14)
        return (0.25 * 32767 * env * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()

    def _sleep(self):
        logger.info("Back to sleep")
        self._awake = False
        self._pending = False
        self._tools.clear()
        self._oww.reset()
        self._cancel_idle()
        if self._watchdog and not self._watchdog.done():
            self._watchdog.cancel()
        if self._on_sleep:
            self._on_sleep()

    def _arm_idle(self):
        self._cancel_idle()
        self._idle_task = asyncio.create_task(self._idle_countdown())

    def _cancel_idle(self):
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    def _arm_watchdog(self):
        if self._watchdog and not self._watchdog.done():
            self._watchdog.cancel()
        self._watchdog = asyncio.create_task(self._watchdog_countdown())

    async def _watchdog_countdown(self):
        try:
            await asyncio.sleep(self._max_awake)
            logger.warning(f"Nobody has spoken for {self._max_awake:.0f}s: forcing sleep")
            self._sleep()
        except asyncio.CancelledError:
            pass

    async def _idle_countdown(self):
        try:
            await asyncio.sleep(self._idle_timeout)
            waited = 0
            # A reply is being prepared (LLM, tools): wait a little. Kept short, because the pet
            # may also choose to say nothing (noise), in which case no reply will come.
            while (self._pending and waited < 12) or (self._tools and waited < TOOL_WAIT_SECS):
                await asyncio.sleep(1)
                waited += 1
            self._sleep()
        except asyncio.CancelledError:
            pass

    # --- detection -------------------------------------------------------
    def _detect(self, frame: InputAudioRawFrame) -> float:
        pcm = np.frombuffer(frame.audio, dtype=np.int16)
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels)[:, 0]
        if frame.sample_rate != 16000:
            n = int(len(pcm) * 16000 / frame.sample_rate)
            pcm = np.interp(np.linspace(0, len(pcm), n, endpoint=False), np.arange(len(pcm)), pcm).astype(np.int16)
        self._buf = np.concatenate([self._buf, pcm])
        best = 0.0
        while len(self._buf) >= CHUNK:
            chunk, self._buf = self._buf[:CHUNK], self._buf[CHUNK:]
            scores = self._oww.predict(chunk)
            best = max(best, max(scores.values()))
        return best

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._awake:
                await self.push_frame(frame, direction)
                return
            score = self._detect(frame)
            if score >= self._threshold:
                await self._wake(score)
            return  # asleep: audio goes no further

        if isinstance(frame, StartFrame):
            self._out_rate = frame.audio_out_sample_rate or self._out_rate

        if self._awake:
            if isinstance(frame, FunctionCallInProgressFrame):
                self._tools.add(frame.tool_call_id)  # broadcast both ways: a set, not a counter
                self._cancel_idle()
            elif isinstance(frame, FunctionCallResultFrame):
                if frame.tool_call_id in self._tools:
                    self._tools.discard(frame.tool_call_id)
                    self._pending = True  # the model now writes the reply
                    self._arm_idle()
            elif isinstance(frame, BotSpeakingFrame):
                # Emitted continuously while the pet speaks: one long uninterrupted story must not
                # trigger the safety net (re-armed at most once per second).
                t = asyncio.get_running_loop().time()
                if t - self._last_watchdog_arm > 1:
                    self._last_watchdog_arm = t
                    self._arm_watchdog()
                    self._pending = False
            elif isinstance(frame, (UserStartedSpeakingFrame, BotStartedSpeakingFrame)):
                self._cancel_idle()
                # Someone is speaking (the child or the pet, e.g. a long story): restart the safety net.
                self._arm_watchdog()
                if isinstance(frame, BotStartedSpeakingFrame):
                    self._pending = False
            elif isinstance(frame, (UserStoppedSpeakingFrame, BotStoppedSpeakingFrame)):
                self._arm_idle()
                self._arm_watchdog()
                self._pending = isinstance(frame, UserStoppedSpeakingFrame)
            elif isinstance(frame, ErrorFrame) and direction == FrameDirection.UPSTREAM:
                # A service failed (network, provider…): a short sound rather than a puzzling silence.
                self._pending = False
                loop_t = asyncio.get_running_loop().time()
                if loop_t - self._last_error_sound > 5:
                    self._last_error_sound = loop_t
                    logger.warning(f"Service error: {frame.error}")
                    await self.push_frame(
                        OutputAudioRawFrame(audio=self._chime(falling=True), sample_rate=self._out_rate, num_channels=1)
                    )

        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._cancel_idle()
        if self._watchdog and not self._watchdog.done():
            self._watchdog.cancel()
        await super().cleanup()
