"""Adventures: a world graph run by code, told by the pet (docs/adventure/design.md)."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from aipet.games import adventure as adv
from aipet.games.adventure_engine import DIFFICULTIES, World, canonical_verb, check, solve, useful_steps
from aipet.games.common import Host
from aipet.memory import Memory

LIB = {g["title"]: g for g in adv.library()}
PHARE, CARILLON = LIB["Le phare de Piko"], LIB["Le carillon de la forêt"]


def play(world, st, *intents):
    return [world.act(st, i) for i in intents]


def at(world, place, inventory=()):
    st = world.start()
    st.place = place
    for i in inventory:
        st.inventory.append(i)
        st.items_at[i] = "inv"
    return st


# ---- the hand-written library -------------------------------------------------------------------
def test_hand_written_adventures_for_every_difficulty():
    assert {g["difficulty"] for g in LIB.values()} == set(DIFFICULTIES) == {"facile", "moyen", "difficile"}


@pytest.mark.parametrize("graph", adv.library(), ids=lambda g: g["title"])
def test_library_adventures_are_valid(graph):
    errors, report = check(graph)
    assert errors == []
    assert 1 <= len(report["wins"]) <= 3 and not report.get("stuck")


@pytest.mark.parametrize("graph", adv.library(), ids=lambda g: g["title"])
def test_every_way_to_win_can_be_played_with_spoken_words(graph):
    """Each solver path, replayed through the engine with names (« la petite clé dorée »), not ids:
    what the checker accepts must also work in play."""
    w = World(graph)
    for ending, path in solve(w)["win_paths"].items():
        st = w.start()
        for kind, ident in path:
            if kind == "go":
                intent = {"verb": "go", "to": w.name(ident)}
            elif kind == "take":
                intent = {"verb": "prendre", "object": w.name(ident)}
            else:
                a = next(x for x in w.rules if x["id"] == ident)
                intent = {"verb": a["verb"], "object": w.name(a.get("object", "")),
                          "target": w.name(a.get("target", "")) if a.get("target") else ""}  # fmt: skip
            res = w.act(st, intent)
            assert res["outcome"] in ("done", "end"), (ending, kind, ident, res)
        assert st.ending == ending


# ---- the solver ---------------------------------------------------------------------------------
LEVER = {
    "title": "Le levier", "difficulty": "facile", "intro": ".", "goal": ".",
    "start": {"place": "a", "inventory": []},
    "places": [
        {"id": "a", "name": "la salle A", "description": ".", "exits": [{"to": "c", "label": "vers C", "dir": "est"}]},
        {"id": "c", "name": "la salle C", "description": ".", "exits": [{"to": "a", "label": "vers A", "dir": "ouest"}]},
        {"id": "b", "name": "la salle B", "description": ".", "exits": []},
    ],
    "items": [
        {"id": "cle", "name": "une clé", "description": ".", "at": "a", "takeable": True},
        {"id": "levier", "name": "un levier", "description": ".", "at": "a", "takeable": False},
        {"id": "coffre", "name": "un coffre", "description": ".", "at": "b", "takeable": False},
    ],
    "characters": [],
    "actions": [
        {"id": "tirer", "verb": "pull", "object": "levier", "effects": ["goto:b"], "text": "."},
        {"id": "ouvrir", "verb": "open", "object": "coffre", "when": ["has:cle"], "effects": ["end:w"], "text": "."},
    ],
    "endings": [{"id": "w", "kind": "win", "text": "."}],
}  # fmt: skip


def test_solver_sees_a_one_way_trap_even_with_an_object_left_behind():
    """Pulling the lever before taking the key leaves the hero in B with no key and no exit."""
    errors, _ = check(LEVER)
    assert any("stuck" in e and "act:tirer" in e for e in errors)


def test_solver_finds_a_wasted_key():
    """Like the two doublons of Port aux Singes: a key that can be wasted blocks the game."""
    g = copy.deepcopy(PHARE)
    g["actions"].append(
        {"id": "manger_algue", "verb": "eat", "object": "algue", "effects": ["lose:algue"], "text": "Miam."}
    )
    g["actions"] = [a for a in g["actions"] if a["id"] not in ("attraper_lucioles", "lucioles_dans_lampe")]
    g["endings"] = [e for e in g["endings"] if e["id"] != "lucioles"]
    assert any("stuck" in e and "manger_algue" in e for e in check(g)[0])


def test_checker_rejects_broken_worlds():
    def broken(change):
        g = copy.deepcopy(PHARE)
        change(g)
        return " | ".join(check(g)[0])

    assert "unknown place lune" in broken(
        lambda g: g["places"][0]["exits"].append({"to": "lune", "label": "vers la lune", "dir": "haut"})
    )
    assert "between 1 and 3 win endings" in broken(
        lambda g: g["endings"].extend({"id": f"w{i}", "kind": "win", "text": "."} for i in range(3))
    )
    assert "no win ending" in broken(
        lambda g: [a["effects"].remove("reveal:cle") for a in g["actions"] if "reveal:cle" in a["effects"]]
    )
    assert "condition 'have:cle'" in broken(lambda g: g["actions"][5]["when"].append("have:cle"))
    assert "effect 'win'" in broken(lambda g: g["actions"][0]["effects"].append("win"))
    assert "built in" in broken(
        lambda g: g["actions"].append({"id": "x", "verb": "take", "object": "algue", "effects": [], "text": "."})
    )
    g = copy.deepcopy(CARILLON)
    g["endings"] = [e for e in g["endings"] if e["kind"] == "win"]
    g["actions"] = [a for a in g["actions"] if not any(e in ("end:dodo", "end:emporte") for e in a["effects"])]
    assert any("1 to 2 lose endings" in e for e in check(g)[0])  # losing is part of moyen


# ---- permissive understanding -------------------------------------------------------------------
def test_the_engine_never_guesses_words():
    """The narrator understands the child and names things by id or exact name: the engine takes
    a canonical verb (or one of its listed French words), and things by id, name or alias, the
    articles aside, singular or plural alike; nothing else."""
    assert canonical_verb("give") == "give" and canonical_verb("donner") == "give" and canonical_verb("tape") == "hit"
    assert canonical_verb("donne") == canonical_verb("partager") == canonical_verb("sentier") == "other"
    w = World(PHARE)
    st = at(w, "rochers")
    assert w._find("pinces", st) == ("pinces", [])  # the id
    assert w._find("le crabe", st) == ("pinces", []) and w._find("les algues", st)[0] == "algue"
    assert w._find("le gros crabe", st) == (None, []) and w._find("or", at(w, "pied_phare")) == (None, [])


def test_children_speak_like_children():
    w = World(PHARE)
    st = w.start()
    talk, north, take, gift, key = play(
        w, st,
        {"verb": "parler", "object": "le hérisson"},
        {"verb": "go", "to": "au nord"},
        {"verb": "take", "object": "les algues"},
        {"verb": "give", "object": "algue", "target": "le gros crabe rouge"},  # an alias
        {"verb": "take", "object": "la clef"},  # an alias
    )  # fmt: skip
    assert "Pinces" in talk["dit"] and north["arrivée"]["lieu"] == "les rochers"
    assert gift["outcome"] == "done" and "clé dorée" in gift["text"] and key["outcome"] == "done"


def test_landmarks_and_directions_from_a_crossroads():
    w = World(PHARE)
    for to, place in [("vers la cabane", "cabane"), ("vers le phare", "pied_phare"), ("à l'est", "cabane"),
                      ("ouest", "pied_phare"), ("la maison", "cabane"), ("cabane", "cabane"), ("les rochers", "rochers")]:  # fmt: skip
        st = w.start()
        assert w.act(st, {"verb": "go", "to": to})["outcome"] == "done" and st.place == place, to


def test_natural_ways_to_say_the_right_move():
    w = World(PHARE)
    for intent in [
        {"verb": "open", "object": "la porte"},
        {"verb": "open", "object": "la porte", "target": "la clé"},  # « ouvrir la porte avec la clé »
        {"verb": "use", "object": "la clé", "target": "la porte"},
        {"verb": "use", "object": "la clé"},  # at the door
    ]:
        st = at(w, "pied_phare", ["cle"])
        assert w.act(st, intent)["outcome"] == "done" and "porte_ouverte" in st.flags, intent


def test_losing_takes_the_exact_verb():
    w = World(PHARE)
    for verb in ("push", "use", "read", "throw", "wear"):
        st = at(w, "rochers")
        assert w.act(st, {"verb": verb, "object": "crabe"}).get("ending") != "lose", verb
    st = at(w, "rochers")
    assert w.act(st, {"verb": "hit", "object": "crabe"})["outcome"] == "warning"  # announced once
    assert w.act(st, {"verb": "hit", "object": "crabe"})["ending"] == "lose"  # he insists: it happens


def test_the_childs_verb_wins_over_the_order_of_the_script():
    g = copy.deepcopy(PHARE)
    g["actions"].insert(0, {"id": "lancer_algue", "verb": "throw", "object": "algue", "target": "pinces",
                            "effects": ["end:pince"], "text": "Pan, sur le nez !"})  # fmt: skip
    w = World(g)
    st = at(w, "rochers", ["algue"])
    assert w.act(st, {"verb": "give", "object": "algue", "target": "crabe"})["outcome"] == "done" and not st.ending


def test_imagination_and_talk_never_answer_absent():
    w = World(PHARE)
    st = w.start()
    assert w.act(st, {"verb": "sing", "object": "une chanson"})["outcome"] == "free"
    assert w.act(st, {"verb": "look", "object": "le ciel"})["outcome"] == "free"
    assert "dit" in w.act(st, {"verb": "talk", "object": "Piko", "target": "bonjour"})
    ask = w.act(st, {"verb": "ask", "object": "Piko", "target": "la clé"})
    assert "dit" in ask and "ailleurs" not in json.dumps(ask, ensure_ascii=False)  # no hint of a hidden key
    absent = w.act(st, {"verb": "take", "object": "une voiture"})
    assert absent["outcome"] == "absent" and "ailleurs" not in absent["note"]  # Flocon decides how to say it
    assert st.inventory == [] and st.flags == []  # imagination changes nothing


def test_things_before_the_place_itself():
    w = World(PHARE)
    st = at(w, "sommet", ["allumettes"])
    assert w.act(st, {"verb": "light", "object": "la lampe"})["outcome"] == "end"
    assert w.act(at(w, "rochers"), {"verb": "search", "object": "rochers"})["outcome"] == "done"  # the place itself


def test_the_hero_keeps_what_he_carries():
    w = World(PHARE)
    st = at(w, "plage", ["algue"])
    assert w.act(st, {"verb": "drop", "object": "algue"})["outcome"] == "impossible" and "algue" in st.inventory


# ---- hints --------------------------------------------------------------------------------------
def test_wrong_timing_gives_a_clue_and_hints_get_stronger():
    w = World(PHARE)
    st = w.start()
    play(w, st, {"verb": "go", "to": "ouest"})
    door = w.act(st, {"verb": "ouvrir", "object": "la porte"})
    assert door["outcome"] == "not_yet" and "clé" in door["text"]
    w.act(st, {"verb": "go", "to": "haut"})
    w.act(st, {"verb": "talk", "object": "la porte"})  # a chat: not a failed try
    assert not w.hint(st)
    w.act(st, {"verb": "take", "object": "la porte"})
    assert st.stalled >= DIFFICULTIES["facile"]["hint_after"] and w.hint(st)
    st.hint_level = 0
    levels = [w.hint(st, force=True) for _ in range(3)]
    assert len(set(levels)) == 3


def test_hints_skip_useless_pickups_and_speak_french():
    w = World(PHARE)
    st = at(w, "rochers", ["algue"])
    st.hint_level = 2
    assert w.hint(st, force=True) == "Essaie : donner une algue verte, avec Pinces le crabe."
    path = [("take", "bocal"), ("take", "algue"), ("act", "nourrir_crabe")]  # the solver took the jar on its way
    assert useful_steps(w, path) == [("take", "algue"), ("act", "nourrir_crabe")]


# ---- the activity: generation, memory, retry ----------------------------------------------------
class Kid(Host):
    def __init__(self, name="Zina"):
        self.name = name
        self.skills = type("S", (), {"active": "adventure"})()

    def player(self):
        return self.name

    def next_offer(self):
        return "si tu proposes une activité, propose d'abord un quiz"


def no_generation(monkeypatch):
    def broken(*a, **k):
        raise ValueError("no network in tests")

    monkeypatch.setattr(adv, "generate_adventure", broken)


@pytest.fixture
def game(tmp_path, monkeypatch):
    no_generation(monkeypatch)
    pinned = [PHARE, CARILLON]  # the fallbacks these tests play
    monkeypatch.setattr(
        adv, "library", lambda difficulty=None: [g for g in pinned if difficulty in (None, g["difficulty"])]
    )
    return adv.AdventureGame({}, tmp_path, Kid())


def win_intents(graph):
    w = World(graph)
    out = []
    for kind, ident in solve(w)["path"]:
        if kind == "go":
            out.append({"verb": "go", "to": w.name(ident)})
        elif kind == "take":
            out.append({"verb": "take", "object": w.name(ident)})
        else:
            a = next(x for x in w.rules if x["id"] == ident)
            out.append({"verb": a["verb"], "object": w.name(a["object"]),
                        "target": w.name(a["target"]) if a.get("target") else ""})  # fmt: skip
    return out


@pytest.mark.parametrize("difficulty", list(DIFFICULTIES))
def test_a_failed_generation_falls_back_to_the_same_difficulty(tmp_path, monkeypatch, difficulty):
    no_generation(monkeypatch)
    game = adv.AdventureGame({}, tmp_path, Kid())
    intro = asyncio.run(game.start("", difficulty))
    assert intro["difficulté"] == difficulty and LIB[intro["title"]]["difficulty"] == difficulty


def test_play_win_and_keep_one_file(game, tmp_path):
    asyncio.run(game.start("un phare", "facile"))
    assert "Piko le hérisson" in game.names() and "Aventure en cours" in game.prompt_section()
    out = game.act(win_intents(PHARE))
    assert out["results"][-1]["ending"] == "win" and "Gagné" in out["note"]
    assert game.end("elle a adoré le crabe")["propose"].endswith("un quiz")
    files = list((tmp_path / "histoires" / "aventures").glob("*.json"))
    stored = json.loads(files[0].read_text())
    assert len(files) == 1 and stored["difficulty"] == "facile" and stored["graph"]["title"] == "Le phare de Piko"
    assert stored["runs"][0]["outcome"] == "win" and stored["runs"][0]["reaction"] == "elle a adoré le crabe"
    asyncio.run(game.start("", "facile"))  # the same hand-written one again: same record, a new run
    assert len(list((tmp_path / "histoires" / "aventures").glob("*.json"))) == 1
    assert len(game.adventure["runs"]) == 2


def test_lose_retry_from_just_before_once_then_from_the_start(game):
    asyncio.run(game.start("", "facile"))
    warned = game.act([{"verb": "go", "to": "nord"}, {"verb": "taper", "object": "crabe"}])
    assert warned["results"][-1]["outcome"] == "warning"  # « attention… tu es sûr ? »
    lost = game.act([{"verb": "taper", "object": "crabe"}])
    assert lost["results"][-1]["ending"] == "lose" and "juste avant la bêtise" in lost["note"]
    assert "Perdu" in game.act([{"verb": "look"}])["note"]  # acting after the end: no crash
    again = asyncio.run(game.start(from_checkpoint=True))
    assert again["lieu"]["lieu"] == "les rochers" and not game.state.ending
    game.act([{"verb": "take", "object": "algue"}, {"verb": "go", "to": "plage"}])
    assert "error" in asyncio.run(game.start(from_checkpoint=True))  # no loss to undo: progress is kept
    assert game.state.place == "plage" and "algue" in game.state.inventory
    again = asyncio.run(game.start(replay="la dernière"))
    assert again["lieu"]["lieu"] == "la plage" and game.state.turns == 0
    assert [r["outcome"] for r in game.adventure["runs"]] == ["lose", None, None]


def test_a_loss_can_be_retried_in_a_new_session(tmp_path, monkeypatch):
    no_generation(monkeypatch)
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [PHARE])
    g1 = adv.AdventureGame({}, tmp_path, Kid())
    asyncio.run(g1.start("", "facile"))
    g1.act([{"verb": "go", "to": "nord"}, {"verb": "hit", "object": "crabe"}])
    g1.act([{"verb": "hit", "object": "crabe"}])  # « oui, je suis sûr ! »
    g1.stop()
    g2 = adv.AdventureGame({}, tmp_path, Kid())
    assert asyncio.run(g2.start(from_checkpoint=True))["lieu"]["lieu"] == "les rochers"


def test_moyen_has_three_retries_from_just_before(game):
    asyncio.run(game.start("", "moyen"))
    game.act([{"verb": "go", "to": "rivière"}, {"verb": "swim", "object": "la rivière"}])
    game.act([{"verb": "swim", "object": "la rivière"}])  # he insists (after a retry, already warned)
    for left in (2, 1, 0):
        assert f"sauvegardes restantes : {left}" in asyncio.run(game.start(from_checkpoint=True))["note"]
        game.act([{"verb": "swim", "object": "la rivière"}])
    assert "Plus de sauvegarde" in asyncio.run(game.start(from_checkpoint=True))["error"]


def test_a_lose_ending_called_lose(tmp_path, monkeypatch):
    no_generation(monkeypatch)
    g = copy.deepcopy(PHARE)
    for e in g["endings"]:
        if e["id"] == "pince":
            e["id"] = "lose"
    for a in g["actions"]:
        a["effects"] = ["end:lose" if x == "end:pince" else x for x in a["effects"]]
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [g])
    game = adv.AdventureGame({}, tmp_path, Kid())
    asyncio.run(game.start("", "facile"))
    game.act([{"verb": "go", "to": "nord"}, {"verb": "hit", "object": "crabe"}])
    game.act([{"verb": "hit", "object": "crabe"}])  # « oui, je suis sûr ! »
    assert "Perdu" in game.act([{"verb": "look"}])["note"]


def test_resume_finds_the_unfinished_one(game):
    asyncio.run(game.start("", "facile"))
    game.act([{"verb": "go", "to": "rochers"}, {"verb": "take", "object": "algue"}])
    game.stop()
    asyncio.run(game.start("", "moyen"))
    game.act([{"verb": "go", "to": "rivière"}, {"verb": "swim", "object": "la rivière"}])
    game.act([{"verb": "swim", "object": "la rivière"}])
    game.end("bof")  # a later adventure, lost: not the one to resume
    back = asyncio.run(game.start(resume=True))
    assert back["title"] == "Le phare de Piko" and back["inventaire"] == ["une algue verte"]


def test_replay_by_title_ignores_little_words(game):
    asyncio.run(game.start("", "facile"))
    game.end("")
    asyncio.run(game.start("", "moyen"))
    game.end("")
    assert asyncio.run(game.start(replay="l'aventure de la forêt"))["title"] == "Le carillon de la forêt"
    assert asyncio.run(game.start(replay="le phare"))["title"] == "Le phare de Piko"


def test_the_narrator_sees_what_can_be_resumed(game):
    asyncio.run(game.start("", "facile"))
    game.stop()
    section = game.prompt_section()
    assert "Aventures gardées" in section and "« Le phare de Piko » (facile) : pas finie" in section


def test_imagination_and_sentences_with_several_actions(game):
    asyncio.run(game.start("", "facile"))
    out = game.act([{"verb": "dance", "object": "Piko"}, {"verb": "go", "to": "nord"}])
    assert [r["outcome"] for r in out["results"]] == ["free", "done"]  # imagination doesn't stop the rest
    out = game.act([{"verb": "go", "to": "la lune"}, {"verb": "take", "object": "algue"}])
    assert [r["outcome"] for r in out["results"]] == ["no_exit"] and out["pas_fait"]
    game.act([{"verb": "look"}], remember="Piko s'appelle maintenant Piko-la-Chanson")
    assert "Piko-la-Chanson" in game.prompt_section()


def test_forgetting_deletes_the_adventures_that_mention_it(tmp_path, monkeypatch):
    no_generation(monkeypatch)
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [PHARE])
    game = adv.AdventureGame({}, tmp_path, Kid())
    asyncio.run(game.start("", "facile"))
    game.stop()
    Memory(tmp_path, "Flocon").forget("Pinces le crabe")
    assert not list((tmp_path / "histoires" / "aventures").glob("*.json"))
    assert "error" in asyncio.run(game.start(resume=True))


def test_a_broken_stored_world_is_refused(game, tmp_path):
    asyncio.run(game.start("", "facile"))
    game.stop()
    f = next((tmp_path / "histoires" / "aventures").glob("*.json"))
    data = json.loads(f.read_text())
    data["graph"]["characters"][1]["id"] = ""
    f.write_text(json.dumps(data))
    assert "abîmée" in asyncio.run(game.start(resume=True))["error"]


def test_generation_is_checked_and_repaired_once(monkeypatch):
    calls = []
    broken = copy.deepcopy(PHARE)
    broken["places"][0]["exits"].append({"to": "lune", "label": "vers la lune", "dir": "haut"})

    def fake(cfg, system, prompt, schema, **kw):
        calls.append((prompt, kw))
        return copy.deepcopy(broken if len(calls) == 1 else PHARE)

    monkeypatch.setattr(adv.engines, "complete_json", fake)
    monkeypatch.setattr(adv, "pet_intro", lambda cfg: "Flocon, un petit chat")
    monkeypatch.setattr(adv, "GENERATION_SECS", 100)
    graph = adv.generate_adventure({}, "un phare", "Zina", "facile", [], [])
    assert graph["title"] == "Le phare de Piko" and len(calls) == 2
    assert "unknown place lune" in calls[1][0]  # the solver's errors go back to the model
    assert "entre 4 et 14 étapes" in calls[0][0] and calls[0][1]["retries"] == 0
    monkeypatch.setattr(adv.engines, "complete_json", lambda *a, **k: copy.deepcopy(broken))
    with pytest.raises(ValueError):
        adv.generate_adventure({}, "un phare", "Zina", "facile", [], [])


def test_clean_maps_stray_verbs_and_empty_fields():
    g = copy.deepcopy(PHARE)
    g["actions"][0]["verb"] = "feed"
    g["actions"][0]["place"] = ""
    g["places"][1]["exits"] = []  # the way back from the rocks, forgotten
    g = adv._clean(g, "facile")
    assert g["actions"][0]["triggers"][0]["verb"] == "give" and "place" not in g["actions"][0]
    assert g["places"][1]["exits"][0]["to"] == "plage" and g["places"][1]["exits"][0]["dir"] == "sud"
    assert g["format"] == 2 and check(g)[0] == []


# ---- the bank: drawn first, generated by exception ----------------------------------------------
def bank_game(tmp_path, monkeypatch, name="Zina"):
    made = []

    def generate(cfg, idea, who, difficulty, recurring, past):
        made.append(idea)
        g = copy.deepcopy(PHARE)
        g.update(title=f"Une aventure inventée {len(made)}", difficulty=difficulty, tags=["inventee"])
        return g

    monkeypatch.setattr(adv, "generate_adventure", generate)
    return adv.AdventureGame({}, tmp_path, Kid(name)), made


def test_an_idea_is_matched_in_the_bank(tmp_path, monkeypatch):
    game, made = bank_game(tmp_path, monkeypatch)
    pick = asyncio.run(game.start("une aventure sous la mer avec une raie", "difficile"))
    assert pick["title"] == "Raya et la perle de lumière" and made == []
    pick = asyncio.run(game.start("un train", "moyen"))
    assert pick["title"] == "Le petit train de Tutu" and made == []


def test_an_idea_not_in_the_bank_is_invented_and_joins_the_bank(tmp_path, monkeypatch):
    game, made = bank_game(tmp_path, monkeypatch)
    pick = asyncio.run(game.start("des dinosaures qui font du ski", "facile"))
    assert made == ["des dinosaures qui font du ski"] and pick["title"] == "Une aventure inventée 1"
    rami, _ = bank_game(tmp_path, monkeypatch, "Rami")
    assert "Une aventure inventée 1" in [g["title"] for g in rami.bank("facile")]  # for the other children too


def test_surprise_draws_what_the_child_has_not_played_then_invents(tmp_path, monkeypatch):
    game, made = bank_game(tmp_path, monkeypatch)
    facile = {g["title"] for g in adv.library("facile")}
    seen = set()
    for _ in facile:
        seen.add(asyncio.run(game.start("surprends-moi", "facile"))["title"])
        game.end("")
    assert seen == facile and made == []  # every one of the bank, never twice
    assert asyncio.run(game.start("", "facile"))["title"] == "Une aventure inventée 1"  # then a new one


def test_a_win_tells_the_fact(game, monkeypatch):
    graph = copy.deepcopy(PHARE)
    graph["fact"] = "Le premier phare, celui d'Alexandrie, était une des sept merveilles du monde."
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [graph])
    asyncio.run(game.start("", "facile"))
    out = game.act(win_intents(PHARE))
    assert "Alexandrie" in out["le_savais_tu"] and "Le savais-tu" in out["note"]


def test_the_narrator_sees_themes_to_offer(tmp_path, monkeypatch):
    game, _ = bank_game(tmp_path, monkeypatch)
    section = game.prompt_section()
    assert "La banque d'aventures" in section  # Flocon chooses with the child's idea
    assert all(f"({d}) :" in section for d in ("facile", "moyen", "difficile"))


# ---- what the bank's authors ran into (21/11/2026) ----------------------------------------------
def test_climbing_into_something_is_not_a_move():
    """« Monter dans la barque »: Flocon sends climb (a gentle loss here); a « go » to it is no ride."""
    w = World(LIB["Le scarabée du scribe"])
    st = at(w, "nil")
    here = w.act(st, {"verb": "go", "to": "dans la barque"})
    assert here["outcome"] == "done" and "climb" in here["note"] and not st.ending
    assert w.act(st, {"verb": "climb", "object": "la barque"})["outcome"] == "warning"
    assert w.act(st, {"verb": "climb", "object": "la barque"})["ending"] == "lose"


def test_more_verbs_of_children():
    assert canonical_verb("caresser") == canonical_verb("chatouiller") == "touch"
    assert canonical_verb("peindre") == canonical_verb("peins") == canonical_verb("ecris") == "use"
    assert canonical_verb("appuie") == canonical_verb("tourner") == "push"


def test_another_way_of_doing_what_is_done():
    w = World(CARILLON)
    st = at(w, "verger")
    assert w.act(st, {"verb": "push", "object": "le pommier"})["outcome"] == "done"
    w.act(st, {"verb": "take", "object": "la pomme"})
    assert w.act(st, {"verb": "climb", "object": "le pommier"})["text"] == "C'est déjà fait."


def test_red_herrings_are_told_as_story():
    g = copy.deepcopy(PHARE)
    g["actions"].append({"id": "sentir_algue", "verb": "smell", "object": "algue", "text": "Ça sent la marée !"})
    g["actions"].append({"id": "nager", "verb": "swim", "object": "rochers", "when": ["flag:jamais"],
                         "effects": ["flag:x"], "not_yet": "Brrr, bien trop froide !", "text": "."})  # fmt: skip
    w = World(g)
    st = at(w, "rochers")
    smell = w.act(st, {"verb": "smell", "object": "algue"})
    assert smell["outcome"] == "clue" and smell["text"] == "Ça sent la marée !"
    assert w.act(st, {"verb": "swim", "object": "les rochers"})["outcome"] == "clue"
    assert st.stalled == 2 and check(g)[0] == []  # circling among red herrings still brings a hint


# ---- second review (21/11/2026) -----------------------------------------------------------------
def test_walking_to_something_here_is_never_a_ride():
    w = World(LIB["Le miroir du phare d'Alexandrie"])
    donkey = next(c for c, ch in w.chars.items() if "ane" in " ".join(ch.get("aliases", [])) or "âne" in ch["name"])
    st = at(w, w.chars[donkey]["at"])
    res = w.act(st, {"verb": "go", "to": "voir l'âne"})
    assert res["outcome"] == "done" and "juste à côté" in res["text"] and not st.ending


def test_one_shot_moves_can_happen_again():
    """A boat that goes one way (goto), the key back on the shore: the solver and the game agree."""
    g = copy.deepcopy(LEVER)
    g["places"].append(
        {"id": "ile", "name": "l'île", "description": ".", "exits": [{"to": "a", "label": "vers A", "dir": "ouest"}]}
    )
    g["places"][0]["exits"].append({"to": "ile", "label": "vers l'île", "dir": "est"})
    g["actions"][0] = {"id": "tirer", "verb": "pull", "object": "levier", "effects": ["goto:b"], "text": "."}
    g["places"][2]["exits"] = [{"to": "a", "label": "vers A", "dir": "nord"}]  # B now leads back
    w = World(g)
    st = w.start()
    w.act(st, {"verb": "pull", "object": "levier"})
    w.act(st, {"verb": "go", "to": "A"})
    w.act(st, {"verb": "take", "object": "clé"})
    assert w.act(st, {"verb": "pull", "object": "levier"})["outcome"] == "done" and st.place == "b"
    assert w.act(st, {"verb": "open", "object": "coffre"})["outcome"] == "end"


def test_tickling_works():
    w = World(LIB["Le scarabée du scribe"])
    assert any(a["verb"] == "touch" for a in w.rules)  # « chatouiller Miou » is understood (bank content)


@pytest.mark.parametrize("idea, title", [
    ("un chat", "Le scarabée du scribe"), ("un robot", "Le petit rover de la Lune"),
    ("les vikings", "Le drakkar de Sigrid"), ("sous la mer avec une raie", "Raya et la perle de lumière"),
    ("un petit lapin", None), ("une rose", None),
])  # fmt: skip
def test_idea_matching_uses_whole_words(idea, title):
    words, refused = adv.idea_words(idea)
    best = max(adv.library(), key=lambda g: adv.idea_score(words, g, refused))
    assert (best["title"] if adv.idea_score(words, best, refused) >= adv.IDEA_MATCH else None) == title


@pytest.mark.parametrize("idea", ["ce que tu veux", "au hasard", "choisis pour moi", "une histoire", "surprends-moi"])
def test_filler_is_a_surprise(idea):
    assert adv.idea_words(idea)[0] == set()


def test_refused_theme_is_never_drawn(tmp_path, monkeypatch):
    game, made = bank_game(tmp_path, monkeypatch)
    for _ in range(10):
        title = asyncio.run(game.start("pas les vikings", "moyen"))["title"]
        game.stop()
        assert title != "Le drakkar de Sigrid"


def test_an_idea_at_another_level_is_offered(tmp_path, monkeypatch):
    game, made = bank_game(tmp_path, monkeypatch)
    res = asyncio.run(game.start("les vikings", "facile"))
    assert res["proposition"] == {"title": "Le drakkar de Sigrid", "difficulté": "moyen"} and made == []
    assert asyncio.run(game.start("les vikings", "facile", new=True))["title"] == "Une aventure inventée 1"


def test_the_same_idea_resumes_instead_of_wiping(tmp_path, monkeypatch):
    game, _ = bank_game(tmp_path, monkeypatch)
    asyncio.run(game.start("un train", "moyen"))
    game.act([{"verb": "go", "to": "la salle d'attente"}])
    game.stop()
    again = asyncio.run(game.start("un train", "moyen"))
    assert "déjà commencée" in again["note"] and again["lieu"]["lieu"] == "la salle d'attente"


def test_bank_fixes_reach_children_who_played(tmp_path, monkeypatch):
    game, _ = bank_game(tmp_path, monkeypatch)
    asyncio.run(game.start("un train", "moyen"))
    game.end("")
    fixed = copy.deepcopy(LIB["Le petit train de Tutu"])
    fixed["intro"] = "Nouvelle intro."
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [fixed])
    asyncio.run(game.start(replay="le train"))
    game.state.ending = "depart"  # this run is over (an unfinished one would be resumed as it was)
    game.end("")
    assert asyncio.run(game.start("un train", "moyen"))["intro"] == "Nouvelle intro."


def test_generated_worlds_are_rechecked_and_nameless(tmp_path, monkeypatch):
    seen = {}

    def generate(cfg, idea, who, difficulty, recurring, past):
        seen["who"] = who
        g = copy.deepcopy(PHARE)
        g.update(title="Monde cassé", tags=["casse"])
        return g

    monkeypatch.setattr(adv, "generate_adventure", generate)
    game = adv.AdventureGame({}, tmp_path, Kid())
    asyncio.run(game.start("des dinosaures", "facile"))
    assert "Zina" not in seen["who"]
    f = next((tmp_path / "histoires" / "aventures").glob("*.json"))
    data = json.loads(f.read_text())
    data["graph"]["actions"][0]["effects"].append("win")  # an effect an older engine accepted
    f.write_text(json.dumps(data))
    assert "Monde cassé" not in [g["title"] for g in game.bank()]


def test_every_bank_adventure_has_keywords_and_a_fact():
    for g in adv.library():
        assert len(g.get("tags", [])) >= 4 and g.get("fact"), g["title"]


@pytest.mark.parametrize("graph", adv.library(), ids=lambda g: g["title"])
def test_the_checker_and_the_game_agree(graph):
    """Every move the solver explores, replayed through the game with spoken names, gives the same
    state (else the « never stuck » guarantee says nothing about real play)."""
    from collections import deque

    from aipet.games.adventure_engine import State, _key, _moves

    w = World(graph)
    seen, todo = set(), deque([w.start()])
    while todo and len(seen) < 1500:
        st = todo.popleft()
        k = _key(st, w)
        if k in seen or st.ending:
            continue
        seen.add(k)
        for (kind, ident), expected in _moves(w, st, reduce=True):
            play = State.from_dict(st.to_dict())
            if kind == "go":
                intent = {"verb": "go", "to": w.name(ident)}
                via = next(x for x in w.exits(st) if x["to"] == ident)
                intent = {"verb": "go", "to": via.get("label") or w.name(ident)}
            elif kind == "take":
                intent = {"verb": "take", "object": w.name(ident)}
            else:
                a = next(x for x in w.rules if x["id"] == ident)
                intent = {"verb": a["verb"], "object": w.name(a.get("object", "")),
                          "target": w.name(a["target"]) if a.get("target") else ""}  # fmt: skip
            if w.act(play, intent)["outcome"] == "warning":  # a losing move is announced once, then done
                w.act(play, intent)
            assert _key(play, w)[:6] == _key(expected, w)[:6], (graph["title"], kind, ident)
            todo.append(expected)


@pytest.mark.parametrize("graph", adv.library(), ids=lambda g: g["title"])
def test_losing_always_names_a_thing(graph):
    """« Sauter de joie » must not lose: a losing action always needs its object."""
    w = World(graph)
    for a in w.rules:
        if w.risky(a):
            assert a.get("object"), (graph["title"], a["id"])


def test_flocon_chooses_from_the_bank_by_title(tmp_path, monkeypatch):
    """The narrator understands « je veux aller chez les pharaons » better than word matching."""
    game, made = bank_game(tmp_path, monkeypatch)
    pick = asyncio.run(game.start("chez les rois d'autrefois", "facile", title="Le scarabée du scribe"))
    assert pick["title"] == "Le scarabée du scribe" and made == []
    pick = asyncio.run(game.start("des dinosaures", "facile", title="un titre qui n'existe pas"))
    assert made == ["des dinosaures"]  # an unknown title: the idea is invented


def test_looping_brings_a_reminder_then_hints(game):
    """Tries that lead nowhere: first what can be done (ways, things, characters, ideas), then hints."""
    asyncio.run(game.start("", "facile"))
    wrong = [{"verb": "go", "to": "la lune"}]  # a way that doesn't exist (imaginary things don't count)
    outs = [game.act(wrong) for _ in range(2 * DIFFICULTIES["facile"]["hint_after"])]
    reminders = [o for o in outs if "rappel" in o]
    hints = [o for o in outs if "indice" in o]
    assert len(reminders) == 1 and hints and outs.index(reminders[0]) < outs.index(hints[0])
    rappel = reminders[0]["rappel"]
    assert rappel["sorties"] and rappel["personnages"] == ["Piko le hérisson"]


# ---- session of 21/11/2026 19:04 (Zina, Le scarabée du scribe) ----------------------------------
def test_every_answer_says_where_the_hero_is():
    """Flocon told « tu redescends » without playing it, then believed Kha had left: the game now says
    where the hero really is in every answer."""
    w = World(LIB["Le scarabée du scribe"])
    st = w.start()
    assert w.act(st, {"verb": "dance"})["tu_es"] == w.name(st.place)


def test_arrival_points_to_what_is_worth_exploring():
    """The child only ever chose a direction: the arrival also lists things to look at, take, talk to."""
    w = World(LIB["Le scarabée du scribe"])
    st = w.start()
    nil = next(x["to"] for x in w.exits(st) if "nil" in x["to"])
    arrival = w.act(st, {"verb": "go", "to": w.name(nil)})["arrivée"]
    assert "une plume d'ibis" in arrival["à_explorer"] and "Sage l'ibis" in arrival["à_explorer"]


def test_imagination_is_not_being_stuck():
    """A handful of sand and imaginary pebbles brought a « you're going round in circles » reminder."""
    w = World(LIB["Le scarabée du scribe"])
    st = w.start()
    for thing in ("une poignée de sable", "des cailloux", "mon doudou", "une étoile"):
        w.act(st, {"verb": "take", "object": thing})
    assert st.stalled == 0


