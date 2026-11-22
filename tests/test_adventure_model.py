"""The version 2 world model on the reference adventure (docs/adventure/model/le-phare-de-piko.json):
triggers, doors and containers, keys (Locksmith), states, composed descriptions."""

import json
from pathlib import Path

import pytest

from aipet.games.adventure_engine import World, check, hint_text, solve, upgrade

REFERENCE = Path(__file__).parent.parent / "docs" / "adventure" / "model" / "le-phare-de-piko.json"


@pytest.fixture
def w():
    return World(json.loads(REFERENCE.read_text(encoding="utf-8")))


@pytest.fixture
def plain():
    """The reference with a plain drawer (no code): opened by hand."""
    g = json.loads(REFERENCE.read_text(encoding="utf-8"))
    del next(i for i in g["items"] if i["id"] == "tiroir")["code"]
    return World(g)


def play(w, st, *intents):
    out = []
    for verb, obj, *rest in intents:
        intent = {"verb": verb, "object": obj, "target": rest[0] if rest else ""}
        if verb == "go":
            intent = {"verb": "go", "to": obj}
        out.append(w.act(st, intent))
    return out


def test_the_reference_is_valid_and_solved(w):
    errors, report = check(w.g)
    assert errors == []
    assert set(report["wins"]) == {"feu", "lucioles"} and report["loses"] == {"pince"}
    assert report["complete"] and not report.get("stuck")


def test_win_with_the_matches_in_spoken_words(w):
    st = w.start()
    *_, drawer, matches = play(w, st, ("go", "cabane"), ("push", "le coquillage bleu"), ("push", "le rose"),
                               ("push", "le coquillage blanc"), ("take", "les allumettes"))  # fmt: skip
    assert drawer["outcome"] == "done" and "clac" in drawer["text"]
    assert matches["outcome"] == "done" and "allumettes" in st.inventory
    play(w, st, ("go", "plage"), ("go", "rochers"), ("take", "une algue"), ("give", "l'algue", "au crabe"))
    play(w, st, ("take", "la clé"), ("go", "plage"), ("go", "phare"))
    up = w.act(st, {"verb": "go", "to": "en haut"})
    assert up["outcome"] == "done" and "Clic" in up["text"] and st.place == "sommet"  # unlocked on the way
    end = w.act(st, {"verb": "give", "object": "les allumettes", "target": "Papy Pic"})
    assert end["outcome"] == "end" and st.ending == "feu"


@pytest.mark.parametrize(
    "intent",
    [
        {"verb": "light", "object": "la lampe"},
        {"verb": "use", "object": "les allumettes", "target": "la lampe"},
        {"verb": "give", "object": "les allumettes", "target": "papy"},
    ],
)
def test_every_trigger_does_the_same_action(w, intent):
    st = w.start()
    st.place, st.inventory, st.items_at["allumettes"] = "sommet", ["allumettes"], "inv"
    assert w.act(st, intent)["outcome"] == "end" and st.ending == "feu"
    assert st.done_actions == ["allumer_lampe"]


def test_the_door_is_locked_until_the_key(w):
    st = w.start()
    st.place = "pied_phare"
    up = w.act(st, {"verb": "go", "to": "haut"})
    assert up["outcome"] == "not_yet" and "fermée à clé" in up["text"] and st.place == "pied_phare"
    assert w.act(st, {"verb": "open", "object": "la porte"})["outcome"] == "not_yet"
    assert any("fermée" in x for x in w.describe(st)["sorties"])
    st.inventory, st.items_at["cle"] = ["cle"], "inv"
    opened = w.act(st, {"verb": "use", "object": "la clé", "target": "la porte"})
    assert opened["outcome"] == "done" and "Clic" in opened["text"]
    assert "grande ouverte" in w.describe(st)["description"]
    assert w.act(st, {"verb": "close", "object": "la porte"})["outcome"] == "impossible"
    assert w.act(st, {"verb": "go", "to": "haut"})["outcome"] == "done" and st.place == "sommet"
    assert w.act(st, {"verb": "go", "to": "bas"})["outcome"] == "done"  # the same door, from the other side


