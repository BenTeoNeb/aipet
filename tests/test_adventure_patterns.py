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


def test_a_dark_place_shows_nothing_but_its_ways_until_a_light_shines():
    graph, _ = PATTERNS["light_in_the_dark"]
    w = World(graph)
    st = w.start()
    say(w, st, "take", "la lampe")
    arrival = say(w, st, "go", "est")["arrivée"]
    assert "four" in arrival["description"] and "dans_le_noir" in arrival and arrival["sens"]
    assert "tresor" not in arrival["à_portée"] and "a" in arrival["à_portée"] and "lampe" in arrival["à_portée"]
    assert say(w, st, "take", "tresor")["outcome"] == "not_yet"  # too dark, even named by its id
    assert "goutte" in say(w, st, "listen")["description"]
    assert say(w, st, "light", "la lampe")["outcome"] == "done"
    assert "tresor" in w.describe(st)["à_portée"] and say(w, st, "take", "le trésor")["outcome"] == "done"


def test_a_thing_with_uses_says_it_is_empty_and_fills_again():
    graph, _ = PATTERNS["uses_and_refill"]
    w = World(graph)
    st = w.start()
    say(w, st, "take", "l'arrosoir")
    assert say(w, st, "inventory")["inventaire"] == ["le arrosoir (encore 1 fois)"]
    assert say(w, st, "use", "l'arrosoir", "la rose")["changes"] == ["L'arrosoir est vide : plus une goutte !"]
    say(w, st, "go", "est")
    assert say(w, st, "use", "l'arrosoir", "la tulipe")["text"] == "L'arrosoir est vide : plus une goutte !"
    assert say(w, st, "fill", "l'arrosoir", "la fontaine")["changes"] == ["de nouveau plein : le arrosoir"]
    assert say(w, st, "fill", "l'arrosoir", "la fontaine").get("already")  # full: nothing to do


def test_a_lantern_burns_a_use_when_the_hero_leaves_the_dark_and_never_strands_him():
    graph, _ = PATTERNS["lantern_that_burns"]
    w = World(graph)
    st = w.start()
    say(w, st, "take", "la lanterne")
    say(w, st, "go", "ouest")
    assert "cle" in w.describe(st)["à_portée"]
    say(w, st, "take", "la clé")
    assert say(w, st, "go", "est")["changes"] == ["La lanterne s'éteint doucement…"]
    say(w, st, "go", "ouest")  # back in the dark, without light: the way back is still felt
    assert w.dark(st) and "a" in w.describe(st)["à_portée"]
    assert say(w, st, "go", "est")["outcome"] == "done" and not w.shines(st, "lanterne")


def test_the_checker_knows_darkness_and_uses():
    graph, _ = PATTERNS["light_in_the_dark"]
    g = json.loads(json.dumps(graph))
    del g["items"][0]["light"]
    g["actions"][0]["effects"].append("spend:tresor")
    errors, _ = check(g)
    assert any("no item gives light" in e for e in errors)
    assert any("spend:tresor: unknown usable tresor" in e for e in errors)


def test_in_the_dark_touching_talking_or_lighting_the_air_says_it_is_too_dark():
    graph, _ = PATTERNS["light_in_the_dark"]
    w = World(graph)
    st = w.start()
    say(w, st, "go", "est")
    assert say(w, st, "touch", "tresor")["outcome"] == "not_yet"
    assert say(w, st, "talk", "roi")["outcome"] == "not_yet"
    assert say(w, st, "light", "b")["outcome"] == "not_yet"


def _dark_world(lantern: dict, extra_actions=(), places=None):
    """A lantern in a, a treasure in the dark b, the king in a (who wants it)."""
    from aipet.games.adventure_patterns import act, adventure, person, place, thing

    return adventure(
        places or [place("a", ("b", "est")), place("b", ("a", "ouest"), dark=True)],
        [thing("lanterne", lantern.pop("at", "a"), **lantern), thing("tresor", "b")],
        [person("roi", "a")],
        [act("rendre", [("give", "tresor", "roi")], ["lose:tresor", "end:gagne"], ["has:tresor"]), *extra_actions],
    )


def test_the_checker_sees_a_treasure_lost_in_the_dark_once_the_light_is_spent():
    errors, _ = check(_dark_world({"light": True, "uses": 1}))  # seen lit, left behind, never seen again
    assert any("get stuck" in e for e in errors)


def test_the_checker_sees_a_treasure_lost_in_the_dark_once_the_light_is_given_away():
    from aipet.games.adventure_patterns import act, person, place

    g = _dark_world({"light": True}, [act("troc", [("give", "lanterne", "taupe")], ["lose:lanterne"], ["has:lanterne"])],
                    [place("a", ("b", "est")), place("b", ("a", "ouest"), ("c", "est"), dark=True), place("c", ("b", "ouest"))])  # fmt: skip
    g["characters"].append(person("taupe", "c"))
    errors, _ = check(g)
    assert any("get stuck" in e for e in errors)


def test_the_checker_sees_a_lantern_burnt_out_on_the_floor_of_the_dark():
    from aipet.games.adventure_patterns import act

    refill = act("remplir", [("fill", "lanterne", "roi")], ["refill:lanterne"], ["has:lanterne"])
    errors, _ = check(_dark_world({"light": True, "uses": 1, "at": "b"}, [refill]))
    assert any("get stuck" in e for e in errors)


def test_a_hint_says_to_take_the_light_and_where_only_counts_what_was_seen():
    w = World(_dark_world({"light": True}))
    st = w.start()
    say(w, st, "go", "est")  # blind
    say(w, st, "go", "ouest")
    assert "pas encore vu" in say(w, st, "where", "le tresor")["où"]
    st.stalled, st.hint_level = 99, 2
    assert "lanterne" in w.hint(st, force=True)


def test_in_the_dark_no_decor_and_burning_a_light_is_no_progress():
    graph, _ = PATTERNS["lantern_that_burns"]
    g = json.loads(json.dumps(graph))
    g["places"][1]["decor"] = ["cailloux"]
    w = World(g)
    st = w.start()
    say(w, st, "take", "la lanterne")
    say(w, st, "go", "ouest")
    say(w, st, "take", "la clé")
    st.stalled = 3
    say(w, st, "go", "est")  # the lantern burns out: not progress
    assert st.stalled == 3
    say(w, st, "go", "ouest")
    assert "à_savoir" not in say(w, st, "sing", "un caillou")