def test_the_generator_writes_puzzles_before_the_world_and_clean_fills_the_gaps():
    order = list(adv.GRAPH_SCHEMA["properties"])
    assert order.index("endings") < order.index("puzzles") < order.index("actions") < order.index("items")
    assert order.index("items") < order.index("places")
    ref = json.loads((Path(__file__).parent.parent / "docs/adventure/model/le-phare-de-piko.json").read_text())
    g = copy.deepcopy(ref)  # as strict structured output sends it: every field, empty when unused
    for i in g["items"]:
        for f in ("fixed", "initial", "key"):
            i.setdefault(f, "")
        i.setdefault("states", [])
        i.setdefault("texts", {"open": "", "locked": ""})
        i.pop("locked", None)
        i.setdefault("code", {"press": [], "right": "", "wrong": "", "hint": ""})
        i.setdefault("sturdy", "")
        i.setdefault("answer", {"question": "", "accept": [], "right": "", "wrong": "", "hint": "", "effects": []})
    for e in g["endings"]:
        e.setdefault("fact", "")
    for p in g["places"]:
        p.setdefault("details", [])
        p.setdefault("decor", [])
        for x in p["exits"]:
            x.setdefault("door", "")
            x.setdefault("blocked", "")
    for a in g["actions"]:
        a.setdefault("hint", "")
        for t in a["triggers"]:
            t.setdefault("target", "")
    g = adv._clean(g, "facile")
    porte = next(i for i in g["items"] if i["id"] == "porte")
    assert porte["locked"] is True and "fixed" not in next(i for i in g["items"] if i["id"] == "cle")
    assert "states" not in porte and "texts" not in next(i for i in g["items"] if i["id"] == "algue")
    assert (
        "code" not in porte
        and next(i for i in g["items"] if i["id"] == "tiroir")["code"]["press"][0] == "coquillage_bleu"
    )
    assert check(g)[0] == []