def test_a_container_shows_what_it_holds_once_open(plain):
    w = plain
    st = w.start()
    st.place = "cabane"
    assert "une boîte d'allumettes" not in w.describe(st)["ici"]
    assert w.act(st, {"verb": "examine", "object": "le tiroir"})["dedans"] == ["fermé"]
    assert w.act(st, {"verb": "examine", "object": "les allumettes"})["text"].startswith("C'est dans le tiroir")
    assert w.act(st, {"verb": "open", "object": "le tiroir"})["changes"] == ["dedans : une boîte d'allumettes"]
    assert "une boîte d'allumettes" in w.describe(st)["ici"]
    assert "grand ouvert" in w.describe(st)["description"]


def test_taking_from_a_closed_drawer_opens_it(plain):
    st = plain.start()
    st.place = "cabane"
    took = plain.act(st, {"verb": "take", "object": "les allumettes"})
    assert took["outcome"] == "done" and "clac" in took["text"] and "allumettes" in st.inventory


@pytest.mark.parametrize("verb", ["pull", "push", "use", "search"])
def test_pulling_a_drawer_opens_it(plain, verb):
    st = plain.start()
    st.place = "cabane"
    assert plain.act(st, {"verb": verb, "object": "le tiroir"})["outcome"] == "done" and plain.prop(
        st, "tiroir", "open"
    )


def test_lighting_a_drawer_with_a_jar_opens_nothing(w):
    st = w.start()
    st.place, st.inventory, st.items_at["bocal"] = "cabane", ["bocal"], "inv"
    w.act(st, {"verb": "light", "object": "le tiroir", "target": "le bocal"})
    assert not w.prop(st, "tiroir", "open")


@pytest.mark.parametrize(
    "intent",
    [
        {"verb": "take", "object": "les lucioles"},  # « j'attrape les lucioles », the jar in the bag
        {"verb": "take", "object": "les lucioles", "target": "le bocal"},
        {"verb": "use", "object": "le bocal", "target": "les lucioles"},
        {"verb": "fill", "object": "le bocal", "target": "les lucioles"},
    ],
)
def test_the_things_named_find_the_action_whatever_the_verb(w, intent):
    st = w.start()
    st.place, st.inventory, st.items_at["bocal"] = "rochers", ["bocal"], "inv"
    assert w.act(st, intent)["outcome"] == "done" and w.prop(st, "bocal", "state") == "plein"


def test_no_remap_for_a_cuddle_a_blow_or_without_the_tool(w):
    st = w.start()
    st.place, st.inventory, st.items_at["algue"] = "rochers", ["algue"], "inv"
    w.act(st, {"verb": "touch", "object": "le crabe"})
    w.act(st, {"verb": "take", "object": "le crabe"})
    assert w.prop(st, "pinces", "state") == "grognon"
    st = w.start()
    st.place = "rochers"
    assert w.act(st, {"verb": "take", "object": "les lucioles"})["outcome"] == "impossible"  # no jar: « il faudrait… »


def test_the_crab_holds_the_key(w):
    st = w.start()
    st.place, st.inventory, st.items_at["algue"] = "rochers", ["algue"], "inv"
    assert w.describe(st)["tenu"] == ["Pinces le crabe tient une petite clé dorée"]
    took = w.act(st, {"verb": "take", "object": "la clé"})
    assert took["outcome"] == "not_yet" and "Pinces le crabe qui l'a" in took["text"]
    fed = w.act(st, {"verb": "give", "object": "l'algue", "target": "le crabe"})
    assert fed["changes"] == ["perdu : une algue verte", "Pinces le crabe lâche : une petite clé dorée"]
    assert "tenu" not in w.describe(st) and w.act(st, {"verb": "take", "object": "la clé"})["outcome"] == "done"


