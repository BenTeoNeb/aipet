"""The hand-written adventures played as a child would (21/11/2026 play-tests): natural sentences,
wrong tries, imagination, help, a loss and a retry. Each turn checks what the engine answers."""

import asyncio

import pytest

from aipet.games import adventure as adv
from aipet.games.common import Host

G = lambda to: {"verb": "go", "to": to}  # noqa: E731
A = lambda verb, obj="", tgt="": {"verb": verb, "object": obj, "target": tgt}  # noqa: E731
RETRY = "retry from just before the mistake"


class Kid(Host):
    skills = type("S", (), {"active": "adventure"})()

    def player(self):
        return "Zina"


def play(tmp_path, monkeypatch, title, turns):
    graph = next(g for g in adv.library() if g["title"] == title)
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [graph])

    def offline(*a, **k):
        raise ValueError("offline")

    monkeypatch.setattr(adv, "generate_adventure", offline)
    game = adv.AdventureGame({}, tmp_path, Kid())
    asyncio.run(game.start("", graph["difficulty"]))
    for said, intents, expected in turns:
        if intents == RETRY:
            assert "juste avant" in asyncio.run(game.start(from_checkpoint=True))["note"], said
            continue
        out = game.act(intents, said=said)
        got = [r["outcome"] for r in out["results"]]
        assert got == expected, (said, got, out)
    return game


def test_le_bonhomme_de_neige(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le bonhomme de neige de Plumette", [
        ("Bonjour Plumette !", [A("talk", "Plumette")], ["done"]),
        ("Je mets mon doigt pour faire le nez", [A("use", "mon doigt", "le bonhomme")], ["absent"]),  # Flocon plays along
        ("Je vais chez Mamie", [G("chez Mamie")], ["done"]),
        ("Je demande une carotte à Mamie", [A("ask", "Mamie", "une carotte")], ["not_yet"]),
        ("Je ferme la fenêtre", [A("close", "la fenêtre")], ["done"]),
        ("Je prends la carotte et je retourne dehors", [A("take", "la carotte"), G("dehors")], ["done", "done"]),
        ("Je mets la carotte sur le bonhomme", [A("use", "la carotte", "le bonhomme")], ["done"]),
        ("Je vais dans la cabane", [G("la cabane")], ["done"]),
        ("Je saute pour attraper le chapeau", [A("jump", "le chapeau")], ["free"]),
        ("Je prends le chapeau", [A("take", "le chapeau")], ["not_yet"]),  # seen on its hook: not « absent »
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je vais dans la remise", [G("dehors"), G("la remise")], ["done", "done"]),
        ("Je mets le seau sur ma tête", [A("wear", "le seau")], ["free"]),
        ("Je prends le tabouret", [A("take", "le tabouret")], ["done"]),
        ("Je retourne à la cabane", [G("jardin"), G("cabane")], ["done", "done"]),
        ("Je monte sur le tabouret", [A("climb", "le tabouret")], ["done"]),
        ("Je prends le chapeau et je le mets au bonhomme",
         [A("take", "chapeau"), G("jardin"), A("use", "chapeau", "bonhomme")], ["done", "done", "end"]),
    ])  # fmt: skip
    assert game.state.ending == "fete"


