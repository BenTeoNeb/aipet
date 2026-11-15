"""When each child last talked with the pet (`presence.json` in its memory folder, not in git), so the
pet can say « ça fait longtemps ! » — and never reproach it."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from aipet import rhythm
from aipet.fileio import atomic_write_json, load_json, lock_for
from aipet.memory.store import now

ANYONE = "_any"


class Presence:
    def __init__(self, memory_root: Path):
        self.file = Path(memory_root) / "presence.json"
        self.before: dict[str, str] = self._load()  # as it was when this conversation started

    def _load(self) -> dict[str, str]:
        data = load_json(self.file, {})
        return data if isinstance(data, dict) else {}

    def new_conversation(self):
        self.before = self._load()

    def note(self, name: str | None):
        """Someone talks now (a child known for sure, or None)."""
        stamp = now().isoformat(timespec="minutes")
        with lock_for(self.file):
            data = self._load()
            data[ANYONE] = stamp
            if name:
                data[name] = stamp
            try:
                atomic_write_json(self.file, data, indent=None)
            except OSError as e:
                logger.warning(f"Presence not saved: {e}")

    def lines(self) -> str:
        """French prompt lines: when the pet last talked with anyone, and with each child."""
        seen = self.before
        if not seen.get(ANYONE):
            return "C'est la toute première fois qu'on te parle."
        current = now()
        lines = [
            "La dernière fois qu'on t'a parlé (avant cette conversation) : "
            f"{rhythm.ago(datetime.fromisoformat(seen[ANYONE]), current)}."
        ]
        others = [
            f"{name} : {rhythm.ago(datetime.fromisoformat(t), current)}"
            for name, t in sorted(seen.items())
            if name != ANYONE
        ]
        if others:
            lines.append("Dernière fois avec chacun : " + " ; ".join(others) + ".")
        lines.append(
            "Si ça fait longtemps, sois content de retrouver l'enfant et reprends le fil, sans jamais le lui reprocher."
        )
        return "\n".join(lines)