def test_a_door_written_once_closes_both_sides(w):
    assert w.places["sommet"]["exits"][0]["door"] == "porte" and "door" not in w.g["places"][4]["exits"][0]
    assert w.items["porte"]["at"] == "pied_phare"
    st = w.start()
    st.place = "pied_phare"
    assert "fermée" in w.describe(st)["sorties"][1]
    st.inventory, st.items_at["cle"] = ["cle"], "inv"
    assert "fermée" not in w.describe(st)["sorties"][1]  # the key is in the bag: the way is open
    assert "l'escalier du phare (haut)" in w.options(st)["sorties"]


def test_states_rename_and_redescribe(w):
    st = w.start()
    play(w, st, ("go", "cabane"))
    assert "Sur la table, un bocal vide." in w.describe(st)["description"]
    play(w, st, ("take", "le bocal"))
    assert "Sur la table" not in w.describe(st)["description"]  # handled: no initial appearance any more
    play(w, st, ("go", "plage"), ("go", "rochers"))
    assert "Des lucioles dansent" in w.describe(st)["description"]
    assert w.act(st, {"verb": "fill", "object": "le bocal", "target": "les lucioles"})["outcome"] == "done"
    assert w.name("bocal", st) == "un bocal de lucioles"
    assert "Quelques lucioles" in w.describe(st)["description"]
    assert w.act(st, {"verb": "inventory"})["inventaire"] == ["un bocal de lucioles"]
    assert w.act(st, {"verb": "fill", "object": "le bocal", "target": "les lucioles"}).get("already")


def test_a_character_changes_with_its_state(w):
    st = w.start()
    st.place, st.inventory, st.items_at["algue"] = "rochers", ["algue"], "inv"
    assert "tout grognon" in w.describe(st)["description"]
    assert w.act(st, {"verb": "give", "object": "l'algue", "target": "Pinces"})["outcome"] == "done"
    assert "grignote, tout content" in w.describe(st)["description"]
    assert "Miam" in w.act(st, {"verb": "talk", "object": "le crabe"})["dit"]


def test_an_empty_jar_is_not_yet_a_light(w):
    st = w.start()
    st.place, st.inventory, st.items_at["bocal"] = "sommet", ["bocal"], "inv"
    res = w.act(st, {"verb": "use", "object": "le bocal", "target": "la lampe"})
    assert res["outcome"] == "not_yet" and "vide" in res["text"]


def test_hitting_the_crab_warns_then_loses(w):
    st = w.start()
    st.place = "rochers"
    assert w.act(st, {"verb": "hit", "object": "le crabe"})["outcome"] == "warning"
    assert w.act(st, {"verb": "hit", "object": "le crabe"})["outcome"] == "end" and st.ending == "pince"


def test_hints_name_things_as_they_are_and_the_way_at_hand(w):
    st = w.start()
    st.place, st.inventory, st.items_at["allumettes"] = "sommet", ["allumettes"], "inv"
    assert (
        hint_text(w, ("act", "allumer_lampe"), 3, st)
        == "Essaie : utiliser une boîte d'allumettes, avec la grande lampe du phare."
    )
    assert hint_text(w, ("act", "allumer_lampe"), 2, st).startswith("Papy Pic a besoin")  # the author's hint
    st = w.start()
    st.place, st.inventory, st.items_at["bocal"] = "sommet", ["bocal"], "inv"
    w.set_prop(st, "bocal", "state", "plein")
    assert "un bocal de lucioles" in hint_text(w, ("act", "lucioles_dans_lampe"), 3, st)


def test_hints_follow_the_new_steps(w):
    st = w.start()
    st.place = "cabane"
    path = solve(w, st, first_path=True)["path"]
    assert path  # a way to win from the cabin
    st.stalled, st.hint_level = 99, 2
    assert w.hint(st, force=True)


