"""Local / headless mode: the machine's own microphone and speaker (Mac or Raspberry Pi).

This is the "product" mode: on the Pi it runs as a systemd service, with no screen.

Run: uv run local.py               (with wake word if [wake].enabled)
     uv run local.py --no-wake     (always listening, greets at startup)
"""

import argparse
import asyncio

from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

from aipet import env, migrate, session_log
from aipet.config import config_snapshot, load_config
from aipet.pipeline import build_worker
from aipet.prompts import GREETING
from aipet.wake import WakeWordGate


async def main(use_wake: bool, input_device: int | None, output_device: int | None):
    cfg = load_config()
    a, w = cfg.get("audio", {}), cfg.get("wake", {})
    logs = cfg.get("logs", {})
    session_log.setup_file_logging(logs.get("text_keep_days", 14))
    session_log.purge(logs.get("keep_days", 30))
    slog = session_log.SessionLog("headless")
    use_wake = use_wake and w.get("enabled", False)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=input_device,
            output_device_index=output_device,
        )
    )
    gate = (
        WakeWordGate(
            model=w.get("model", "hey_jarvis"),
            threshold=w.get("threshold", 0.5),
            idle_timeout=w.get("sleep_after_secs", 8),
            ack=w.get("response", "sound"),
            max_awake=w.get("max_awake_secs", 120),
        )
        if use_wake
        else None
    )
    # Microphone without automatic gain: low volume threshold so speech is detected.
    worker, context, keeper = build_worker(
        transport,
        # On the Pi the Jabra cancels echo: PET_DEAF=0 (systemd service) lets kids interrupt it.
        mute_while_speaking=env.get("DEAF", str(a.get("deaf_while_speaking", True))).lower()
        not in ("0", "false", "no"),
        min_volume=a.get("mic_min_volume", 0.2),
        gate=gate,
        session_log=slog,
    )
    if gate:
        # One session log = one wake-up, until sleep (and the condensation that follows).
        async def on_wake():
            slog.start(wake_score=round(getattr(gate, "last_score", 0), 3), config=config_snapshot(cfg))
            await keeper.on_wake()  # refresh the date, trim the context after a long pause

        def on_sleep():
            slog.event("sleep")
            keeper.who.reset()
            # Activities and games survive a short sleep (a child thinking over a quiz question);
            # keeper.on_wake ends them after a long pause.
            if keeper.wait:
                keeper.wait.cancel()
            current = slog.path

            async def condense_then_close():
                await keeper.checkpoint("sleep")
                if slog.path == current:  # not woken up again in the meantime
                    slog.end("sleep")

            asyncio.create_task(condense_then_close())

        gate._on_wake, gate._on_sleep = on_wake, on_sleep
    else:
        slog.start(config=config_snapshot(cfg))
    if not use_wake:
        context.add_message({"role": "developer", "content": GREETING})
        await worker.queue_frames([LLMRunFrame()])

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"{a['name']} {'is waiting for its wake word' if use_wake else 'is listening'}… (Ctrl+C to quit)")
    try:
        await runner.run()
    finally:
        keeper.worker = None
        await keeper.checkpoint("shutdown", trim=False)
        slog.end("shutdown")


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
