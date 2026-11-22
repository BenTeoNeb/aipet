import json

import pytest

from aipet.games import adventure_viz as viz
from aipet.games.adventure import library
from aipet.games.adventure_engine import World

PIKO = next(g for g in library() if g["title"] == "Le phare de Piko")


def test_every_ending_of_the_bank_has_a_road_that_reaches_it():
    for g in library():
        w = World(g)
        ex = viz.explore(w)
        assert ex["complete"], g["title"]
        for e in g["endings"]:
            road = viz.walk(w, ex["roads"][e["id"]])
            assert road[-1]["state"]["ending"] == e["id"], (g["title"], e["id"])


def test_explore_sorts_the_actions():
    ex = viz.explore(World(PIKO))
    assert "embeter_crabe" in ex["losing"]
    assert "nourrir_crabe" in ex["on_win"]
    assert "donner_lucioles" not in ex["fired"]  # a clue: changes nothing, the solver never does it
    assert set(ex["reached"]) == {p["id"] for p in PIKO["places"]}


def test_the_chain_leads_every_ending_back_to_the_start():
    for g in library():
        ch = viz.chain(World(g))
        into: dict[str, list[str]] = {}
        for e in ch["edges"]:
            into.setdefault(e["to"], []).append(e["from"])
        for e in g["endings"]:
            seen, todo = set(), [f"end:{e['id']}"]
            while todo:
                n = todo.pop()
                if n not in seen:
                    seen.add(n)
                    todo += into.get(n, [])
            assert "start" in seen, (g["title"], e["id"])


def test_the_chain_of_piko():
    ch = viz.chain(World(PIKO))
    edges = {(e["from"], e["to"]): e["alt"] for e in ch["edges"]}
    assert ("act:nourrir_crabe", "take:cle") in edges  # the key appears when the crab eats
    assert ("take:cle", "act:ouvrir_porte") in edges
    assert ("act:embeter_crabe", "end:pince") in edges
    assert edges[("act:fouiller_tiroir", "take:allumettes")]  # version 1: two actions, two ways


def test_the_page_shows_the_model_as_it_is():
    ref = json.loads(viz.MODEL_DIR.joinpath("le-phare-de-piko.json").read_text(encoding="utf-8"))
    d = viz.describe(ref, "étalon", [], [])
    assert d["model"] == 2 and d["graph"] is not None
    assert d["labels"]["allumer_lampe"] == "light / use / give la grande lampe du phare (+2)"
    assert d["places_of"]["lucioles_dans_lampe"] == "sommet"  # where the lamp is, not the jar
    assert d["places_of"]["attraper_lucioles"] == "rochers"
    nodes = {n["id"] for n in d["chain"]["nodes"]}
    assert {"open:porte", "open:tiroir"} <= nodes
    edges = {(e["from"], e["to"]) for e in d["chain"]["edges"]}
    assert ("take:cle", "open:porte") in edges and ("open:porte", "reach:sommet") in edges
    assert ("open:tiroir", "take:allumettes") in edges and ("code:tiroir", "open:tiroir") in edges
    assert {s["kind"] for s in d["roads"]["feu"]} >= {"press", "act"}
    v1 = viz.describe(PIKO, "banque", [], [])
    assert v1["model"] == 1 and all(len(a["triggers"]) == 1 for a in v1["graph"]["actions"])