def test_a_version_1_graph_is_upgraded_without_loss():
    v1 = {  # the bank's first format: one verb per action, booleans, states as a map
        "title": "v1", "difficulty": "facile", "start": {"place": "a", "inventory": []},
        "places": [{"id": "a", "name": "a", "description": "A.", "exits": [{"to": "b", "label": "b", "dir": "est"}]},
                   {"id": "b", "name": "b", "description": "B.", "exits": [{"to": "a", "label": "a", "dir": "ouest"}]},
                   {"id": "c", "name": "c", "description": "C.", "exits": []}],
        "items": [{"id": "algue", "name": "une algue", "at": "a", "takeable": True},
                  {"id": "pot", "name": "un pot", "at": "b", "takeable": False, "state": "vide",
                   "states": {"plein": {}, "vide": {}}}],
        "characters": [{"id": "pinces", "name": "Pinces", "at": "b", "says": "Grr"}],
        "actions": [{"id": "nourrir", "verb": "give", "object": "algue", "target": "pinces", "when": ["has:algue"],
                     "effects": ["end:gagne"], "text": "Miam"}],
        "endings": [{"id": "gagne", "kind": "win", "text": "Bravo"}],
    }  # fmt: skip
    v2 = upgrade(v1)
    assert v1["actions"][0]["verb"] == "give" and "format" not in v1  # the original is left alone
    first = v2["actions"][0]
    assert first["triggers"] == [{"verb": "give", "object": "algue", "target": "pinces"}] and "verb" not in first
    assert v2["format"] == 2 and v2["items"][1]["kind"] == "scenery" and v2["items"][1]["states"][0]["id"] == "vide"
    assert upgrade(v2) is v2
    assert solve(World(v1))["wins"] == solve(World(v2))["wins"] == {"gagne": 3}


def test_montrer_is_not_monter():
    from aipet.games.adventure_engine import canonical_verb

    assert canonical_verb("montrer") == "give" and canonical_verb("monter") == "go"


def _broken(change):
    g = json.loads(REFERENCE.read_text(encoding="utf-8"))
    change(g)
    return check(g)[0]


def test_check_finds_what_the_generator_gets_wrong():
    def dup(g):
        g["actions"].append(dict(g["actions"][1]))

    assert any("used twice" in e for e in _broken(dup))

    def twin(g):  # a scripted opening shadows the built-in one the solver counts on
        g["actions"].append({"id": "ouvrir", "triggers": [{"verb": "open", "object": "tiroir"}],
                             "when": ["has:algue"], "effects": ["open:tiroir"], "text": "", "not_yet": "Coincé"})  # fmt: skip

    assert any("opening tiroir is built in" in e for e in _broken(twin))

    def closing(g):
        g["actions"][0]["effects"].append("close:porte")

    assert any("close:porte" in e for e in _broken(closing))

    def kind(g):
        g["items"][0]["kind"] = "furniture"
        items = {i["id"]: i for i in g["items"]}
        items["bocal"]["states"][1]["colour"] = "jaune"
        items["porte"]["texts"]["opened"] = "x"

    errors = _broken(kind)
    assert (
        any("furniture" in e for e in errors)
        and any("colour" in e for e in errors)
        and any("opened" in e for e in errors)
    )

    def never(g):  # the lamp needs matches nobody can find
        next(i for i in g["items"] if i["id"] == "allumettes")["at"] = ""

    assert any("needs has:allumettes, and no effect ever makes it true" in e for e in _broken(never))


def test_check_plays_the_solvers_way_through_the_turn():
    from aipet.games.adventure_engine import World, replay

    w = World(json.loads(REFERENCE.read_text(encoding="utf-8")))
    report = check(w.g)[1]
    assert report["puzzles"] == 3 and report["warnings"] == []
    assert all(replay(w, p) == "" for p in report["win_paths"].values())
    assert "answers absent" in replay(w, [("go", "rochers"), ("act", "attraper_lucioles")])  # no jar yet


