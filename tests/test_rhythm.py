"""Sense of time: day parts, school days and holidays, bedtime, "how long ago" (no network)."""

import json
from datetime import date, datetime, timedelta

import pytest

from aipet import rhythm
from aipet.rhythm import TZ, Holiday, Rhythm

TOUSSAINT = Holiday("Vacances de la Toussaint", datetime(2026, 10, 17, tzinfo=TZ), datetime(2026, 11, 2, tzinfo=TZ))
NOEL = Holiday("Vacances de Noël", datetime(2026, 12, 19, tzinfo=TZ), datetime(2027, 1, 4, tzinfo=TZ))


def at(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


@pytest.fixture
def r():
    return Rhythm(holidays=[TOUSSAINT, NOEL])


def test_easter_and_public_holidays():
    assert rhythm.easter(2026) == date(2026, 4, 5) and rhythm.easter(2027) == date(2027, 3, 28)
    assert rhythm.public_holiday(date(2026, 11, 11)) == "l'Armistice de 1918"
    assert rhythm.public_holiday(date(2026, 5, 14)) == "l'Ascension"  # Easter + 39 days
    assert rhythm.public_holiday(date(2026, 11, 12)) is None


def test_school_status(r):
    assert r.school_status(date(2026, 11, 12)) == (True, "c'est un jour d'école")  # Thursday
    assert r.school_status(date(2026, 11, 11))[1].startswith("c'est un jour férié")
    assert r.school_status(date(2026, 11, 14)) == (False, "c'est le week-end")
    assert r.school_status(date(2026, 10, 20)) == (False, "ce sont les vacances de la Toussaint")
    assert r.school_status(date(2026, 11, 2))[0] is True  # back to school on Monday
    assert r.school_status(date(2026, 11, 18))[0] is False  # Wednesday, 4-day week
    assert Rhythm(wednesday_school="morning", holidays=[]).school_status(date(2026, 11, 18))[0] is True


def test_bedtime_weekdays_and_weekend(r):
    assert r.is_night(at(2026, 11, 12, 20, 30)) and not r.is_night(at(2026, 11, 12, 20, 29))  # Thursday
    assert r.is_night(at(2026, 11, 13, 7, 29)) and not r.is_night(at(2026, 11, 13, 7, 30))
    assert not r.is_night(at(2026, 11, 14, 21, 0)) and r.is_night(at(2026, 11, 14, 21, 30))  # Saturday
    assert r.is_night(at(2026, 11, 15, 8, 29)) and not r.is_night(at(2026, 11, 15, 8, 30))  # Sunday morning
    assert r.next_wakeup(at(2026, 11, 13, 22, 0)) == at(2026, 11, 14, 8, 30)  # Friday night → Saturday 8:30
    assert r.minutes_to_bedtime(at(2026, 11, 12, 20, 20)) == 10


def test_day_parts(r):
    assert "avant l'école" in r.day_part(at(2026, 11, 12, 8, 0))
    assert "l'heure de l'école" in r.day_part(at(2026, 11, 12, 10, 0))
    assert "l'heure de l'école" not in r.day_part(at(2026, 11, 14, 10, 0))  # Saturday
    assert "goûter" in r.day_part(at(2026, 11, 12, 16, 45))
    assert "se calmer" in r.day_part(at(2026, 11, 12, 20, 0))
    assert "nuit" in r.day_part(at(2026, 11, 12, 23, 0))


def test_now_section(r):
    s = r.now_section(at(2026, 12, 7, 17, 0))  # Monday, Noël in 12 days
    assert s.startswith("## Maintenant") and "lundi 7 décembre 2026, 17h00" in s
    assert "Les vacances de Noël commencent dans 12 jours." in s and "tu iras dormir à 20h30" in s
    s = r.now_section(at(2026, 10, 20, 10, 0))
    assert "ce sont les vacances de la Toussaint" in s and "Retour à l'école le lundi 2 novembre" in s
    assert "tu dors jusqu'à 7h30" in r.now_section(at(2026, 11, 12, 22, 40))


def test_ago():
    now = at(2026, 11, 12, 18, 0)
    assert rhythm.ago(now - timedelta(minutes=2), now) == "à l'instant"
    assert rhythm.ago(now - timedelta(minutes=30), now) == "il y a quelques minutes"
    assert rhythm.ago(now - timedelta(hours=2), now) == "il y a 2 heures"
    assert rhythm.ago(at(2026, 11, 12, 8, 0), now) == "ce matin"
    assert rhythm.ago(at(2026, 11, 11, 19, 0), now) == "hier soir"
    assert rhythm.ago(at(2026, 11, 7, 10, 0), now) == "il y a 5 jours"
    assert rhythm.ago(at(2026, 10, 1, 10, 0), now) == "il y a 6 semaines"


def test_holidays_cache_and_offline(tmp_path, monkeypatch):
    cache = tmp_path / "holidays.json"
    records = [
        {"description": "Vacances de Noël", "start_date": "2026-12-18T23:00:00+00:00", "end_date": "2027-01-03T23:00:00+00:00"},
        {"description": "Vacances de Noël", "start_date": "2026-12-18T23:00:00+00:00", "end_date": "2027-01-03T23:00:00+00:00"},
    ]  # fmt: skip
    calls = []
    monkeypatch.setattr(rhythm, "fetch_holidays", lambda zone, since: calls.append(zone) or records)
    hs = rhythm.school_holidays("C", date(2026, 11, 12), cache)
    assert len(hs) == 1 and hs[0].start.date() == date(2026, 12, 19) and hs[0].end.date() == date(2027, 1, 4)
    rhythm.school_holidays("C", date(2026, 11, 13), cache)
    assert calls == ["C"]  # cached for a week

    def offline(zone, since):
        raise OSError("no network")

    monkeypatch.setattr(rhythm, "fetch_holidays", offline)
    assert len(rhythm.school_holidays("C", date(2026, 11, 30), cache)) == 1  # stale cache still used
    assert json.loads(cache.read_text())["zone"] == "C"
    assert rhythm.school_holidays("A", date(2026, 11, 30), cache) == []  # other zone, no data: no holidays
