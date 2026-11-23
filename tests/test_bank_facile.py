"""The « facile » adventures played as a child would: natural sentences, wrong tries,
imagination, help, things seen but out of reach, a loss and a retry. Each turn checks the outcome."""

from tests.test_adventure_playthroughs import RETRY, A, G, play


def test_la_grotte_aux_peintures(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "La grotte aux peintures d'Oukou", [
        ("Bonjour Oukou, qu'est-ce qu'on fait ?", [A("talk", "Oukou")], ["done"]),
        ("Je vais dans la grotte", [G("la grotte")], ["done"]),
        ("J'entre tout au fond", [G("dedans")], ["not_yet"]),  # too dark: a light is needed
        ("Je prends le charbon", [A("take", "le charbon")], ["done"]),
        ("Je tape sur le tambour !", [A("hit", "le tambour")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("hit", "le tambour")], ["end"]),  # the bats wake up
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je retourne au campement et je prends la torche",
         [G("le campement"), A("take", "la torche")], ["done", "done"]),
        ("Je danse autour du feu", [A("dance", "le feu")], ["free"]),
        ("J'allume la torche", [A("light", "la torche")], ["done"]),
        ("Je vais à la rivière", [G("la rivière")], ["done"]),
        ("Je prends la terre rouge", [A("take", "la terre rouge")], ["not_yet"]),  # under the mammoth
        ("Je pousse le mammouth", [A("push", "le mammouth")], ["clue"]),
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je cueille des framboises et je les donne à Patapouf",
         [A("take", "des framboises"), A("give", "les framboises", "Patapouf")], ["done", "done"]),
        ("Je prends l'ocre", [A("take", "l'ocre")], ["done"]),
        ("Je vais au fond de la grotte",
         [G("le campement"), G("la grotte"), G("dedans")], ["done", "done", "done"]),
        ("Je dessine les mammouths avec le charbon", [A("use", "le charbon", "les mammouths")], ["done"]),
        ("Je mets l'ocre sur la peinture", [A("use", "l'ocre", "la peinture")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "festin"
    assert [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_la_grotte_aux_peintures_mains(tmp_path, monkeypatch):
    """The second way to win: the hand blown with the wolf pup's hollow bone."""
    game = play(tmp_path, monkeypatch, "La grotte aux peintures d'Oukou", [
        ("Je vais dans le bois", [G("le bois")], ["done"]),
        ("Je prends l'os du louveteau", [A("take", "l'os")], ["not_yet"]),  # in Pistache's mouth
        ("Je lance une pomme de pin à Pistache",
         [A("take", "une pomme de pin"), A("throw", "la pomme de pin", "Pistache")], ["done", "done"]),
        ("Je prends l'os", [A("take", "l'os")], ["done"]),
        ("Je prends la torche et je la mets dans le feu",
         [G("campement"), A("take", "la torche"), A("use", "la torche", "le feu")], ["done", "done", "done"]),
        ("Je donne des framboises au mammouth",
         [G("rivière"), A("take", "les framboises"), A("give", "les framboises", "le mammouth"), A("take", "l'ocre")],
         ["done", "done", "done", "done"]),
        ("Je vais dans la grotte avec ma torche", [G("campement"), G("grotte")], ["done", "done"]),
        ("Je ramasse du charbon et j'entre", [A("take", "du charbon"), G("dedans")], ["done", "done"]),
        ("Je souffle dans l'os", [A("use", "l'os")], ["not_yet"]),  # the black outline first
        ("Je fais le contour en noir", [A("use", "le charbon", "la paroi")], ["done"]),
        ("Je souffle dans l'os pour faire ma main", [A("souffler", "l'os", "la paroi")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "mains"


def test_le_ballon_de_versailles(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le ballon de Versailles", [
        ("Je prends la clochette", [A("take", "la clochette")], ["not_yet"]),  # Lucile holds it
        ("Lucile, qu'est-ce qui se passe ?", [A("talk", "Lucile")], ["done"]),  # she gives the bell
        ("J'allume le feu !", [A("light", "le feu")], ["not_yet"]),  # no passengers yet
        ("Je vais dans le jardin", [G("le jardin")], ["done"]),
        ("J'attrape le mouton", [A("take", "le mouton")], ["impossible"]),
        ("Je fais sonner la clochette", [A("use", "la clochette")], ["done"]),
        ("Je grimpe sur la statue", [A("climb", "la statue")], ["clue"]),
        ("Je chante cocorico au coq", [A("sing", "le coq")], ["free"]),
        ("Je vais aux cuisines et je prends les grains",
         [G("la cour"), G("les cuisines"), A("take", "les grains")], ["done", "done", "done"]),
        ("Je donne les grains au coq",
         [G("la cour"), G("le jardin"), A("give", "les grains", "le coq")], ["done", "done", "done"]),
        ("Aide-moi", [A("help")], ["hint"]),
        ("Je vais au bassin", [G("la cour"), G("le bassin")], ["done", "done"]),
        ("Monsieur, comment on attrape le canard ?", [A("ask", "le fontainier", "le canard")], ["done"]),
        ("Je saute dans le bassin !", [A("jump", "le bassin")], ["clue"]),  # the fountain keeper says no
        ("Je touche la vanne", [A("touch", "la vanne")], ["clue"]),  # « pas touche ! »: a warning, not a loss
        ("Coin ! Coin ! Je fais le canard", [A("sing", "le canard")], ["done"]),  # the duck follows a friend
        ("Je retourne dans la cour et j'allume le feu", [G("la cour"), A("light", "le feu")], ["done", "end"]),
    ])  # fmt: skip
    assert game.state.ending == "coincoin"


def test_le_ballon_de_versailles_brioche(tmp_path, monkeypatch):
    """The other way: the brioche crumbs; the valve is a gentle loss, then a retry."""
    game = play(tmp_path, monkeypatch, "Le ballon de Versailles", [
        ("Lucile, aide-moi", [A("talk", "Lucile")], ["done"]),
        ("Je vais aux cuisines et je prends la brioche et les grains",
         [G("les cuisines"), A("take", "la brioche"), A("take", "les grains")], ["done", "done", "done"]),
        ("Je vais au bassin et je tourne la vanne", [G("la cour"), G("le bassin"), A("tourner", "la vanne")],
         ["done", "done", "warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("tourner", "la vanne")], ["end"]),  # everybody gets wet
        ("On recommence juste avant !", RETRY, None),
        ("Je donne la brioche au canard", [A("give", "la brioche", "le canard")], ["done"]),
        ("Je vais dans le jardin", [G("la cour"), G("le jardin")], ["done", "done"]),
        ("Je donne les grains au coq et je sonne la clochette",
         [A("give", "les grains", "le coq"), A("use", "la clochette", "le mouton")], ["done", "done"]),
        ("Je retourne dans la cour et j'allume le ballon", [G("la cour"), A("light", "le ballon")], ["done", "end"]),
    ])  # fmt: skip
    assert game.state.ending == "envol"
    assert [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_le_scarabee_du_scribe(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le scarabée du scribe", [
        ("Kha, pourquoi tu pleures ?", [A("talk", "Kha")], ["done"]),
        ("Je monte sur la terrasse", [G("la terrasse")], ["done"]),
        ("Je prends le scarabée", [A("take", "le scarabée")], ["not_yet"]),  # under the cat's paw
        ("Je pousse la chatte", [A("push", "la chatte")], ["clue"]),
        ("Je fais un câlin à Miou", [A("other", "Miou")], ["free"]),
        ("Je chatouille Miou", [A("chatouiller", "Miou")], ["clue"]),  # with something soft, maybe?
        ("Je redescends et je prends le papyrus", [G("en bas"), A("take", "le papyrus")], ["done", "done"]),
        ("Je vais au marché", [G("le marché")], ["done"]),
        ("Je prends un poisson", [A("take", "un poisson")], ["not_yet"]),  # on the stall: not for free
        ("Je donne la feuille à la marchande", [A("give", "la feuille", "la marchande")], ["not_yet"]),  # blank
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je retourne prendre le calame",
         [G("l'atelier"), A("take", "le calame"), G("le marché")], ["done", "done", "done"]),
        ("J'écris une pancarte sur le papyrus avec le calame", [A("use", "le calame", "le papyrus")], ["done"]),
        ("Je retourne sur la terrasse et je donne le poisson à Miou",
         [G("l'atelier"), G("la terrasse"), A("give", "le poisson", "Miou")], ["done", "done", "done"]),
        ("Je prends le scarabée", [A("take", "le scarabée")], ["done"]),
        ("Je vais au temple", [G("en bas"), G("le marché"), G("le temple")], ["done", "done", "done"]),
        ("Princesse, voilà ton scarabée !", [A("give", "le scarabée", "la princesse")], ["not_yet"]),  # her riddle first
        ("Bonjour princesse !", [A("talk", "la princesse")], ["done"]),  # the riddle, like the Sphinx's
        ("C'est la lune !", [A("answer", "la lune", "Méryt")], ["not_yet"]),
        ("C'est le soleil !", [A("answer", "le soleil", "Méryt")], ["done"]),
        ("Voilà ton scarabée !", [A("give", "le scarabée", "la princesse")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "chatte"


def test_le_scarabee_du_scribe_barque(tmp_path, monkeypatch):
    """The lose ending is signposted (Kha and the ibis); the feather is the other way to the scarab."""
    game = play(tmp_path, monkeypatch, "Le scarabée du scribe", [
        ("Je vais au bord du Nil", [G("le Nil")], ["done"]),
        ("Je cueille un papyrus", [A("take", "les roseaux")], ["impossible"]),
        ("Je prends la plume", [A("take", "la plume")], ["done"]),
        ("Je grimpe dans la barque", [A("climb", "la barque")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("climb", "la barque")], ["end"]),  # adrift on the Nile
        ("On réessaie juste avant !", RETRY, None),
        ("Je chatouille Miou avec la plume",
         [G("l'atelier"), G("la terrasse"), A("chatouiller", "Miou", "la plume")], ["done", "done", "done"]),
        ("Je caresse Miou", [A("caresser", "Miou")], ["clue"]),  # she purrs: nothing more to do here
        ("Je prends le scarabée et je l'apporte à la princesse",
         [A("take", "le scarabée"), G("en bas"), G("le marché"), G("le temple"), A("answer", "le soleil", "Méryt"),
          A("give", "le scarabée", "Méryt")],
         ["done", "done", "done", "done", "done", "end"]),
    ])  # fmt: skip
    assert game.state.ending == "fete"


def test_le_scarabee_du_scribe_souris(tmp_path, monkeypatch):
    """The silly recipe: a papyrus mouse with an ibis-feather tail, a third way to the scarab."""
    game = play(tmp_path, monkeypatch, "Le scarabée du scribe", [
        ("Je prends le papyrus", [A("take", "le papyrus")], ["done"]),
        ("Je vais au bord du Nil et je prends la plume", [G("le Nil"), A("take", "la plume")], ["done", "done"]),
        ("Je fabrique une souris avec le papyrus et la plume",
         [A("combine", "la plume", "le papyrus")], ["done"]),
        ("Je montre la souris à Miou",
         [G("l'atelier"), G("la terrasse"), A("give", "la souris", "Miou")], ["done", "done", "done"]),
        ("Je prends le scarabée et je vais au temple",
         [A("take", "le scarabée"), G("en bas"), G("le marché"), G("le temple")], ["done"] * 4),
        ("Bonjour princesse", [A("talk", "Méryt")], ["done"]),
        ("Un chat ?", [A("answer", "un chat", "Méryt")], ["not_yet"]),
        ("Le soleil !", [A("answer", "le soleil")], ["done"]),
        ("Voilà le scarabée", [A("give", "le scarabée", "Méryt")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "chatte"


def test_le_phare_de_piko(tmp_path, monkeypatch):
    """The child brings the matches; Papy Pic, the grown-up keeper, strikes them."""
    game = play(tmp_path, monkeypatch, "Le phare de Piko", [
        ("Piko, qu'est-ce qui se passe ?", [A("talk", "Piko")], ["done"]),
        ("Je vais aux rochers", [G("les rochers")], ["done"]),
        ("Je prends la clé", [A("take", "la clé")], ["not_yet"]),  # in the crab's claw
        ("Je tape le crabe !", [A("hit", "le crabe")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("hit", "le crabe")], ["end"]),  # warned: he pinches
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je fais un château de sable", [A("other", "un château de sable")], ["free"]),
        ("Je prends une algue et je la donne au crabe",
         [A("take", "une algue"), A("give", "l'algue", "le crabe"), A("take", "la clé")], ["done", "done", "done"]),
        ("Je vais dans la cabane de Zouzou", [G("la plage"), G("la cabane")], ["done", "done"]),
        ("Je prends les allumettes", [A("take", "les allumettes")], ["not_yet"]),  # in the drawer with a code
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("J'ouvre le tiroir", [A("open", "le tiroir")], ["not_yet"]),  # three shells glued on it
        ("J'appuie sur les coquillages comme sur le dessin : bleu, rose, blanc, et je prends les allumettes",
         [A("push", "le coquillage bleu"), A("push", "le coquillage rose"), A("push", "le coquillage blanc"),
          A("take", "les allumettes")], ["done", "done", "done", "done"]),
        ("Je vais au phare et j'ouvre la porte", [G("la plage"), G("le phare"), A("open", "la porte")],
         ["done", "done", "done"]),
        ("Je monte en haut", [G("en haut")], ["done"]),
        ("Je donne les allumettes à Papy", [A("give", "les allumettes", "Papy")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "feu"
    assert [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]
