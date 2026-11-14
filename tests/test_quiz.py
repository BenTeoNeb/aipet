"""Quiz: prepared questions, score and streaks kept by code, history across games (no network)."""

import asyncio
from datetime import date, datetime

import pytest

from aipet.games import quiz as quiz_mod
from aipet.games.common import days_in_a_row
from aipet.games.quiz import Quiz
from aipet.memory import Memory
from aipet.rhythm import TZ

CFG = {"engines": {}}


def q(i, theme="animaux"):
    return {"question": f"{theme} question {i} ?", "choices": ["a", "b", "c"], "answer": f"réponse {i}",
            "explanation": f"parce que {i}"}  # fmt: skip


class Who:
    def __init__(self, name, certainty="sure"):
        self.current, self.certainty = name, certainty


class Keeper:
    TOOLS_EXHAUSTED = {"error": "trop"}

    def __init__(self, name):
        self.who = Who(name)

    def tool_budget_exhausted(self):
        return False


@pytest.fixture
def setup(tmp_path, monkeypatch):
    mem = Memory(tmp_path)
    calls = []
    counter = {"n": 0}

    def fake_generate(cfg, theme, level, exclude, n, difficulty=2):
        calls.append({"theme": theme, "level": level, "exclude": list(exclude), "n": n, "difficulty": difficulty})
        out = []
        while len(out) < n:
            counter["n"] += 1
            out.append(q(counter["n"], theme))
        return out

    monkeypatch.setattr(quiz_mod, "generate_questions", fake_generate)
    clock = {"t": datetime(2026, 11, 12, 17, 0, tzinfo=TZ)}
    monkeypatch.setattr(quiz_mod, "now", lambda: clock["t"])
    return mem, calls, clock


def play(quiz, theme, results):
    start = asyncio.run(quiz.start(theme))
    out = [start]
    for ok in results:
        out.append(quiz.answer(ok))
    return out


def test_full_game_score_streak_and_explanations(setup):
    mem, calls, _ = setup
    quiz = Quiz(CFG, mem.root, Keeper("Mila"))
    start, *answers = play(quiz, "les animaux", [True, True, False, True, True])
    assert start["total"] == 5 and start["question"]["number"] == 1 and start["record_on_this_theme"] is None
    assert answers[0]["correct"] and answers[0]["score"] == "1/1" and answers[0]["next_question"]["number"] == 2
    wrong = answers[2]
    assert not wrong["correct"] and wrong["right_answer"] == "réponse 3" and wrong["explanation"] == "parce que 3"
    assert wrong["streak"] == 0
    end = answers[-1]["finished"]
    assert end["score"] == 4 and end["total"] == 5 and end["best_streak"] == 2 and end["days_in_a_row"] == 1
    assert quiz.game is None  # finished
    saved = quiz.store.load()["players"]["Mila"]
    assert saved["games"][0]["questions"][2] == {"question": "les animaux question 3 ?", "answer": "réponse 3",
                                                 "correct": False}  # fmt: skip


def test_no_repeated_questions_and_level_from_card(setup):
    mem, calls, _ = setup
    mem.apply({"memory": {"participants": [], "topics": [], "summary": ""},
               "people": [{"name": "Mila", "age": "7 ans", "role": "enfant", "likes": [], "dislikes": [], "facts": [],
                           "school_class": "CE1", "school_topics": [], "remove": []}],
               "stories": [], "pet_life": {}, "pet_summary": ""}, datetime.now(TZ))  # fmt: skip
    quiz = Quiz(CFG, mem.root, Keeper("Mila"))
    play(quiz, "Les animaux", [True] * 5)
    play(quiz, "animaux", [True] * 5)  # same theme, other wording
    assert calls[0]["level"] == "CE1" and calls[0]["exclude"] == []
    assert calls[1]["exclude"] == [f"Les animaux question {i} ?" for i in range(1, 6)]


