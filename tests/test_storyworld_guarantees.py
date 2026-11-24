"""The guarantees at their edges (the second review of storyworld): reactions that depend on what is
held, a compound turn, a game taken off the proven ways by a destroy, a lucky guess, creative ideas,
hints that never waste, old saves. Each case is a tiny adventure built with the patterns' helpers."""

import json

from aipet.games.adventure_engine import World  # the narrator's (French) keys
from aipet.games.adventure_patterns import act, adventure, person, place, thing
from storyworld import State, validate


def errors(g):
    diags, report = validate(g)
    return [str(d) for d in diags if d.severity == "error"], report


def two_rooms(items, chars, actions, **extra):
    return adventure([place("a", ("b", "est")), place("b", ("a", "ouest"))], items, chars, actions, **extra)


def test_a_reaction_on_what_is_held_is_explored_in_every_order():
    """Opening the chest frees a butterfly only with the net in hand: opened first, it's lost."""
    g = two_rooms(
        [thing("filet", "a"), thing("papillon", ""),
         thing("coffre", "a", "container", after={"open": {"when": ["has:filet"], "effects": ["gain:papillon"], "text": "!"}})],
        [person("roi", "b")],
        [act("fin", [("give", "papillon", "roi")], ["end:gagne"], ["has:papillon"])],
    )  # fmt: skip
    assert "filet" not in World(g).eager_ok
    _, report = errors(g)
    assert report["stuck"] == [("open", "coffre")]


def test_a_compound_turn_reacts_in_the_order_things_happen():
    """« Je prends l'œuf » in the closed chest: the chest opens, then the egg is taken."""
    g = two_rooms(
        [thing("coffre", "a", "container", after={"open": {"effects": ["flag:ouvert"], "text": "pouf"}}),
         thing("oeuf", "coffre", after={"take": {"when": ["flag:ouvert"], "effects": ["flag:reveil"], "text": "réveil"}})],
        [person("roi", "b")],
        [act("fin", [("give", "oeuf", "roi")], ["end:gagne"], ["has:oeuf", "flag:reveil"])],
    )  # fmt: skip
    w = World(g)
    st = w.start()
    res = w.act(st, {"verb": "take", "object": "oeuf"})
    assert res["ensuite"] == ["pouf", "réveil"] and "reveil" in st.flags


def test_the_arrival_is_told_after_the_reaction():
    g = adventure(
        [place("a", ("b", "est")),
         place("b", ("a", "ouest"), after={"enter": {"effects": ["move:dragon:a"], "text": "Le dragon s'envole."}})],
        [thing("oeuf", "b")],
        [person("dragon", "b", initial="Un dragon dort ici."), person("roi", "a")],
        [act("fin", [("give", "oeuf", "roi")], ["end:gagne"], ["has:oeuf"])],
    )  # fmt: skip
    w = World(g)
    st = w.start()
    arrival = w.act(st, {"verb": "go", "to": "b"})["arrivée"]
    assert arrival["personnages"] == [] and "dort" not in arrival["description"]


def test_after_a_destroy_every_change_is_checked_again():
    """The first key destroyed (the second still wins), then the second traded away: doomed."""
    g = two_rooms(
        [thing("cle1", "a"), thing("cle2", "a"), thing("bonbon", "")],
        [person("roi", "b"), person("marchand", "a")],
        [act("fin1", [("give", "cle1", "roi")], ["end:gagne"], ["has:cle1"]),
         act("fin2", [("give", "cle2", "roi")], ["end:gagne"], ["has:cle2"]),
         act("troc", [("give", "cle2", "marchand")], ["lose:cle2", "gain:bonbon"], ["has:cle2"])],
    )  # fmt: skip
    w = World(g)
    st = w.start()
    for thing_ in ("cle1", "cle2"):
        w.act(st, {"verb": "take", "object": thing_})
    assert w.act(st, {"verb": "destroy", "object": "cle1"})["outcome"] == "done" and not st.doomed
    w.act(st, {"verb": "give", "object": "cle2", "target": "marchand"})
    assert st.doomed
    assert w.act(st, {"verb": "help"})["outcome"] == "end"  # no hint searched: the mission ends


def test_no_think_twice_once_the_game_is_doomed():
    g = two_rooms(
        [thing("cle1", "a"), thing("caillou", "a")],
        [person("roi", "b")],
        [act("fin1", [("give", "cle1", "roi")], ["end:gagne"], ["has:cle1"])],
    )
    w = World(g)
    st = w.start()
    assert w.act(st, {"verb": "destroy", "object": "cle1"})["outcome"] == "warning"
    w.act(st, {"verb": "destroy", "object": "cle1"})
    assert st.doomed
    assert w.act(st, {"verb": "destroy", "object": "caillou"})["outcome"] == "done"


