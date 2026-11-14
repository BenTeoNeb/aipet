"""School awareness: French school levels, how they advance over time, and typical landmarks.

A child's age and class are noted with the date they were learned, then advanced automatically
(age on each anniversary of the note, class at each rentrée on September 1st). The landmarks
(`data/school_fr.toml`, French, model-facing) are only injected for the levels of known children.
"""

from __future__ import annotations

import re
import tomllib
from datetime import date
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "school_fr.toml"
LEVELS = ["PS", "MS", "GS", "CP", "CE1", "CE2", "CM1", "CM2", "6e", "5e", "4e", "3e"]
_ALIASES = {
    "petite section": "PS", "moyenne section": "MS", "grande section": "GS",
    "sixieme": "6e", "6eme": "6e", "cinquieme": "5e", "5eme": "5e",
    "quatrieme": "4e", "4eme": "4e", "troisieme": "3e", "3eme": "3e",
}  # fmt: skip


@cache
def landmarks() -> dict[str, dict]:
    with open(DATA, "rb") as f:
        return tomllib.load(f)


def normalize_level(text: str | None) -> str | None:
    """« ce1 », « CE 1 », « en grande section », « 6ème » → canonical level, or None."""
    if not text:
        return None
    import unicodedata

    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t).strip()
    for alias, level in _ALIASES.items():
        if alias in t:
            return level
    compact = t.replace(" ", "").upper()
    for level in sorted(LEVELS, key=len, reverse=True):  # "CE1" before "CE…"
        if level.upper() in compact:
            return level
    return None


def parse_age(text: str | None) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _rentrees_between(noted: date, today: date) -> int:
    """Number of September 1st (school years) strictly after `noted` and up to `today`."""
    first = date(noted.year if noted < date(noted.year, 9, 1) else noted.year + 1, 9, 1)
    if today < first:
        return 0
    return today.year - first.year + (1 if (today.month, today.day) >= (9, 1) else 0)


def current_level(level: str | None, noted: date | None, today: date) -> str | None:
    level = normalize_level(level)
    if not level or noted is None:
        return level
    i = LEVELS.index(level) + _rentrees_between(noted, today)
    return LEVELS[min(i, len(LEVELS) - 1)]


def current_age(age: int | None, noted: date | None, today: date) -> int | None:
    if age is None or noted is None:
        return age
    years = today.year - noted.year - ((today.month, today.day) < (noted.month, noted.day))
    return age + max(years, 0)


def typical_level(age: int | None) -> str | None:
    """The usual level for an age (may be off by one: it depends on the birth month)."""
    for level, info in landmarks().items():
        if info.get("age") == age:
            return level
    return None


def child_summary(meta: dict, today: date) -> str:
    """French line for a child's card, e.g. « Aujourd'hui : environ 8 ans, en CE2. »"""

    def d(key):
        try:
            return date.fromisoformat(meta[key])
        except (KeyError, ValueError):
            return None

    age = current_age(parse_age(meta.get("age")), d("age_note_le"), today)
    level = current_level(meta.get("classe"), d("classe_note_le"), today)
    if age is None and level is None:
        return ""
    parts = []
    if age is not None:
        parts.append(f"environ {age} ans")
    if level:
        parts.append(f"en {level}")
    elif age is not None and typical_level(age):
        parts.append(f"sans doute en {typical_level(age)} (classe à confirmer)")
    return "Aujourd'hui : " + ", ".join(parts) + "."


def landmarks_for(levels: list[str]) -> str:
    """French prompt section with the landmarks of the given levels (deduplicated, in school order)."""
    known = landmarks()
    wanted = [lv for lv in LEVELS if lv in set(levels) and lv in known]
    if not wanted:
        return ""
    lines = "\n".join(f"- {known[lv]['reperes']}" for lv in wanted)
    return (
        "## Ce qu'on apprend à l'école à leur âge\n"
        "Repères approximatifs : chaque classe avance à son rythme, alors demande aussi ce qu'ils font en ce moment.\n"
        + lines
    )