def test_a_thing_only_some_triggers_need_is_another_way():
    graph = {
        "title": "t", "difficulty": "facile", "start": {"place": "a", "inventory": []}, "format": 2,
        "places": [{"id": "a", "name": "a", "exits": []}],
        "items": [{"id": "x", "name": "x", "at": ""}, {"id": "y", "name": "y", "at": ""},
                  {"id": "z", "name": "z", "at": "a", "takeable": False}],
        "characters": [],
        "actions": [
            {"id": "rx", "effects": ["reveal:x"], "triggers": [{"verb": "search", "object": "z"}]},
            {"id": "ry", "effects": ["reveal:y"], "triggers": [{"verb": "pull", "object": "z"}]},
            {"id": "u1", "effects": ["end:w"], "triggers": [
                {"verb": "use", "object": "x", "target": "z"}, {"verb": "use", "object": "y", "target": "z"}]}],
        "endings": [{"id": "w", "kind": "win", "text": ""}],
    }  # fmt: skip
    ch = viz.chain(World(graph))
    edges = {(e["from"], e["to"]): e["alt"] for e in ch["edges"]}
    assert edges[("act:rx", "act:u1")] and edges[("act:ry", "act:u1")]  # with x or with y
    assert not edges[("act:u1", "end:w")]


def _log(path, events):
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")


def test_played_games_are_replayed_through_the_engine(tmp_path):
    def call(name, args, result):
        return [{"type": "tool_call", "name": name, "arguments": args},
                {"type": "tool_result", "name": name, "result": result}]  # fmt: skip

    _log(tmp_path / "2026-11-21_100000_web.jsonl", [
        *call("adventure_start", {"title": PIKO["title"]}, json.dumps({"title": PIKO["title"]})),
        *call("adventure_act", {"actions": [{"verb": "go", "to": "rochers"}], "said": "je vais aux rochers"},
              {"results": [{"outcome": "done"}]}),
        *call("adventure_act", {"actions": [{"verb": "hit", "object": "crabe"}], "said": "je tape le crabe"},
              {"results": [{"outcome": "done"}]}),
    ])  # fmt: skip
    games = viz.played({PIKO["title"]: World(PIKO)}, tmp_path)
    (game,) = games[PIKO["title"]]
    assert game["mode"] == "new"
    first, second = game["turns"]
    assert first["state"]["place"] == "rochers" and not first["mismatch"]
    assert second["outcomes"] in (["end"], ["warning"])  # risky: the engine may warn first
    assert second["mismatch"]  # logged « done », the engine says otherwise


def test_render_embeds_the_data():
    page = viz.render({"adventures": [], "bank": "x</script>"})
    assert "let DATA = {" in page
    assert "x<\\/script>" in page


@pytest.mark.parametrize("title", ["Le phare de Piko"])
def test_describe_is_json(title):
    g = next(x for x in library() if x["title"] == title)
    json.dumps(viz.describe(g, "banque", [], []), ensure_ascii=False)


def test_simulated_games_carry_the_narrator_and_its_slips(tmp_path):
    def call(name, args, result):
        return [{"type": "tool_call", "name": name, "arguments": args},
                {"type": "tool_result", "name": name, "result": result}]  # fmt: skip

    _log(tmp_path / "2026-11-22_100000_playtest.jsonl", [
        {"type": "playtest_start", "title": PIKO["title"], "child": "Zina, 7 ans", "style": "curieux"},
        *call("adventure_start", {"title": PIKO["title"]}, {"title": PIKO["title"]}),
        *call("adventure_act", {"actions": [{"verb": "go", "to": "rochers"}], "said": "aux rochers !"},
              {"results": [{"outcome": "done"}]}),
        {"type": "playtest_narrator", "text": "[joie] Te voilà aux rochers !"},
        {"type": "playtest_issue", "kind": "piste_inventee", "text": "Donne-lui une algue ?"},
    ])  # fmt: skip
    (game,) = viz.played({PIKO["title"]: World(PIKO)}, tmp_path)[PIKO["title"]]
    assert game["simulated"]["child"] == "Zina, 7 ans"
    (turn,) = game["turns"]
    assert turn["told"].endswith("aux rochers !") and turn["issues"][0]["kind"] == "piste_inventee"


def test_the_catalogue_of_patterns_is_on_the_page():
    titles = {a["title"]: a for a in viz.collect({})["adventures"]}
    code = titles["Patron · code"]
    assert code["source"] == "patron" and code["errors"] == []  # tiny on purpose: no size errors
