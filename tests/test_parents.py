"""Parents' space and technical monitoring, from session logs and memory files (no network)."""

import json
import subprocess
from datetime import date

import pytest

from aipet import character, monitoring, parents
from aipet.memory import Memory

FLOCON = character.load("flocon")
DAY = date(2026, 11, 13)


def ev(s, type_, **kw):
    return {"t": f"2026-11-13T19:{int(s) // 60:02d}:{int(s) % 60:02d}.000+01:00", "s": s, "type": type_, **kw}


SESSION = [
    ev(0, "session_start", mode="web", config={"engines": {"conversation": "openrouter · google/gemini-3.6-flash"}}),
    ev(1, "llm_response", raw_text="[joie] Miaou-coucou ! Qui est là ?"),
    ev(2, "transcription", text="Mila."),
    ev(2.1, "user_started_speaking"),
    ev(3.9, "user_stopped_speaking"),
    ev(3.9, "turn", speaker="Mila", certainty="sure"),
    ev(4.8, "metric", name="ttfb_ms", service="OpenRouterLLMService#0", value=900),
    ev(4.9, "metric", name="tokens", service="OpenRouterLLMService#0", input=1000, output=20, cache=600, reasoning=0),
    ev(5.0, "llm_response", raw_text="[parle:Mila] [triste] Oh, tu as l'air triste ?"),
    ev(5.5, "bot_started_speaking", turn=2, voice_latency_ms=1600, perceived_latency_ms=3400),
    ev(6, "metric", name="tts_characters", service="TTS", value=30),
    ev(7, "transcription", text="Welcome to VoiceOver."),
    ev(7.5, "turn", speaker="Mila", certainty="sure"),
    ev(8, "llm_response", raw_text="[silence]"),
    ev(9, "transcription", text="Putain de bruit"),
    ev(9.2, "transcript_dropped", text="Putain de bruit"),
    ev(10, "transcription", text="C'est Lila, putain !"),
    ev(10.5, "turn", speaker="Lila", certainty="sure"),
    ev(10.6, "speaker", name="Lila", certainty="sure", previous="Mila"),
    ev(11, "llm_response", raw_text=""),  # a tool call
    ev(12, "guess_end", player="Mila", found=False, questions=2),
    ev(13, "error", message="502 Provider audio stream failed", fatal=False),
    ev(20, "session_end", duration_s=20),
]


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("PET_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("PET_STATE", str(tmp_path / "state"))
    d = tmp_path / "logs" / "sessions"
    d.mkdir(parents=True)
    (d / "2026-11-13_190000_web.jsonl").write_text("\n".join(json.dumps(e) for e in SESSION))
    return d


def test_thread_is_a_readable_conversation():
    items = parents.thread(SESSION, FLOCON)
    kinds = [(i["kind"], i.get("who"), i.get("text")) for i in items]
    assert kinds[0] == ("pet", None, "Miaou-coucou ! Qui est là ?")
    assert ("child", "Mila", "Mila.") in kinds
    assert ("pet", None, "Oh, tu as l'air triste ?") in kinds  # control tags removed
    assert next(i for i in items if i.get("text") == "Oh, tu as l'air triste ?")["emotions"] == ["triste"]
    assert any(i.get("silent") for i in items)
    assert ("child", "Lila", "C'est Lila, putain !") in kinds  # the dropped transcript is gone
    assert not any("Putain de bruit" in (i.get("text") or "") for i in items)
    markers = [i["text"] for i in items if i["kind"] == "marker"]
    assert "Lila prend la parole" in markers and "Devinette : Mila a gagné (2 questions)" in markers


def test_day_digest(logs):
    d = parents.day_digest(DAY, FLOCON)
    assert d["minutes"] == 0 and len(d["conversations"]) == 1
    mila = next(c for c in d["children"] if c["name"] == "Mila")
    assert mila["messages"] == 2 and mila["activities"] == ["Devinette : Mila a gagné (2 questions)"]
    assert parents.days_with_sessions() == ["2026-11-13"]


def test_review_flags_and_seen(logs, tmp_path):
    mem = Memory(tmp_path / "mem", "Flocon")
    (mem.root / "famille" / "mila.md").write_text(
        "---\nprenom: Mila\n---\n# Mila\n\n## À savoir\n- confidence : a peur des orages\n"
    )
    flags = parents.review(mem.root, FLOCON, DAY, days=1)
    kinds = {f["kind"]: f for f in flags}
    assert set(kinds) == {"sad", "noise", "swear", "name", "game", "confidence"}
    assert "Welcome to VoiceOver" in kinds["noise"]["detail"] and "Lila" in kinds["name"]["title"]
    assert "a peur des orages" in kinds["confidence"]["detail"]
    assert "Putain de bruit" not in json.dumps(flags)  # dropped transcripts are not flagged
    parents.mark_seen([kinds["sad"]["id"]])
    again = parents.review(mem.root, FLOCON, DAY, days=1)
    assert [f["kind"] for f in again][-1] == "sad" and again[-1]["seen"]  # seen ones last, ids stable


def git_log(mem):
    return subprocess.run(["git", "log", "--format=%s"], cwd=mem.root, capture_output=True, text=True).stdout


def test_parent_corrections_are_committed(tmp_path):
    mem = Memory(tmp_path / "mem", "Flocon")
    card = mem.root / "famille" / "noam.md"
    card.write_text("---\nprenom: Noam\n---\n# Noam\n\n## Aime\n- les dinosaures\n- le foot\n")
    s = parents.structured(mem.root, "famille/noam.md")
    assert s["kind"] == "card" and s["sections"]["Aime"] == ["les dinosaures", "le foot"]
    parents.edit(
        mem, "famille/noam.md", {"section": "Aime", "index": 0, "expected": "les dinosaures", "text": "les T-Rex"}
    )
    parents.edit(mem, "famille/noam.md", {"section": "Aime", "index": 1, "expected": "le foot", "text": ""})
    assert "- les T-Rex" in card.read_text() and "foot" not in card.read_text()
    with pytest.raises(ValueError, match="changé"):
        parents.edit(mem, "famille/noam.md", {"section": "Aime", "index": 0, "expected": "autre chose", "text": "x"})
    assert "Correction par un parent (famille/noam.md)" in git_log(mem)

    (mem.root / "jeux").mkdir(exist_ok=True)
    games = mem.root / "jeux" / "devine.json"
    games.write_text(json.dumps({"players": {"Mila": {"games": [{"date": "2026-11-13T19:02", "found": False,
                                                                  "answer": "un fennec", "questions": 2}]}}}))  # fmt: skip
    parents.edit(mem, "jeux/devine.json", {"player": "Mila", "index": 0, "answer": "un chat"})
    assert json.loads(games.read_text())["players"]["Mila"]["games"][0]["answer"] == "un chat"

    (mem.root / "histoires").mkdir(exist_ok=True)
    uni = mem.root / "histoires" / "univers.json"
    uni.write_text(json.dumps({"characters": {"noam": {"name": "Noam"}, "piko": {"name": "Piko"}},
                               "stories": [{"title": "Le gâteau"}]}))  # fmt: skip
    parents.edit(mem, "histoires/univers.json", {"character": "noam", "delete": True})
    parents.edit(mem, "histoires/univers.json", {"story": 0, "delete": True})
    data = json.loads(uni.read_text())
    assert list(data["characters"]) == ["piko"] and data["stories"] == []

    for bad in ("../outside.md", ".git/config", "nope.md"):
        with pytest.raises(ValueError):
            parents.structured(mem.root, bad)


def test_latency_costs_and_errors(logs):
    lat = monitoring.latency(SESSION)
    assert lat["turns"] == [{"t": "19:00:05", "text": "Mila.", "stt": None, "wait": 1800, "llm": 900, "voice": 700,
                             "total": 3400}]  # fmt: skip
    table = {
        "google/gemini-3.6-flash": {"prompt": "0.000001", "completion": "0.000004", "input_cache_read": "0.0000001"}
    }
    c = monitoring.costs(days=400, cost_cfg={"tts_usd_per_million_chars": 10}, table=table)
    day = next(d for d in c["days"] if d["day"] == "2026-11-13")
    assert day["conversation_usd"] == round(400 * 1e-6 + 600 * 1e-7 + 20 * 4e-6, 4)
    assert day["tts_chars"] == 30 and day["tts_usd"] == round(30 / 1e6 * 10, 4) and day["stt_usd"] is None
    assert round(day["stt_minutes"] * 60, 1) == 1.8
    errs = monitoring.errors(days=400)
    assert [e["kind"] for e in errs] == ["Erreur"] and "502" in errs[0]["detail"]


def test_removing_a_game_or_lesson_recomputes_the_records(tmp_path):
    mem = Memory(tmp_path / "mem", "Flocon")
    (mem.root / "jeux").mkdir(exist_ok=True)
    quiz = mem.root / "jeux" / "quiz.json"
    quiz.write_text(json.dumps({"players": {"Mila": {"games": [
        {"date": "2026-11-12", "theme": "Les animaux", "score": 3, "total": 5},
        {"date": "2026-11-13", "theme": "animaux", "score": 5, "total": 5}],
        "best": {"animaux": {"theme": "animaux", "score": 5, "total": 5}}, "best_streak": 5, "play_days": []}}}))  # fmt: skip
    parents.edit(mem, "jeux/quiz.json", {"player": "Mila", "index": 1, "delete": True})
    assert json.loads(quiz.read_text())["players"]["Mila"]["best"]["animaux"]["score"] == 3

    yn = mem.root / "jeux" / "ni_oui_ni_non.json"
    yn.write_text(json.dumps({"players": {"Noam": {"games": [{"held": 4}, {"held": 12}], "best": 12}}, "asked": []}))
    parents.edit(mem, "jeux/ni_oui_ni_non.json", {"player": "Noam", "index": 1, "delete": True})
    assert json.loads(yn.read_text())["players"]["Noam"]["best"] == 4

    learn = mem.root / "jeux" / "apprendre.json"
    learn.write_text(json.dumps({"players": {"Noam": {
        "domains": {"nature": {"name": "nature", "level": 4, "lessons": 2}},
        "topics": [{"date": "2026-11-14", "topic": "les loups", "domain": "nature"}],
        "interests": {"nature": 1}, "open_questions": ["pourquoi le ciel est bleu ?"]}}}))  # fmt: skip
    parents.edit(mem, "jeux/apprendre.json", {"player": "Noam", "index": 0, "delete": True})
    parents.edit(mem, "jeux/apprendre.json", {"player": "Noam", "question": 0, "delete": True})
    r = json.loads(learn.read_text())["players"]["Noam"]
    assert r["topics"] == [] and r["domains"]["nature"]["lessons"] == 1 and r["interests"] == {}
    assert r["open_questions"] == []
    assert parents.structured(mem.root, "jeux/apprendre.json")["kind"] == "learn"
