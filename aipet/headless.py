"""Local / headless mode: the machine's own microphone and speaker (Mac or Raspberry Pi).

This is the "product" mode: on the Pi it runs as a systemd service, with no screen.

Run: uv run local.py               (with wake word if [wake].enabled)
     uv run local.py --no-wake     (always listening, greets at startup)
"""

import argparse
import asyncio

from loguru import logger
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

from aipet import character, env, migrate, session_log
from aipet.config import load_config
from aipet.session import Session
from aipet.wake import WakeWordGate


class HeadlessSession(Session):
    """The pet on the machine's own microphone and speaker. With a wake word, one session log = one
    wake-up, until it goes back to sleep. Hanging up = back to waiting for the wake word (it answers
    « zzz » if woken at night)."""

    mode = "headless"

    def __init__(self, transport, wake_cfg: dict | None, **build_kwargs):
        w = wake_cfg
        self.gate: WakeWordGate | None = (
            WakeWordGate(
                model=w.get("model", "hey_jarvis"),
                threshold=w.get("threshold", 0.5),
                idle_timeout=w.get("sleep_after_secs", 8),
                ack=w.get("response", "sound"),
                max_awake=w.get("max_awake_secs", 120),
                on_wake=lambda: self.open(
                    greet=False, wake_score=round(self.gate.last_score, 3) if self.gate else None
                ),
                on_sleep=self.pause,
            )
            if w
            else None
        )
        super().__init__(transport, gate=self.gate, **build_kwargs)

    async def hang_up(self, reason: str):
        if self.gate and self.gate.awake:
            self.gate.sleep()


async def main(use_wake: bool, input_device: int | None, output_device: int | None):
    cfg = load_config()
    a, w = cfg.get("audio", {}), cfg.get("wake", {})
    logs = cfg.get("logs", {})
    session_log.setup_file_logging(logs.get("text_keep_days", 14))
    session_log.purge(logs.get("keep_days", 30))
    use_wake = use_wake and w.get("enabled", False)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=input_device,
            output_device_index=output_device,
        )
    )
    # Microphone without automatic gain: low volume threshold so speech is detected.
    session = HeadlessSession(
        transport,
        w if use_wake else None,
        # On the Pi the Jabra cancels echo: PET_DEAF=0 (systemd service) lets kids interrupt it.
        mute_while_speaking=env.get("DEAF", str(a.get("deaf_while_speaking", True))).lower()
        not in ("0", "false", "no"),
        min_volume=a.get("mic_min_volume", 0.2),
    )
    if not use_wake:
        await session.open()  # always listening: one session, greeting at startup

    runner = WorkerRunner()
    await runner.add_workers(session.worker)
    logger.info(
        f"{character.of(cfg).name} {'is waiting for its wake word' if use_wake else 'is listening'}… (Ctrl+C to quit)"
    )
    try:
        await runner.run()
    finally:
        await session.close("shutdown", condensation="shutdown")


def run():
    """Entry point of the `pet-headless` command."""
    p = argparse.ArgumentParser(description="The pet on a Raspberry Pi (microphone + speaker + wake word).")
    p.add_argument("--no-wake", action="store_true", help="disable the wake word")
    p.add_argument("--mic", type=int, default=None, help="PyAudio index of the microphone")
    p.add_argument("--speaker", type=int, default=None, help="PyAudio index of the speaker")
    args = p.parse_args()
    migrate.migrate_legacy()  # data from before character packs (~/.flocon), once
    asyncio.run(main(not args.no_wake, args.mic, args.speaker))


if __name__ == "__main__":
    run()