def test_the_shells_code_opens_the_drawer(w):
    st = w.start()
    st.place = "cabane"
    took = w.act(st, {"verb": "take", "object": "les allumettes"})
    assert took["outcome"] == "not_yet" and "On dirait des boutons" in took["text"]
    assert w.act(st, {"verb": "open", "object": "le tiroir"})["outcome"] == "not_yet"
    assert w.act(st, {"verb": "push", "object": "le coquillage rose"})["outcome"] == "not_yet"  # wrong: again
    assert w.act(st, {"verb": "push", "object": "le bleu"})["code"] == "1 / 3"
    assert w.act(st, {"verb": "touch", "object": "le blanc"})["outcome"] == "not_yet"  # wrong: back to zero
    for c, answer in (("bleu", "1 / 3"), ("rose", "2 / 3")):
        assert w.act(st, {"verb": "push", "object": f"le coquillage {c}"})["code"] == answer
    last = w.act(st, {"verb": "hit", "object": "le coquillage blanc"})
    assert last["changes"] == ["dedans : une boîte d'allumettes"] and w.prop(st, "tiroir", "open")
    assert w.act(st, {"verb": "push", "object": "le bleu"}).get("already")


def test_a_code_is_one_puzzle_and_hints_point_at_the_clue(w):
    report = check(w.g)[1]
    assert report["puzzles"] == 3  # the code, the crab, the lamp
    st = w.start()
    st.place = "cabane"
    assert hint_text(w, ("press", "coquillage_bleu"), 2, st).startswith("Ces coquillages")
    assert hint_text(w, ("press", "coquillage_bleu"), 3, st) == "Appuie sur le coquillage bleu."


def test_check_guards_the_codes():
    def bad(g):
        t = next(i for i in g["items"] if i["id"] == "tiroir")
        t["code"]["press"] = ["coquillage_bleu", "algue"]
        t["code"]["colour"] = "x"
        g["actions"].append({"id": "x", "triggers": [{"verb": "push", "object": "coquillage_bleu"}],
                             "effects": ["flag:x"], "text": ""})  # fmt: skip

    errors = _broken(bad)
    assert any("algue must be scenery" in e for e in errors)
    assert any("code.colour" in e for e in errors)
    assert any("buttons are built in" in e for e in errors)


@pytest.mark.parametrize("verb", ["climb", "go"])
def test_climbing_up_somewhere_is_going_there(w, verb):
    st = w.start()
    st.place, st.inventory, st.items_at["cle"] = "pied_phare", ["cle"], "inv"
    assert "comme ta clé" in w.describe(st)["description"]  # the text says what is true: the key fits
    assert w.act(st, {"verb": verb, "to": "en haut du phare"})["outcome"] == "done" and st.place == "sommet"


def test_what_a_character_says_follows_the_situation(w):
    st = w.start()
    assert "C'est Pinces le crabe qui l'a" in w.act(st, {"verb": "talk", "object": "Piko"})["dit"]
    w.set_prop(st, "pinces", "state", "content")
    assert w.act(st, {"verb": "talk", "object": "Piko"})["dit"].startswith("Pinces a lâché la clé")
    st.inventory, st.items_at["cle"] = ["cle"], "inv"
    assert w.act(st, {"verb": "talk", "object": "Piko"})["dit"].startswith("Tu as la clé")


def test_situated_lines_are_checked():
    def bad(g):
        piko = next(c for c in g["characters"] if c["id"] == "piko")
        piko["says"][0]["when"] = ["has:licorne"]
        piko["says"][-1]["when"] = ["has:cle"]

    errors = _broken(bad)
    assert any("says of piko: condition has:licorne" in e for e in errors)
    assert any("last of its says lines" in e for e in errors)


