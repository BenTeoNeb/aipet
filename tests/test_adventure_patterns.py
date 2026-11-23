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


def test_a_flag_typo_is_reported_and_a_never_flag_is_not():
    graph, _ = PATTERNS["riddle"]
    g = json.loads(json.dumps(graph))
    g["items"][0]["answer"]["effects"] = ["flag:devinette_resolue"]
    g["actions"][0]["when"].append("flag:devinete_resolue")
    _, report = check(g)
    assert any("did you mean devinette_resolue" in x for x in report["warnings"])
    g["actions"][0]["when"][-1] = "flag:jamais"
    _, report = check(g)
    assert not any("did you mean" in x for x in report["warnings"])


def test_a_creative_idea_plays_a_ready_step_of_the_game_masters_screen():
    graph, _ = PATTERNS["recipe_changes_a_thing"]
    w = World(graph)
    st = w.start()
    say(w, st, "go", "est")
    idea = {"verb": "improvise", "step": "ouvrir_conserve", "object": "conserve"}
    assert w.act(st, idea)["outcome"] == "free"  # not ready: no can-opener in the bag
    say(w, st, "go", "ouest")
    say(w, st, "take", "l'ouvre-boîte")
    say(w, st, "go", "est")
    screen = w.describe(st)["écran_mj"]
    assert [s["étape"] for s in screen] == ["ouvrir_conserve"]
    assert w.act(st, {"verb": "improvise", "step": "ouvrir_conserve", "object": "chat"})["outcome"] == "free"
    done = w.act(st, idea)  # « je fais sauter le couvercle avec la pointe de l'ouvre-boîte, comme un pirate »
    assert done["outcome"] == "done" and w.prop(st, "conserve", "state") == "ouverte" and "SES mots" in done["note"]


def test_a_persona_reaches_the_narrator():
    graph, _ = PATTERNS["companion"]
    g = json.loads(json.dumps(graph))
    g["characters"][0]["persona"] = "Grogne doucement, adore les câlins."
    w = World(g)
    assert w.describe(w.start())["personas"] == {"Ours": "Grogne doucement, adore les câlins."}


def test_the_solver_plays_a_code_only_once_its_clue_is_met():
    from storyworld import solve

    graph, _ = PATTERNS["code"]
    g = json.loads(json.dumps(graph))
    g["start"]["place"] = "b"  # the code's order is drawn on a wall in a
    w = World(g)
    path = solve(w)["path"]
    assert path.index(("go", "a")) < path.index(("press", "lune"))
    g["puzzles"] = []
    errors, _ = check(g)
    assert any("needs its puzzle in `puzzles`, with clue_in" in e for e in errors)


def test_the_derived_chart_finds_what_each_puzzle_needs():
    from storyworld import solve
    from storyworld.chart import derive

    graph, _ = PATTERNS["recipe_changes_a_thing"]
    w = World(graph)
    chart = derive(w, solve(w)["path"])
    assert chart["needs"] == {"ouvrir_conserve": [], "nourrir_chat": ["ouvrir_conserve"]}
    assert chart["depth"] == 2 and chart["width"] == 1
    assert check(graph)[1]["chart"]["puzzles"] == ["ouvrir_conserve", "nourrir_chat"]


def test_a_hint_goes_to_the_puzzle_the_child_is_trying():
    from aipet.games.adventure_patterns import act, adventure, person, place, thing

    g = adventure([place("a", ("b", "est"), ("c", "ouest")), place("b", ("a", "ouest")), place("c", ("a", "est"))],
                  [thing("pomme", "b"), thing("os", "c"), thing("cle", "", aliases=["clé", "cle"]),
                   thing("plume", "", aliases=["plume"])],
                  [person("cheval", "a"), person("chien", "a")],
                  [act("cheval", [("give", "pomme", "cheval")], ["lose:pomme", "reveal:cle"], ["has:pomme"]),
                   act("chien", [("give", "os", "chien")], ["lose:os", "reveal:plume"], ["has:os"]),
                   act("fin", [("combine", "cle", "plume")], ["end:gagne"], ["has:cle", "has:plume"])])  # fmt: skip
    w = World(g)
    st = w.start()
    for _ in range(3):
        say(w, st, "take", "le chien")  # he keeps trying the dog
    assert st.tried == ["chien"]
    st.hint_level = 2
    assert "c" in w.hint(st, force=True)  # the bone is to the west (c), not the apple to the east


def test_taking_the_egg_wakes_the_dragon_once_and_a_reaction_never_loses():
    graph, _ = PATTERNS["after_take"]
    w = World(graph)
    st = w.start()
    say(w, st, "go", "est")
    took = say(w, st, "take", "l'œuf")
    assert took["ensuite"] == ["Le dragon ouvre un œil… puis deux ! « Mon œuf ! »"]
    assert w.prop(st, "dragon", "state") == "reveille" and "after:take:oeuf" in st.done_actions
    g = json.loads(json.dumps(graph))
    g["endings"].append({"id": "mange", "kind": "lose", "text": "Croc."})
    g["items"][0]["after"]["take"]["effects"].append("end:mange")
    assert any("a reaction never loses" in e for e in check(g)[0])
