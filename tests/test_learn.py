"""Learning activity: suggestions from what the child likes, lesson plan, level per domain (no network)."""

import asyncio
from datetime import datetime

import pytest

from aipet.games import learn as learn_mod
from aipet.games.learn import START_LEVEL, LearnGame
from aipet.memory import Memory
from aipet.rhythm import TZ


class Who:
    def __init__(self, name):
        self.current, self.certainty = name, "sure"


class Keeper:
    TOOLS_EXHAUSTED = {"error": "trop"}

    def __init__(self, name):
        self.who = Who(name)

    def tool_budget_exhausted(self):
        return False


def lesson(title, n=3):
    return {
        "title": title,
        "discoveries": [
            {"fact": f"fait {i}", "image": f"image {i}", "guess_question": f"à ton avis {i} ?"} for i in range(1, n + 1)
        ],  # fmt: skip
        "link": "comme les volcans",
        "bonus": "et sur Mars ?",
        "check_question": "tu peux me redire ?",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls = []

    def fake(cfg, topic, domain, who, level, seen):
        calls.append({"topic": topic, "domain": domain, "who": who, "level": level, "seen": seen})
        return lesson(f"Leçon {len(calls)}")

    monkeypatch.setattr(learn_mod, "generate_lesson", fake)
    monkeypatch.setattr(learn_mod, "now", lambda: datetime(2026, 11, 12, 16, 0, tzinfo=TZ))
    mem = Memory(tmp_path)
    mem.apply({"memory": {"participants": [], "topics": [], "summary": ""},
               "people": [{"name": "Mila", "age": "7 ans", "role": "enfant", "likes": ["les dinosaures"],
                           "dislikes": [], "facts": [], "school_class": "CE1",
                           "school_topics": ["lire l'heure"], "remove": []}],
               "stories": [], "pet_life": {}, "pet_summary": ""}, datetime.now(TZ))  # fmt: skip
    return LearnGame({}, mem.root, Keeper("Mila")), calls


def run_lesson(g, topic, domain, answers, bonus=True, liked=True, questions=None):
    asyncio.run(g.start(topic, domain))
    for a in answers:
        g.next(a)
    return g.end("le magma est très chaud", liked, bonus, questions or [])


def test_suggest_uses_card_school_and_history(env):
    g, _ = env
    s = g.suggest()
    assert s["class"] == "CE1" and s["likes"] == ["les dinosaures"] and s["at_school_now"] == ["lire l'heure"]
    assert s["domains"] == [] and s["recent_topics"] == []
    run_lesson(g, "les volcans", "sciences de la Terre", ["oui"] * 3, questions=["pourquoi la lave est rouge ?"])
    s = g.suggest()
    assert s["domains"] == ["sciences de la Terre : niveau 4 sur 5, 1 leçon(s)"]
    assert s["recent_topics"] == ["les volcans (sciences de la Terre, a aimé)"]
    assert s["loves_learning_about"] == ["sciences de la Terre"] and s["open_questions"] == [
        "pourquoi la lave est rouge ?"
    ]


def test_lesson_plan_prompt_and_progress(env):
    g, calls = env
    res = asyncio.run(g.start("les volcans", "sciences"))
    assert res["discoveries"] == 3 and calls[0]["level"] == START_LEVEL and calls[0]["who"] == "Mila, en CE1"
    sec = g.prompt_section()
    assert sec.startswith("## Leçon en cours : « Leçon 1 »") and "→ 1. fait 1 (image : image 1" in sec
    assert "Défi bonus (à la fin) : et sur Mars ?" in sec and "Lien avec ce qu'il a déjà vu : comme les volcans" in sec
    assert g.on_user_message("je sais pas") == "leçon : découverte 1 sur 3"
    assert g.next("oui") == {"discovery": "2 sur 3", "guess_question": "à ton avis 2 ?"}
    assert "1. fait 1" in g.prompt_section() and "[fait]" in g.prompt_section()
    g.next("un peu")
    assert "défi bonus" in g.next("oui")["note"]


def test_level_per_domain_up_down_and_seen_topics(env):
    g, calls = env
    assert run_lesson(g, "les volcans", "sciences", ["oui", "oui", "oui"])["level_change"] == "up"  # 3 → 4
    assert run_lesson(g, "les séismes", "sciences", ["oui", "un peu", "oui"], bonus=False)["level_change"] is None
    assert calls[1]["level"] == 4 and calls[1]["seen"] == ["les volcans"]
    assert run_lesson(g, "les marées", "sciences", ["non", "non", "un peu"])["level_change"] == "down"  # 4 → 3
    asyncio.run(g.start("les pharaons", "histoire"))  # another domain starts at the class level
    assert calls[-1]["level"] == START_LEVEL


def test_stop_and_errors(env, monkeypatch):
    g, _ = env
    assert "error" in g.next("oui") and "error" in g.end("x")
    asyncio.run(g.start("les volcans", "sciences"))
    g.stop()
    assert g.lesson is None and g.prompt_section() == "" and g.suggest()["recent_topics"] == []

    def boom(*a):
        raise RuntimeError("model down")

    monkeypatch.setattr(learn_mod, "generate_lesson", boom)
    assert "error" in asyncio.run(g.start("les volcans", "sciences")) and g.lesson is None
    assert "error" in asyncio.run(g.start("", "sciences"))


def test_duplicate_discovery_is_rejected(env):
    g, _ = env
    asyncio.run(g.start("les volcans", "sciences"))
    assert g.next("oui", discovery_done=1)["discovery"] == "2 sur 3"
    assert "déjà faite" in g.next("oui", discovery_done=1)["error"] and g.lesson["step"] == 1