def test_the_first_hint_points_at_where_the_clue_is(w):
    st = w.start()
    st.place = "cabane"  # the shells' order is drawn on the beach
    assert hint_text(w, ("press", "coquillage_bleu"), 1, st) == (
        "Et si tu retournais voir le dessin sur le sable, du côté de la plage ? Il y a un indice."
    )
    st.place = "plage"
    assert hint_text(w, ("press", "coquillage_bleu"), 1, st) == "Regarde bien autour de toi."  # it is right here


def test_prose_that_will_lie_is_warned():
    from aipet.games.adventure_engine import _prose

    g = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _prose(World(g)) == []
    g["actions"][0]["effects"].append("move:piko:sommet")
    next(p for p in g["places"] if p["id"] == "pied_phare")["details"][1]["when"] = ["!open:porte"]
    warned = _prose(World(g))
    assert any("plage: its description names piko" in x for x in warned) is False  # piko is in his initial
    assert any("pied_phare: a text says « fermé à clé »" in x for x in warned)
    g["places"][0]["description"] += " Piko le hérisson fait les cent pas."
    assert any("plage: its description names piko" in x for x in _prose(World(g)))


def test_a_free_act_on_a_shut_thing_says_what_is_true(w):
    st = w.start()
    st.place = "pied_phare"
    door = w.act(st, {"verb": "hit", "object": "la porte"})
    assert door["outcome"] == "free" and door["à_savoir"].startswith("La porte du phare est fermée à clé")
    assert "à_savoir" in door["note"]
    st.inventory, st.items_at["cle"] = ["cle"], "inv"
    assert w.act(st, {"verb": "hit", "object": "la porte"})["à_savoir"].endswith("est dans ton sac.")
    st.place = "cabane"
    assert "On dirait des boutons" in w.act(st, {"verb": "hit", "object": "le tiroir"})["à_savoir"]
    st = w.start()
    st.place = "rochers"
    assert w.act(st, {"verb": "pull", "object": "la clé"})["à_savoir"] == "C'est Pinces le crabe qui l'a."
    st.place = "sommet"
    assert "à_savoir" not in w.act(st, {"verb": "sing", "object": "la lampe"})  # nothing shut: pure play


def test_a_drawing_can_be_wiped_out_and_the_game_goes_on(w):
    st = w.start()
    wiped = w.act(st, {"verb": "destroy", "object": "le dessin"})
    assert wiped["outcome"] == "done" and wiped["changes"] == ["disparu : le dessin sur le sable"]
    assert "coquillages" not in w.describe(st)["description"] and not st.doomed  # the code can still be found
    assert "dessin" not in " ".join(w.describe(st)["ici"])


def test_a_key_thrown_into_the_sea_ends_the_game_when_he_runs_into_it(w):
    st = w.start()
    st.place, st.inventory, st.items_at["cle"] = "rochers", ["cle"], "inv"
    st.items_at["allumettes"] = "inv"
    st.inventory.append("allumettes")
    thrown = w.act(st, {"verb": "destroy", "object": "la clé"})
    assert thrown["outcome"] == "done" and st.doomed["lost"] == "cle" and not st.ending  # not yet: he doesn't know
    assert w.act(st, {"verb": "go", "to": "plage"})["outcome"] == "done"
    assert w.act(st, {"verb": "go", "to": "phare"})["outcome"] == "done"
    up = w.act(st, {"verb": "go", "to": "haut"})  # the locked door: now he knows
    assert up["outcome"] == "end" and up["ending"] == "lose" and st.ending == "_coince"
    assert up["ending_text"].startswith("Sans une petite clé dorée")
    assert st.doomed["before"]["inventory"] == ["cle", "allumettes"]  # the « sauvegarde »: before the throw