def test_a_lucky_guess_that_leaves_the_clue_behind_is_a_stuck_state():
    """The riddle's answer guessed before its clue: the hero is sent past a one-way, the clue (also the
    chest's code) left behind."""
    g = adventure(
        [place("a", ("b", "est"), ("d", "nord")), place("b", ("a", "ouest")), place("d", ("a", "sud")), place("c")],
        [thing("dessin", "d", "scenery", description="Un peigne, puis rouge et bleu."),
         thing("coffre", "c", "container", code={"press": ["rouge", "bleu"]}), thing("rouge", "c", "scenery"),
         thing("bleu", "c", "scenery"), thing("tresor", "coffre")],
        [person("sphinx", "b", answer={"question": "J'ai des dents mais je ne mords pas ?", "accept": ["peigne"],
                                       "effects": ["goto:c"]})],
        [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        puzzles=[{"goal": "sphinx", "action": "sphinx", "needs": [], "clue_in": "dessin"},
                 {"goal": "coffre", "action": "coffre", "needs": [], "clue_in": "dessin"}],
    )  # fmt: skip
    errs, report = errors(g)
    assert report["wins"] and report.get("stuck")
    assert ("go", "d") in report["path"]  # the way to win shown is a fair one


def _pinces():
    return two_rooms(
        [thing("algue", "a"), thing("coquillage", "a"), thing("cle", "pinces")],
        [person("pinces", "a"), person("roi", "b")],
        [act("nourrir", [("give", "algue", "pinces"), ("give", "coquillage", "pinces")], ["reveal:cle"], []),
         act("fin", [("give", "cle", "roi")], ["end:gagne"], ["has:cle"])],
        puzzles=[{"goal": "Faire lâcher la clé à Pinces", "action": "nourrir", "needs": [], "clue_in": "pinces"}],
    )  # fmt: skip


def test_a_creative_idea_names_every_thing_not_already_held():
    w = World(_pinces())
    st = w.start()
    tickle = {"verb": "improvise", "step": "nourrir", "object": "pinces"}
    assert w.act(State.from_dict(st.to_dict()), tickle)["outcome"] == "free"  # the seaweed was never named
    s2 = State.from_dict(st.to_dict())
    res = w.act(s2, {"verb": "improvise", "step": "nourrir", "object": "coquillage", "target": "pinces"})
    assert res["outcome"] == "done" and "coquillage" in s2.inventory and "algue" not in s2.inventory
    w.act(st, {"verb": "take", "object": "algue"})
    assert w.act(st, tickle)["outcome"] == "done"  # the seaweed in the bag: the idea needs only the crab


def test_an_action_takes_only_what_its_trigger_names():
    w = World(_pinces())
    st = w.start()
    w.act(st, {"verb": "give", "object": "coquillage", "target": "pinces"})
    assert st.inventory == ["coquillage"] and st.items_at["algue"] == "a"


def test_a_hint_on_the_childs_tries_never_points_at_a_wasteful_step():
    g = two_rooms(
        [thing("cle1", "b"), thing("cle2", "a"), thing("bonbon", "")],
        [person("roi", "b"), person("marchand", "a")],
        [act("fin1", [("give", "cle1", "roi")], ["end:gagne"], ["has:cle1"]),
         act("fin2", [("give", "cle2", "roi")], ["end:gagne"], ["has:cle2"]),
         act("troc", [("give", "cle2", "marchand")], ["lose:cle2", "gain:bonbon"], ["has:cle2"])],
    )  # fmt: skip
    w = World(g)
    st = w.start()
    w.act(st, {"verb": "take", "object": "cle2"})
    st.tried = ["marchand"]
    assert not any(step == ("act", "troc") for step in w.hints.towards_tries(st))


def test_an_old_save_gets_the_clues_it_had_met():
    g = adventure(
        [place("a", ("b", "est")), place("b", ("a", "ouest"))],
        [thing("dessin", "a", "scenery", description="Rouge puis bleu."),
         thing("coffre", "b", "container", code={"press": ["rouge", "bleu"]}), thing("rouge", "b", "scenery"),
         thing("bleu", "b", "scenery"), thing("tresor", "coffre")],
        [],
        [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        puzzles=[{"goal": "coffre", "action": "coffre", "needs": [], "clue_in": "dessin"}],
    )  # fmt: skip
    w = World(g)
    st = w.start()
    w.act(st, {"verb": "go", "to": "b"})
    saved = json.loads(json.dumps(st.to_dict()))
    saved.pop("known")
    assert w.restore(saved).known == ["dessin"]


def test_the_narrator_knows_where_the_story_stands():
    w = World(_pinces())
    st = w.start()
    assert w.describe(st)["progression"] == {"résolues": 0, "sur": 1, "acte": "début"}
    w.act(st, {"verb": "give", "object": "algue", "target": "pinces"})
    assert w.describe(st)["progression"]["acte"] == "finale"


def test_a_persona_that_gives_an_answer_away_is_reported():
    g = adventure(
        [place("a")],
        [thing("tresor", "")],
        [person("sphinx", "a", answer={"question": "J'ai des dents mais je ne mords pas ?", "accept": ["peigne"],
                                       "effects": ["reveal:tresor"]})],
        [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        puzzles=[{"goal": "sphinx", "action": "sphinx", "needs": [], "clue_in": "sphinx"}],
    )  # fmt: skip
    g["characters"][0]["persona"] = "Parle en rimes, se coiffe sans arrêt avec son peigne d'or."
    _, report = errors(g)
    assert any("persona of sphinx" in x for x in report["warnings"])
    g["characters"][0]["persona"] = "Parle en rimes, ronronne entre deux questions."
    _, report = errors(g)
    assert not any("persona" in x for x in report["warnings"])
