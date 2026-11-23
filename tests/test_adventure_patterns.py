"""The catalogue of puzzle patterns (aipet/games/adventure_patterns.py): each one is checked and won by
speaking. A change to the model must keep them all green."""

import json

import pytest

from aipet.games.adventure_engine import World, check
from aipet.games.adventure_patterns import PATTERNS


def say(w, st, verb, obj="", target=""):
    intent = {"verb": "go", "to": obj} if verb == "go" else {"verb": verb, "object": obj, "target": target}
    return w.act(st, intent)


@pytest.mark.parametrize("name", list(PATTERNS))
def test_the_pattern_is_checked_and_won_by_speaking(name):
    graph, spoken = PATTERNS[name]
    errors, report = check(graph)
    assert [e for e in errors if not e.startswith("facile:")] == [], name  # tiny: below the size limits
    assert report["wins"] and not report.get("stuck")
    w = World(graph)
    st = w.start()
    for line in spoken:
        res = say(w, st, *line)
        assert res["outcome"] not in ("absent", "free"), (name, line, res)
    assert st.ending == "gagne", (name, st.to_dict())


def test_a_wrong_answer_repeats_the_question_and_a_talk_asks_it():
    graph, _ = PATTERNS["riddle"]
    w = World(graph)
    st = w.start()
    wrong = say(w, st, "answer", "une brosse", "la porte")
    assert wrong["outcome"] == "not_yet" and wrong["text"] == "Non !" and "dents" in wrong["question"]
    assert say(w, st, "answer", "un peigne")["outcome"] == "done"  # the only riddle here: no need to name it
    assert w.prop(st, "porte", "open")


def test_a_companion_comes_along_and_stays_when_sent_away():
    graph, _ = PATTERNS["companion"]
    w = World(graph)
    st = w.start()
    assert say(w, st, "talk", "l'ours")["changes"] == ["te suit : Ours"]
    say(w, st, "go", "est")
    assert st.chars_at["ours"] == "b" and "Ours" in w.describe(st)["personnages"]
    w.apply(st, ["move:ours:a"])
    say(w, st, "go", "ouest")
    say(w, st, "go", "est")
    assert st.chars_at["ours"] == "a"


def test_the_strongest_hint_for_a_riddle_gives_the_answer():
    graph, _ = PATTERNS["riddle"]
    w = World(graph)
    st = w.start()
    st.stalled, st.hint_level = 99, 2
    assert w.hint(st, force=True) == "Réponds : « peigne »."


def test_a_flag_set_by_a_riddle_counts_as_reachable():
    graph, _ = PATTERNS["riddle"]
    g = json.loads(json.dumps(graph))
    g["items"][0]["answer"]["effects"] = ["flag:devine"]
    g["actions"][0]["when"].append("flag:devine")
    errors, report = check(g)
    assert [e for e in errors if not e.startswith("facile:")] == [] and report["wins"]
