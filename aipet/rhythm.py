"""The pet's sense of time: part of the day, school days and holidays, bedtime, and "how long ago".

Everything here is computed by code (never guessed by the model) and turned into short French
lines for the prompt. School holidays come from the official French calendar
(data.education.gouv.fr), cached locally and refreshed weekly; without network the cache is used,
and without cache the pet simply does not mention holidays.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from loguru import logger

from aipet import paths
from aipet.timefmt import MONTHS_FR, TZ, WEEKDAYS_FR

CACHE = paths.cache_dir() / "school_holidays.json"  # shared by the household's pets
HOLIDAYS_API = "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-calendrier-scolaire/records"
REFRESH_DAYS = 7


# ---------------------------------------------------------------------------
# Public holidays (jours fériés, metropolitan France)
# ---------------------------------------------------------------------------
def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def public_holiday(d: date) -> str | None:
    """French name of the public holiday on `d`, or None."""
    fixed = {
        (1, 1): "le jour de l'An", (5, 1): "la fête du Travail", (5, 8): "la Victoire de 1945",
        (7, 14): "la fête nationale", (8, 15): "l'Assomption", (11, 1): "la Toussaint",
        (11, 11): "l'Armistice de 1918", (12, 25): "Noël",
    }  # fmt: skip
    if (d.month, d.day) in fixed:
        return fixed[(d.month, d.day)]
    e = easter(d.year)
    moving = {e + timedelta(days=1): "le lundi de Pâques", e + timedelta(days=39): "l'Ascension",
              e + timedelta(days=50): "le lundi de Pentecôte"}  # fmt: skip
    return moving.get(d)


# ---------------------------------------------------------------------------
# School holidays (official calendar, one zone)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Holiday:
    name: str  # French, e.g. "Vacances de Noël"
    start: datetime  # first day off (local midnight)
    end: datetime  # first day back at school (local midnight)


def _parse_holidays(records: list[dict]) -> list[Holiday]:
    seen, out = set(), []
    for r in records:
        try:
            start = datetime.fromisoformat(r["start_date"]).astimezone(TZ)
            end = datetime.fromisoformat(r["end_date"]).astimezone(TZ)
        except (KeyError, TypeError, ValueError):
            continue
        key = (r.get("description"), start.date())
        if key not in seen:  # the dataset repeats each holiday once per académie
            seen.add(key)
            out.append(Holiday(r.get("description") or "Vacances", start, end))
    return sorted(out, key=lambda h: h.start)


def fetch_holidays(zone: str, since: date) -> list[dict]:
    """Official calendar records for a zone, from `since` on (network call; no personal data sent)."""
    where = f'zones="Zone {zone}" and end_date>="{since.isoformat()}"'
    query = urllib.parse.urlencode({"where": where, "limit": 100, "order_by": "start_date"})
    with urllib.request.urlopen(f"{HOLIDAYS_API}?{query}", timeout=10) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp).get("results", [])


def school_holidays(zone: str, today: date, cache: Path = CACHE, fetch: bool = True) -> list[Holiday]:
    """Holidays from the local cache, refreshed from the official calendar at most once a week
    (`fetch=False`: the cache only, never the network — for the event loop)."""
    data = {}
    try:
        data = json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    fresh = data.get("zone") == zone and data.get("fetched", "") >= (today - timedelta(days=REFRESH_DAYS)).isoformat()
    if not fresh and fetch:
        try:
            records = fetch_holidays(zone, today - timedelta(days=30))
            data = {"zone": zone, "fetched": today.isoformat(), "records": records}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:  # no network: keep the old cache, if any
            logger.warning(f"School holiday calendar unavailable ({e}); using the cache")
    return _parse_holidays(data.get("records", [])) if data.get("zone") == zone else []


# ---------------------------------------------------------------------------
# Daily rhythm
# ---------------------------------------------------------------------------
def _hm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


@dataclass
class Rhythm:
    zone: str = "C"
    wednesday_school: str = "none"  # "none" or "morning"
    weekday_bedtime: str = "20:30"
    weekday_wakeup: str = "07:30"
    weekend_bedtime: str = "21:30"  # Saturday and Sunday evenings
    weekend_wakeup: str = "08:30"  # Saturday and Sunday mornings
    holidays: list[Holiday] | None = None  # injected (tests) or loaded lazily
    _holidays_day: date | None = field(default=None, repr=False)  # day the cache was last read

    @classmethod
    def from_config(cls, cfg: dict) -> Rhythm:
        r = cfg.get("rhythm", {})
        keys = ("zone", "wednesday_school", "weekday_bedtime", "weekday_wakeup", "weekend_bedtime", "weekend_wakeup")
        return cls(**{k: str(r[k]) for k in keys if k in r})

    def refresh_holidays(self, today: date) -> None:
        """Update the calendar cache from the network if it is old (blocking: run it in a thread)."""
        self.holidays = school_holidays(self.zone, today)
        self._holidays_day = today

    def _holidays(self, today: date) -> list[Holiday]:
        """From the cache only (called while building the prompt, on the event loop); read again
        each day so a long-running pet sees the new calendar."""
        if self._holidays_day is None and self.holidays is not None:
            return self.holidays  # injected
        if self._holidays_day != today:
            self.holidays = school_holidays(self.zone, today, fetch=False)
            self._holidays_day = today
        return self.holidays or []

    def holiday_on(self, d: date) -> Holiday | None:
        midnight = datetime.combine(d, time(12), TZ)
        return next((h for h in self._holidays(d) if h.start <= midnight < h.end), None)

    def next_holiday(self, d: date) -> Holiday | None:
        midnight = datetime.combine(d, time(0), TZ)
        return next((h for h in self._holidays(d) if h.start > midnight), None)

    def bedtime(self, d: date) -> time:
        return _hm(self.weekend_bedtime if d.weekday() >= 5 else self.weekday_bedtime)

    def wakeup(self, d: date) -> time:
        return _hm(self.weekend_wakeup if d.weekday() >= 5 else self.weekday_wakeup)

    def is_night(self, dt: datetime) -> bool:
        return dt.time() >= self.bedtime(dt.date()) or dt.time() < self.wakeup(dt.date())

    def next_wakeup(self, dt: datetime) -> datetime:
        d = dt.date() if dt.time() < self.wakeup(dt.date()) else dt.date() + timedelta(days=1)
        return datetime.combine(d, self.wakeup(d), TZ)

    def minutes_to_bedtime(self, dt: datetime) -> float:
        bed = datetime.combine(dt.date(), self.bedtime(dt.date()), dt.tzinfo or TZ)
        return (bed - dt).total_seconds() / 60

    def school_status(self, d: date) -> tuple[bool, str]:
        """(is there school today, French reason)."""
        if d.weekday() >= 5:
            return False, "c'est le week-end"
        holiday = public_holiday(d)
        if holiday:
            return False, f"c'est un jour férié ({holiday})"
        h = self.holiday_on(d)
        if h:
            return False, f"ce sont les {h.name[0].lower() + h.name[1:]}" if h.name.startswith("Vacances") else h.name
        if d.weekday() == 2 and self.wednesday_school == "none":
            return False, "le mercredi, il n'y a pas école"
        return True, "c'est un jour d'école"

    def day_part(self, dt: datetime) -> str:
        """French description of the moment and what it implies for the pet."""
        d, t = dt.date(), dt.time()
        school, _ = self.school_status(d)
        wed_morning_only = d.weekday() == 2 and self.wednesday_school == "morning"
        if self.is_night(dt):
            return "c'est la nuit : tout le monde dort"
        if t < time(9) and school:
            return "c'est le matin avant l'école : les enfants se préparent, sois bref et souhaite une bonne journée"
        if t < time(12):
            if school:
                return "c'est la matinée, normalement l'heure de l'école : si un enfant te parle, demande gentiment s'il n'a pas école aujourd'hui"
            return "c'est la matinée"
        if t < time(14):
            return "c'est l'heure du déjeuner"
        if t < time(16, 30) and school and not wed_morning_only:
            return "c'est l'après-midi, normalement l'heure de l'école"
        if t < time(18):
            return "c'est la fin d'après-midi, l'heure du goûter" if t < time(17) else "c'est la fin d'après-midi"
        if t < time(19, 30):
            return "c'est le début de soirée, souvent l'heure des devoirs, du bain ou du dîner"
        return "c'est le soir : l'heure de se calmer avant d'aller dormir ; préfère les activités douces"

    def now_section(self, dt: datetime) -> str:
        """French prompt section « Maintenant »."""
        d = dt.date()
        school, why = self.school_status(d)
        lines = [f"Nous sommes le {WEEKDAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]} {d.year}, "
                 f"{dt.hour}h{dt.minute:02d} : {self.day_part(dt)}."]  # fmt: skip
        lines.append(f"Aujourd'hui, {why}." if not school else "Aujourd'hui, c'est un jour d'école.")
        current = self.holiday_on(d)
        if not current:
            nxt = self.next_holiday(d)
            if nxt and (nxt.start.date() - d).days <= 21:
                days = (nxt.start.date() - d).days
                lines.append(
                    f"Les {nxt.name[0].lower() + nxt.name[1:]} commencent dans {days} jour{'s' if days > 1 else ''}."
                )
        else:
            back = current.end.date()
            lines.append(f"Retour à l'école le {WEEKDAYS_FR[back.weekday()]} {back.day} {MONTHS_FR[back.month - 1]}.")
        if self.is_night(dt):
            up = self.next_wakeup(dt)
            lines.append(f"C'est ta nuit : tu dors jusqu'à {up.hour}h{up.minute:02d}.")
        else:
            bed = self.bedtime(d)
            lines.append(f"Ce soir, tu iras dormir à {bed.hour}h{bed.minute:02d}.")
        return "## Maintenant\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# "How long ago", said in French
# ---------------------------------------------------------------------------
def ago(then: datetime, now: datetime) -> str:
    """« il y a quelques minutes », « ce matin », « hier soir », « il y a 5 jours »…"""
    then, now = then.astimezone(TZ), now.astimezone(TZ)
    minutes = (now - then).total_seconds() / 60
    if minutes < 5:
        return "à l'instant"
    if minutes < 60:
        return "il y a quelques minutes"
    days = (now.date() - then.date()).days
    if days == 0:
        hours = int(minutes // 60)
        if hours < 3:
            return f"il y a {hours} heure{'s' if hours > 1 else ''}"
        return "ce matin" if then.hour < 12 else "cet après-midi" if then.hour < 18 else "ce soir"
    if days == 1:
        return "hier matin" if then.hour < 12 else "hier après-midi" if then.hour < 18 else "hier soir"
    if days == 2:
        return "avant-hier"
    if days < 14:
        return f"il y a {days} jours"
    if days < 60:
        return f"il y a {days // 7} semaines"
    return f"il y a {days // 30} mois"