def test_le_petit_train_de_tutu(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le petit train de Tutu", [
        ("Bonjour Tutu, qu'est-ce qui se passe ?", [A("talk", "Tutu")], ["done"]),
        ("Je vais au château d'eau", [G("le château d'eau")], ["done"]),
        ("Je prends le sifflet", [A("take", "le sifflet")], ["not_yet"]),  # in the magpie's nest
        ("Chipie, rends le sifflet !", [A("talk", "la pie", "le sifflet")], ["done"]),
        ("Je lui donne mon doudou", [A("give", "mon doudou", "Chipie")], ["absent"]),  # Flocon plays along
        ("Je vais dans la salle d'attente", [G("gare"), G("la salle d'attente")], ["done", "done"]),
        ("Je fouille les poches du manteau", [A("search", "les poches")], ["done"]),
        ("Je prends le bouton et le pain", [A("take", "le bouton"), A("take", "le pain")], ["done", "done"]),
        ("Je donne le bouton brillant à la pie",
         [G("gare"), G("nord"), A("give", "le bouton", "la pie")], ["done", "done", "done"]),
        ("Je vais dans la forêt et je prends une bûche",
         [G("gare"), G("la forêt"), A("take", "une bûche")], ["done", "done", "not_yet"]),
        ("Je réveille l'ours", [A("reveiller", "l'ours")], ["done"]),  # he mumbles « miel… tartine… »
        ("Je vais chez la taupe", [G("gare"), G("chez la taupe")], ["done", "done"]),  # where she is
        ("Je tire le levier", [A("pull", "le levier")], ["done"]),
        ("Je prends le miel et je le mets sur le pain",
         [A("take", "le miel"), A("use", "le miel", "le pain")], ["done", "done"]),
        ("Je mange la tartine", [A("eat", "la tartine")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("eat", "la tartine")], ["end"]),
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je donne la tartine à l'ours",
         [G("gare"), G("forêt"), A("give", "la tartine", "le gros ours")], ["done", "done", "done"]),
        ("Je mets le bois dans la chaudière", [G("gare"), A("use", "le bois", "la chaudière")], ["done", "done"]),
        ("Tuuut !", [A("souffler", "le sifflet")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "depart" and [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_raya_et_la_perle_de_lumiere(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Raya et la perle de lumière", [
        ("Raya, qu'est-ce qu'il faut faire ?", [A("talk", "Raya")], ["done"]),
        ("Je vais dans la plaine et je descends dans le trou", [G("la plaine de sable"), G("en bas")], ["done", "not_yet"]),
        ("Je parle à l'anguille", [A("talk", "l'anguille")], ["done"]),
        ("Je vais à l'épave", [G("le récif"), G("l'épave")], ["done", "done"]),
        ("Je prends le bocal", [A("take", "le bocal")], ["done"]),
        ("Je tourne le gouvernail", [A("other", "le gouvernail")], ["free"]),
        ("Je fouille le filet et j'ouvre la boîte", [A("search", "le filet"), A("open", "la boîte")], ["done", "done"]),
        ("Je prends tout", [A("take", "la ficelle"), A("take", "les miettes")], ["done", "done"]),
        ("Je vais dans la plaine et je creuse dans le sable",
         [G("récif"), G("plaine"), A("creuser", "le sable")], ["done", "done", "done"]),
        ("Je fais un collier", [A("take", "les coquillages"), A("combine", "la ficelle", "les coquillages")], ["done", "done"]),
        ("Je donne le collier à Zigzag", [A("give", "le collier", "Zigzag")], ["done"]),
        ("Je descends dans la fosse", [G("la fosse")], ["done"]),
        ("J'allume la lumière", [A("light", "la fosse")], ["not_yet"]),  # Flocon: light up here → « il te faut une lumière »
        ("Aide-moi", [A("help")], ["hint"]),
        ("Je vais dans la forêt d'algues et je prends l'algue qui brille",
         [G("haut"), G("récif"), G("la forêt d'algues"), A("take", "l'algue qui brille")], ["done"] * 4),
        ("Je mets l'algue dans le bocal", [A("use", "l'algue", "le bocal")], ["done"]),
        ("Je vais dans la grotte", [G("la grotte")], ["done"]),
        ("Je cherche partout", [A("search")], ["done"]),  # Octave is found
        ("Je retourne à la fosse", [G("algues"), G("récif"), G("plaine"), G("fosse")], ["done"] * 4),
        ("Je lève la lampe", [A("use", "la lampe")], ["done"]),
        ("Je prends la perle et je donne les miettes aux poissons",
         [A("take", "la perle"), A("give", "les miettes", "les poissons")], ["done", "done"]),
        ("Je vais vers la lueur et je souffle dans la conque", [G("la lueur dorée"), A("use", "la conque")], ["done", "done"]),
        ("Je donne la perle à Madame Bénitier", [A("give", "la perle", "Madame Bénitier")], ["clue"]),  # a clue
        ("Je dis à Madame Bénitier que j'ai des amis qui brillent",
         [A("talk", "Madame Bénitier", "mes amis qui brillent")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "poissons"
    assert "miettes" not in game.state.inventory and game.state.turns == 39


@pytest.mark.parametrize("graph", adv.library(), ids=lambda g: g["title"])
def test_every_object_shown_or_out_of_reach_is_never_absent(graph):
    """What a place description shows is either here, or « out of reach » until its action."""
    from aipet.games.adventure_engine import World, _norm, solve

    w = World(graph)
    for path in solve(w)["win_paths"].values():
        st = w.start()
        for kind, ident in path:
            desc = f" {_norm(w.describe(st)['description'])} "
            for i, item in w.items.items():
                if st.items_at.get(i) == "" and f" {_norm(item['name'])} " in desc:  # named as such
                    res = w.act(st, {"verb": "take", "object": item["name"]})
                    assert res["outcome"] != "absent", (graph["title"], st.place, item["name"], res)
            if kind == "go":
                w.act(st, G(w.name(ident)))
            elif kind == "take":
                w.act(st, A("take", w.name(ident)))
            else:
                a = next(x for x in w.rules if x["id"] == ident)
                w.act(st, A(a["verb"], w.name(a.get("object", "")), w.name(a["target"]) if a.get("target") else ""))
