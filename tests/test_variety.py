"""Variety: the code picks what the pet suggests, so it stops offering « une histoire ou une devinette ? »
every time (seen in all 17 sessions up to 2026-11-21)."""

import asyncio
import json
from datetime import date

from pipecat.processors.aggregators.llm_context import LLMContext

from aipet.conversation import OFFER_TURNS, Conversation
from aipet.memory import Memory
from aipet.skills import HERE, Skills
from aipet.variety import FAMILY, ActivityHistory

DAY = date(2026, 11, 21)
SKILLS = ["story", "guess-what-im-thinking", "quiz", "yes-no-game", "learn"]
LABELS = {s: f"<{s}>" for s in SKILLS}


def test_never_played_first_next_to_the_favourite(tmp_path):
    h = ActivityHistory(tmp_path)
    for _ in range(3):
        h.played("Zina", "story", DAY)
    h.played("Zina", "guess-what-im-thinking", date(2026, 11, 15))
    s = h.suggest("Zina", SKILLS, DAY)
    assert s == {"new": "quiz", "since": None, "favourite": "story"}
    for skill in ("quiz", "yes-no-game", "learn"):
        h.played("Zina", skill, DAY)
    assert h.suggest("Zina", SKILLS, DAY)["new"] == "guess-what-im-thinking"  # the longest ago
    assert h.suggest("Zina", SKILLS, DAY)["since"] == 6


def test_a_declined_suggestion_gives_way(tmp_path):
    h = ActivityHistory(tmp_path)
    h.played("Rami", "story", DAY)
    first = h.suggest("Rami", SKILLS, DAY)["new"]
    h.offer("Rami", LABELS, DAY)  # offered, not played
    assert h.suggest("Rami", SKILLS, DAY)["new"] != first
    h.played("Rami", first, DAY)  # played at last: its offers start again from zero
    assert json.loads(h.path.read_text())["players"]["Rami"][first]["offered"] == 0


def test_hint_text_and_unknown_child(tmp_path):
    h = ActivityHistory(tmp_path)
    h.played("Zina", "story", DAY)
    h.played(None, "learn", date(2026, 11, 1))
    line = h.offer("Zina", LABELS, DAY, record=False)
    assert "propose d'abord <guess-what-im-thinking> (jamais fait avec Zina)" in line
    assert "ou bien <story> que Zina adore" in line
    family = h.offer(None, LABELS, DAY, record=False)  # nobody known: everyone's history merged
    assert "avec les enfants" in family and "que les enfants adorent" in family
    assert ActivityHistory(tmp_path).offer(None, {}, DAY) == ""


def test_bootstrap_from_the_session_logs(tmp_path):
    logs = tmp_path / "sessions"
    logs.mkdir()
    events = [
        {"t": "2026-11-14T14:01:00+01:00", "type": "turn", "speaker": "Rami", "certainty": "sure"},
        {"t": "2026-11-14T14:02:00+01:00", "type": "skill_start", "name": "story"},
        {"t": "2026-11-14T14:09:00+01:00", "type": "turn", "speaker": None, "certainty": "unknown"},
        {"t": "2026-11-14T14:10:00+01:00", "type": "skill_start", "name": "learn"},
    ]
    (logs / "2026-11-14_140142_web.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    h = ActivityHistory(tmp_path / "mem", logs)
    players = json.loads(h.path.read_text())["players"]
    assert players["Rami"]["story"] == {"last": "2026-11-14", "count": 1, "offered": 0}
    assert players[FAMILY]["learn"]["count"] == 1
    h.played("Rami", "quiz", DAY)
    ActivityHistory(tmp_path / "mem", logs)  # only once: the record is not rebuilt over new data
    assert "quiz" in json.loads(h.path.read_text())["players"]["Rami"]


def conversation(tmp_path):
    k = Conversation(
        Memory(tmp_path, "Flocon"), {"memory": {}}, LLMContext(), "BASE", skills=Skills([HERE / "builtin_skills"])
    )
    k.who.current, k.who.certainty = "Zina", "sure"
    return k


def say(k, text):
    k.context.add_message({"role": "user", "content": text})
    k.tag_last_user_message()
    return k.context.get_messages()[-1]["content"].split("⟧")[0]


def test_greeting_and_idle_turns_carry_the_suggestion(tmp_path):
    k = conversation(tmp_path)
    k.who.current, k.who.certainty = None, "unknown"
    assert "Idée du moment : si tu proposes une activité" in k.greeting()
    k.who.current, k.who.certainty = "Zina", "sure"
    labels = [say(k, f"message {i}") for i in range(OFFER_TURNS + 1)]
    assert all("avec Zina" in label for label in labels[:OFFER_TURNS])  # her own, once she is known
    assert "propose d'abord" not in labels[-1]  # the moment has passed


def test_an_activity_is_recorded_and_its_end_brings_a_new_suggestion(tmp_path):
    k = conversation(tmp_path)
    k.greeting()
    asyncio.run(k.use_skill("story"))
    assert "propose d'abord" not in say(k, "une histoire !")  # an activity is going on
    rec = json.loads(k.activities.path.read_text())["players"]["Zina"]["story"]
    assert rec["count"] == 1 and rec["last"]
    res = asyncio.run(k.end_skill())
    assert "propose d'abord" in res["propose"] and "une histoire" not in res["propose"].split(", ou bien")[0]
    assert "propose d'abord" in say(k, "et maintenant ?")