def test_what_cannot_be_destroyed(w):
    st = w.start()
    assert w.act(st, {"verb": "destroy", "object": "Piko"})["outcome"] == "impossible"
    st.place = "sommet"
    assert "verre épais" in w.act(st, {"verb": "destroy", "object": "la lampe"})["text"]  # sturdy
    st.place = "rochers"
    assert w.act(st, {"verb": "destroy", "object": "la clé"})["outcome"] == "not_yet"  # Pinces holds it


def test_the_heros_bag(w):
    st = w.start()
    st.place = "rochers"
    assert w.act(st, {"verb": "open", "object": "mon sac"})["inventaire"] == ["rien"]
    assert w.act(st, {"verb": "use", "object": "l'algue", "target": "mon sac"})["outcome"] == "done"
    assert w.act(st, {"verb": "look", "object": "le sac"})["inventaire"] == ["une algue verte"]
    assert w.act(st, {"verb": "throw", "object": "le sac", "target": "le crabe"})["outcome"] == "impossible"


def test_where_is_told_from_what_the_hero_knows(w):
    st = w.start()
    assert "pas encore vu" in w.act(st, {"verb": "where", "object": "le crabe"})["où"]
    w.act(st, {"verb": "go", "to": "rochers"})
    assert w.act(st, {"verb": "where", "object": "le crabe"})["où"] == "Pinces le crabe est juste ici."
    w.act(st, {"verb": "go", "to": "plage"})
    assert w.act(st, {"verb": "where", "object": "le crabe"})["où"] == "Tu as vu Pinces le crabe du côté des rochers."
    assert w.act(st, {"verb": "where", "object": "la cabane"})["où"].startswith("Tu n'y es pas encore allé")
    assert (
        w.act(st, {"verb": "where", "object": "les rochers"})["où"]
        == "Pour aller vers les rochers : vers les rochers (nord)."
    )


def test_invented_details_are_kept_with_who_they_are_about(w):
    st = w.start()
    w.note(st, "il s'appelle aussi Monsieur Pinpin", "le crabe")
    w.note(st, "un château de sable au bord de l'eau")
    assert w.describe(st)["souvenirs"] == ["un château de sable au bord de l'eau"]
    st.place = "rochers"
    assert w.describe(st)["souvenirs"] == ["Pinces le crabe : il s'appelle aussi Monsieur Pinpin"]
    assert w.act(st, {"verb": "examine", "object": "le crabe"})["souvenirs"] == ["il s'appelle aussi Monsieur Pinpin"]
    assert w.describe(st)["décor"][0] == "cailloux"


def test_a_failed_try_says_how_far_the_next_hint_is(w):
    st = w.start()
    st.place = "cabane"
    res = w.act(st, {"verb": "open", "object": "le tiroir"})
    assert res["outcome"] == "not_yet" and res["essais_avant_indice"] == w.spec["hint_after"] - 1


def test_the_decor_is_there_to_play_with(w):
    st = w.start()
    st.place = "rochers"
    stone = w.act(st, {"verb": "take", "object": "un gros caillou"})
    assert stone["outcome"] == "free" and stone["à_savoir"].startswith("Il y a des cailloux ici")
    assert st.inventory == []  # never a real thing
    assert (
        w.act(st, {"verb": "take", "object": "un bâton"})["outcome"] == "absent"
    )  # not in the decor: the narrator judges


def test_giving_what_lies_here_takes_it_first(w):
    st = w.start()
    st.place = "rochers"  # the seaweed at the crab's feet, not in the bag
    fed = w.act(st, {"verb": "give", "object": "algue", "target": "pinces"})
    assert fed["outcome"] == "done" and fed["changes"][0] == "gagné : une algue verte"
    assert w.prop(st, "pinces", "state") == "content"


def test_using_the_key_alone_at_the_door_opens_it(w):
    st = w.start()
    st.place, st.inventory, st.items_at["cle"] = "pied_phare", ["cle"], "inv"
    opened = w.act(st, {"verb": "use", "object": "cle"})
    assert opened["outcome"] == "done" and w.prop(st, "porte", "open")
