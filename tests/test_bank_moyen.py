"""The « moyen » bank of hand-written adventures (Moyen Âge, Vikings, Renaissance), played as a child
would: natural sentences, wrong tries, imagination, help, an object seen but out of reach, a loss and
a retry from just before. Each turn checks what the engine answers."""

from aipet.games import adventure as adv
from aipet.games.adventure_engine import World
from tests.test_adventure_playthroughs import RETRY, A, G, play


def test_le_tournoi_du_chateau(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le tournoi du château", [
        ("Bonjour Sire Bertrand !", [A("talk", "Sire Bertrand")], ["done"]),
        ("Je vais au tournoi", [G("le tournoi")], ["not_yet"]),  # no knight without his horse
        ("Je danse avec l'oie", [A("dance", "l'oie")], ["free"]),
        ("Je tire une plume à l'oie", [A("pull", "l'oie")], ["clue"]),  # she pinches: she's hungry
        ("Je vais à la cuisine", [G("la cuisine")], ["done"]),
        ("Je goûte la soupe", [A("eat", "la soupe")], ["clue"]),
        ("Je prends la pomme et le pain", [A("take", "la pomme"), A("take", "le pain")], ["done", "done"]),
        ("Je trempe le pain dans la soupe", [A("use", "le pain", "la soupe")], ["done"]),  # a soup sandwich
        ("Je retourne dans la cour et je donne le pain à l'oie",
         [G("la cour"), A("give", "le pain", "l'oie")], ["done", "done"]),
        ("Je vais à l'écurie", [G("l'écurie")], ["done"]),
        ("Je pousse Caramel", [A("push", "Caramel")], ["clue"]),
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je fouille dans le foin", [A("search", "le foin")], ["done"]),
        ("Je prends le bouclier et la perche", [A("take", "le bouclier"), A("take", "la perche")], ["done", "done"]),
        ("Je donne la pomme au cheval", [A("give", "la pomme", "le cheval")], ["done"]),
        ("Je monte dans la tour", [G("la cour"), G("la tour")], ["done", "done"]),
        ("Je prends la bannière", [A("take", "la bannière")], ["not_yet"]),  # seen, not finished yet
        ("Je donne la plume au moine", [A("give", "la plume", "le moine")], ["done"]),
        ("J'accroche la bannière à la perche", [A("attacher", "la bannière", "la perche")], ["done"]),
        ("Je descends et je vais au tournoi", [G("en bas"), G("le tournoi")], ["done", "done"]),
        ("Je souffle dans la trompette", [A("souffler", "la trompette")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("souffler", "la trompette")], ["end"]),
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je plante le drapeau", [A("use", "le drapeau")], ["done"]),
        ("Je donne le bouclier à Sire Bertrand", [A("give", "le bouclier", "Sire Bertrand")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "tournoi" and [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_le_drakkar_de_sigrid(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le drakkar de Sigrid", [
        ("Sigrid, qu'est-ce qu'on doit faire ?", [A("talk", "Sigrid")], ["done"]),
        ("Je vais nager", [A("swim")], ["clue"]),  # Sigrid holds you back: no bare « nager » loss
        ("Je monte dans le drakkar", [G("le drakkar")], ["done"]),
        ("Je caresse le corbeau", [A("other", "le corbeau")], ["free"]),
        ("Je répare la voile", [A("use", "la voile")], ["not_yet"]),  # « il faut une aiguille avec de la laine »
        ("Je descends et je vais dans la cabane", [G("sur la plage"), G("la cabane")], ["done", "done"]),
        ("Bonjour papi Olaf", [A("talk", "papi")], ["done"]),
        ("J'ouvre le coffre", [A("open", "le coffre")], ["done"]),
        ("Je prends tout",
         [A("take", "l'aiguille"), A("take", "le navet"), A("take", "le poisson")], ["done", "done", "done"]),
        ("Je mange le poisson", [A("eat", "le poisson")], ["clue"]),  # beurk: a raven would love it
        ("Je vais voir la chèvre", [G("la chèvre")], ["done"]),
        ("Je pousse la chèvre", [A("push", "la chèvre")], ["clue"]),
        ("Je prends la laine", [A("take", "la laine")], ["not_yet"]),  # Brunhilde lies on it
        ("Je donne la moufle à la chèvre", [A("give", "la moufle", "la chèvre")], ["absent"]),  # still in the chest
        ("Je retourne prendre la moufle", [G("la cabane"), A("take", "la moufle")], ["done", "done"]),
        ("Je frotte la moufle avec le poisson", [A("frotter", "la moufle", "le poisson")], ["done"]),  # silly recipe
        ("Je jette la moufle qui pue sur la chèvre",
         [G("l'enclos"), A("throw", "la moufle qui pue", "la chèvre")], ["done", "done"]),  # she jumps off the wool
        ("Je prends la pelote et je mets la laine dans l'aiguille",
         [A("take", "la pelote"), A("use", "la laine", "l'aiguille")], ["done", "done"]),
        ("Aide-moi", [A("help")], ["hint"]),
        ("Je vais sur la colline", [G("la cabane"), G("la plage"), G("la colline")], ["done"] * 3),
        ("Je cherche la pierre de soleil près du rocher", [A("search", "le rocher")], ["not_yet"]),  # read first
        ("Je lis les runes", [A("read", "les runes")], ["done"]),  # a riddle of Olaf's
        ("Les nuages !", [A("answer", "les nuages", "le rocher")], ["not_yet"]),
        ("C'est la mer !", [A("answer", "la mer", "le rocher")], ["done"]),  # « sous mon pied dort le soleil »
        ("Je creuse au pied du rocher", [A("creuser", "le rocher")], ["done"]),
        ("Je prends la pierre de soleil", [A("take", "la pierre de soleil")], ["done"]),
        ("Je regarde le ciel avec la pierre", [A("use", "la pierre de soleil")], ["clue"]),  # not at sea yet
        ("Je retourne au bateau", [G("la plage"), G("le bateau")], ["done", "done"]),
        ("Je couds la voile avec l'aiguille", [A("use", "l'aiguille enfilée", "la voile")], ["done"]),
        ("Je cherche le soleil avec la pierre de soleil", [A("use", "la pierre de soleil")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "soleil"


def test_le_carillon_de_la_foret(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le carillon de la forêt", [
        ("Bambou, qu'est-ce qui se passe ?", [A("talk", "Bambou")], ["done"]),
        ("Je vais au verger et je secoue le pommier", [G("le verger"), A("push", "le pommier")], ["done", "done"]),
        ("Je prends la pomme", [A("take", "la pomme")], ["done"]),
        ("Je vais chez Taupinette", [G("la clairière"), G("le terrier")], ["done", "done"]),
        ("Je donne la pomme à la taupe", [A("give", "la pomme", "Taupinette")], ["done"]),
        ("Je vais à la rivière", [G("la clairière"), G("la rivière")], ["done", "done"]),
        ("Je regarde sous les galets", [A("search", "les galets")], ["done"]),
        ("Je prends la pierre à étincelles et un roseau", [A("take", "la pierre à étincelles"), A("take", "le roseau")], ["done", "done"]),
        ("Je fais des trous dans le roseau avec la pierre",
         [A("use", "la pierre à étincelles", "le roseau")], ["done"]),  # silly recipe: a duck-with-a-cold pipe
        ("J'allume la lanterne avec la pierre", [A("light", "la lanterne")], ["done"]),
        ("Je vais sur le pont", [G("le vieux pont")], ["done"]),
        ("Je passe", [G("la grotte")], ["not_yet"]),  # Grognon bars the way
        ("Bonjour Grognon !", [A("talk", "Grognon")], ["done"]),  # his riddle
        ("Je lui chante une chanson", [A("sing", "Grognon")], ["clue"]),  # nice, but the riddle first
        ("Je joue du pipeau à Grognon", [A("use", "le pipeau", "Grognon")], ["clue"]),  # he laughs, hic!
        ("La lumière !", [A("answer", "la lumière", "Grognon")], ["not_yet"]),
        ("C'est le noir !", [A("answer", "le noir", "Grognon")], ["done"]),
        ("J'entre dans la grotte et je regarde avec la lanterne",
         [G("la grotte"), A("use", "la lanterne")], ["done", "done"]),
        ("Je prends le battant", [A("take", "le battant")], ["done"]),
        ("Je retourne à la clairière", [G("le pont"), G("la rivière"), G("la clairière")], ["done"] * 3),
        ("Je raccroche le battant au carillon", [A("use", "le battant", "le carillon")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "fete"


def test_l_atelier_de_leonard(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "L'atelier de Léonard", [
        ("Je regarde la maquette", [A("examine", "la maquette")], ["done"]),
        ("Je lis le plan", [A("read", "le plan")], ["clue"]),  # mirror writing
        ("Je prends le plan et la ficelle", [A("take", "le plan"), A("take", "la ficelle")], ["done", "done"]),
        ("Je dessine un oiseau sur le mur", [A("other", "un oiseau", "le mur")], ["free"]),
        ("Je vais à la cuisine", [G("la cuisine")], ["done"]),
        ("Bonjour Mamma Rosa !", [A("talk", "Mamma Rosa")], ["done"]),
        ("Je prends la farine et les graines", [A("take", "la farine"), A("take", "les graines")], ["done", "done"]),
        ("Je vais dans la cour", [G("l'atelier"), G("la cour")], ["done", "done"]),
        ("Je prends le seau", [A("take", "le seau")], ["done"]),
        ("Je mélange la farine et l'eau", [A("combine", "la farine", "l'eau")], ["done"]),
        ("Je monte dans la chambre", [G("l'atelier"), G("la chambre")], ["done", "done"]),
        ("Je lis le plan dans le miroir", [A("read", "le plan", "le miroir")], ["done"]),
        ("Redis-moi ce qui est écrit sur le plan", [A("read", "le plan")], ["clue"]),  # told again, not « déjà fait »
        ("Je prends le foulard", [A("take", "le foulard")], ["absent"]),  # under the sleeping cat
        ("Je pousse le chat", [A("push", "le chat")], ["clue"]),
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je monte sur la terrasse", [G("la terrasse")], ["done"]),
        ("Je donne les graines au pigeon", [A("give", "les graines", "le pigeon")], ["done"]),
        ("Je ramasse les plumes", [A("take", "les plumes")], ["done"]),
        ("Je redescends à l'atelier", [G("en bas"), G("l'atelier")], ["done", "done"]),
        ("Je mets les plumes sur la maquette", [A("use", "les plumes", "la maquette")], ["not_yet"]),  # glue first
        ("Je relis le plan", [A("read", "le plan")], ["clue"]),  # the instructions once more
        ("Je colle les plumes", [A("coller", "les plumes")], ["done"]),
        ("Je mets les ailes sur la maquette", [A("use", "les ailes en plumes", "la maquette")], ["done"]),
        ("Je fais voler la maquette", [A("throw", "la maquette")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "oiseau"


def test_le_tournoi_du_chateau_pomme_au_bout_de_la_perche(tmp_path, monkeypatch):
    """The silly recipe: the apple dangled at the end of the pole leads Caramel out; the pole stays useful."""
    game = play(tmp_path, monkeypatch, "Le tournoi du château", [
        ("Je vais à la cuisine et je prends la pomme", [G("la cuisine"), A("take", "la pomme")], ["done", "done"]),
        ("Je vais à l'écurie et je prends la perche",
         [G("la cour"), G("l'écurie"), A("take", "la perche")], ["done", "done", "done"]),
        ("J'accroche la pomme au bout de la perche", [A("combine", "la pomme", "la perche")], ["done"]),
        ("Je balance la pomme devant Caramel", [A("use", "la perche", "Caramel")], ["done"]),  # he follows it out
        ("Je vais au tournoi", [G("la cour"), G("le tournoi")], ["done", "done"]),
    ])  # fmt: skip
    assert game.state.place == "lice" and "perche" in game.state.inventory


def _graph(title):
    return next(g for g in adv.library() if g["title"] == title)


def test_descriptions_follow_the_story():
    """A place never describes what was taken, eaten or moved away."""
    w = World(_graph("Le tournoi du château"))
    st = w.start()
    w.act(st, {"verb": "go", "to": "l'écurie"})
    assert "rond" in w.describe(st)["description"]
    w.act(st, {"verb": "search", "object": "le foin"})
    assert "rond" not in w.describe(st)["description"]

    w = World(_graph("Le petit train de Tutu"))
    st = w.start()
    w.act(st, {"verb": "go", "to": "la salle d'attente"})
    w.act(st, {"verb": "take", "object": "le pain"})
    assert "pain" not in w.describe(st)["description"]

    w = World(_graph("Le drakkar de Sigrid"))
    st = w.start()
    w.act(st, {"verb": "go", "to": "la cabane"})
    w.act(st, {"verb": "take", "object": "le navet"})
    w.act(st, {"verb": "take", "object": "le poisson"})
    assert w.describe(st)["description"].endswith("la table est vide.")


def test_moyen_lose_endings_are_warned_and_need_a_thing():
    for title in ("Le carillon de la forêt", "Le petit train de Tutu", "Le tournoi du château",
                  "Le drakkar de Sigrid", "L'atelier de Léonard"):  # fmt: skip
        g = _graph(title)
        w = World(g)
        assert len(g["tags"]) >= 4 and g["fact"], title
        assert all(a.get("object") for a in w.rules if w.risky(a)), title
    leonard = str(_graph("L'atelier de Léonard"))
    assert " duc" not in leonard and "tournesol" not in leonard
