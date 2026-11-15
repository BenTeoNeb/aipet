"""The household's clock and how dates are said in French (the pets speak French)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]


def now() -> datetime:
    return datetime.now(TZ)


def spoken_date(d: datetime) -> str:
    """Date and time as said in French, e.g. « mercredi 11 novembre 2026, 18h02 »."""
    return f"{WEEKDAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]} {d.year}, {d.hour}h{d.minute:02d}"