def test_the_drafts_are_offered_only_when_asked(tmp_path):
    off = adv.AdventureGame({}, tmp_path, Kid())
    on = adv.AdventureGame({"games": {"adventure_drafts": True}}, tmp_path, Kid())
    assert "Le phare de Piko (nouvelle version)" not in {g["title"] for g in off.bank()}
    titles = {g["title"] for g in on.bank()}
    assert {"Le phare de Piko (nouvelle version)", "Le phare de Piko"} <= titles  # never replaces the bank's


def _draft_game(tmp_path):
    game = adv.AdventureGame({"games": {"adventure_drafts": True}}, tmp_path, Kid())
    asyncio.run(game.start(title="Le phare de Piko (nouvelle version)", difficulty="facile"))
    return game


def test_each_win_tells_its_own_fact(tmp_path):
    game = _draft_game(tmp_path)
    st = game.state
    st.place, st.inventory, st.items_at["allumettes"] = "sommet", ["allumettes"], "inv"
    out = game.act([{"verb": "give", "object": "les allumettes", "target": "Papy Pic"}])
    assert "Alexandrie" in out["le_savais_tu"]  # the lighthouses' fact, not the fireflies'


def test_after_the_lost_key_the_sauvegarde_is_before_the_throw(tmp_path):
    game = _draft_game(tmp_path)
    st = game.state
    st.place, st.inventory, st.items_at["cle"] = "pied_phare", ["cle"], "inv"
    assert game.act([{"verb": "destroy", "object": "la clé"}])["results"][0]["outcome"] == "done"
    out = game.act([{"verb": "go", "to": "haut"}])
    assert out["results"][0]["ending"] == "lose" and "from_checkpoint" in out["note"]
    assert game.adventure["checkpoint"]["inventory"] == ["cle"]
    assert game.adventure["runs"][-1]["outcome"] == "lose"


def test_what_the_child_does_freely_is_remembered(tmp_path):
    game = _draft_game(tmp_path)
    game.act([{"verb": "other", "object": "Piko", "target": ""}], said="je t'appelle Pikouik !")
    game.act([{"verb": "take", "object": "un galet"}], said="je ramasse un galet")
    st, w = game.state, game.world
    assert st.notes["piko"] == ["il a fait : « je t'appelle Pikouik ! »"]
    assert "il a fait : « je ramasse un galet »" in w.describe(st)["souvenirs"]
    assert "Pikouik" in game.prompt_section()