def test_records_and_history_next_time(setup):
    mem, _, clock = setup
    quiz = Quiz(CFG, mem.root, Keeper("Noam"))
    play(quiz, "l'espace", [True, False, False, True, False])  # 2/5
    clock["t"] = datetime(2026, 11, 13, 17, 0, tzinfo=TZ)
    start, *answers = play(quiz, "espace", [True, True, True, False, True])  # 4/5, next day
    assert start["record_on_this_theme"] == "2/5"
    end = answers[-1]["finished"]
    assert end["new_record"] and end["previous_best"]["score"] == 2 and end["days_in_a_row"] == 2
    h = quiz.history()
    assert h["games_played"] == 2 and h["themes"] == ["espace : record 4/5, niveau 2 sur 5"] and h["best_streak"] == 3
    assert "espace, 4/5" in h["last_game"]
    assert Quiz(CFG, mem.root, Keeper("Mila")).history()["games_played"] == 0  # per child


def test_unknown_speaker_plays_as_family(setup):
    mem, calls, _ = setup
    keeper = Keeper("Mila")
    keeper.who.certainty = "probable"
    quiz = Quiz(CFG, mem.root, keeper)
    play(quiz, "les fruits", [True] * 5)
    assert calls[0]["level"] is None and "famille" in quiz.store.load()["players"]


def test_errors(setup, monkeypatch):
    mem, _, _ = setup
    quiz = Quiz(CFG, mem.root, Keeper("Mila"))
    assert "error" in quiz.answer(True)  # no game
    assert "error" in asyncio.run(quiz.start(""))

    def boom(*a):
        raise RuntimeError("model down")

    monkeypatch.setattr(quiz_mod, "generate_questions", boom)
    assert "error" in asyncio.run(quiz.start("les dinosaures")) and quiz.game is None


def test_generate_questions_filters_repeats(monkeypatch):
    monkeypatch.setattr(
        quiz_mod.engines,
        "complete_json",
        lambda *a, **k: {
            "questions": [q(1), {"question": "Déjà posée ?", "choices": [], "answer": "x", "explanation": ""}, q(2)]
        },  # fmt: skip
    )
    out = quiz_mod.generate_questions(CFG, "animaux", "CE1", ["déjà posée"], 5)
    assert [x["question"] for x in out] == ["animaux question 1 ?", "animaux question 2 ?"]


def test_days_in_a_row():
    days = ["2026-11-10", "2026-11-11", "2026-11-12"]
    assert days_in_a_row(days, date(2026, 11, 12)) == 3
    assert days_in_a_row(days, date(2026, 11, 13)) == 3  # not played yet today: still counts
    assert days_in_a_row(days, date(2026, 11, 14)) == 0


def test_difficulty_rises_with_success_and_falls_back(setup):
    mem, calls, _ = setup
    quiz = Quiz(CFG, mem.root, Keeper("Mila"))
    ends = [play(quiz, "les animaux", r)[-1]["finished"] for r in (
        [True] * 5,                                  # 5/5: 2 → 3
        [True, True, True, True, False],             # 4/5: 3 → 4
        [True, True, True, False, False],            # 3/5: stays 4
        [True] * 5,                                  # 4 → 5
        [True] * 5,                                  # stays 5 (max)
        [False, False, True, False, False],          # 1/5: 5 → 4
    )]  # fmt: skip
    assert [c["difficulty"] for c in calls] == [2, 3, 4, 4, 5, 5]
    assert [e["level_change"] for e in ends] == ["up", "up", None, "up", None, "down"]
    assert ends[-1]["difficulty"] == 4
    start = asyncio.run(quiz.start("animaux"))
    assert start["difficulty"].startswith("niveau 4 sur 5")
    assert "niveau 4 sur 5" in quiz.history()["themes"][0]
    play(quiz, "les volcans", [True] * 5)  # another theme starts from the beginning
    assert calls[-1]["difficulty"] == 2


def test_duplicate_answer_is_rejected_and_stop_drops_the_game(setup):
    mem, _, _ = setup
    quiz = Quiz(CFG, mem.root, Keeper("Mila"))
    asyncio.run(quiz.start("animaux"))
    assert quiz.answer(True, question_number=1)["score"] == "1/1"
    dup = quiz.answer(True, question_number=1)  # the same answer sent twice
    assert "déjà comptée" in dup["error"] and dup["current_question"]["number"] == 2 and quiz.game.score == 1
    assert quiz.on_user_message("la vache") is None
    quiz.stop()
    assert quiz.game is None
