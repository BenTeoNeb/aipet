"""Helpers shared by the pet's games: who is playing, their school level, streaks of days."""

from __future__ import annotations

import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from aipet import character, school
from aipet.memory.store import _slug, _without_article, normalize_text, parse_card

UNKNOWN_PLAYER = "famille"  # games played while the pet doesn't know (for sure) who is talking
GAMES_DIR = "jeux"  # in the family memory (data)


def current_player(keeper) -> str:
    who = getattr(keeper, "who", None)
    if who and who.current and who.certainty == "sure":
        return who.current
    return UNKNOWN_PLAYER


def theme_key(theme: str) -> str:
    """Same theme whatever the wording: « Les animaux » = « animaux », « l'espace » = « espace »."""
    t = normalize_text(theme).strip()
    t = re.sub(r"^(les |le |la |l'|l’|un |une |des |du |de la |de l'|d'|d’)", "", t)
    return _without_article(t).strip()


def days_in_a_row(play_days: list[str], today: date) -> int:
    """Consecutive days played, ending today or yesterday."""
    days = {date.fromisoformat(d) for d in play_days}
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def child_level(memory_root: Path, name: str, today: date) -> str | None:
    """School level of a known child (from their card), or None."""
    card = Path(memory_root) / "famille" / f"{_slug(name)}.md"
    if not card.exists():
        return None
    meta = parse_card(card.read_text())[0]

    def noted(key):
        try:
            return date.fromisoformat(meta.get(key, ""))
        except ValueError:
            return None

    level = school.current_level(meta.get("classe"), noted("classe_note_le"), today)
    if level:
        return level
    return school.typical_level(school.current_age(school.parse_age(meta.get("age")), noted("age_note_le"), today))


class JsonStore:
    """A small JSON file in the family memory, written atomically."""

    def __init__(self, path: Path, empty: dict):
        self.path, self._empty = Path(path), empty
        self.lock = threading.Lock()

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return json.loads(json.dumps(self._empty))

    def save(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)


def pet_intro(cfg: dict) -> str:
    """How preparation prompts name the pet (French): « Flocon, un petit chat »."""
    pet = character.of(cfg)
    return f"{pet.name}, {pet.intro}"
