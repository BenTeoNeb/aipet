"""The « difficile » bank adventures played as a child would: natural sentences, wrong tries,
imagination, help, objects out of reach, a loss and a retry. Each turn checks what the engine answers."""

from tests.test_adventure_playthroughs import RETRY, A, G, play


def test_le_miroir_du_phare_d_alexandrie(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le miroir du phare d'Alexandrie", [
        ("Bonjour Théon, qu'est-ce qui se passe ?", [A("talk", "Théon")], ["done"]),
        ("Je caresse le chat", [A("other", "le chat")], ["free"]),
        ("Je prends un poisson", [A("take", "un poisson")], ["impossible"]),  # the whole basket: a clue
        ("Je fouille dans le panier", [A("search", "le panier")], ["done"]),
        ("Je prends la sardine et je fouille les filets",
         [A("take", "la sardine"), A("search", "les filets")], ["done", "done"]),
        ("Je prends la pièce et la lampe", [A("take", "la pièce"), A("take", "la lampe")], ["done", "done"]),
        ("Je donne la sardine au chat", [A("give", "la sardine", "Miou")], ["clue"]),  # he has eaten
        ("Je vais au marché", [G("au marché")], ["done"]),
        ("Je prends les braises", [A("take", "les braises")], ["impossible"]),  # « il te faudrait un pot »
        ("J'achète l'huile avec ma pièce", [A("give", "la pièce", "Ménès")], ["done"]),
        ("Je prends le pot et je mets des braises dedans",
         [A("take", "le pot"), A("use", "les braises", "le pot")], ["done", "done"]),
        ("Et je prends les figues", [A("take", "les figues")], ["done"]),
        ("Je vais à la bibliothèque", [G("la bibliothèque")], ["done"]),
        ("Je prends la clé", [A("take", "la clé")], ["not_yet"]),  # in the ibis's beak
        ("Je lis les rouleaux", [A("read", "les rouleaux")], ["done"]),
        ("Je donne la sardine à l'oiseau", [A("give", "la sardine", "l'oiseau")], ["done"]),
        ("Je prends la clé et le chiffon", [A("take", "la clé"), A("take", "le chiffon")], ["done", "done"]),
        ("Aide-moi Flocon", [A("help")], ["hint"]),
        ("Je vais sur la digue", [G("le marché"), G("le port"), G("la digue")], ["done"] * 3),
        ("Je vais au bout de la jetée et je nage", [G("la jetée"), A("swim")], ["done", "clue"]),  # the sailor says no
        ("Bonjour monsieur le marin", [A("talk", "le marin")], ["done"]),
        ("Je retourne sur la digue", [G("la digue")], ["done"]),
        ("Je mets du sable sur le chiffon",
         [A("take", "le sable"), A("combine", "le sable", "le chiffon")], ["done", "done"]),
        ("Je vais au phare et j'ouvre la porte", [G("le pied du phare"), A("open", "la porte")], ["done", "done"]),
        ("J'entre et je monte en haut", [G("dedans"), G("en haut")], ["done", "not_yet"]),  # Pistache
        ("Je grimpe sur l'âne", [A("climb", "l'âne")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("climb", "l'âne")], ["end"]),
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je donne les figues à Pistache", [A("give", "les figues", "Pistache")], ["done"]),
        ("Je monte tout en haut", [G("en haut")], ["done"]),
        ("Je tourne le miroir", [A("push", "le miroir")], ["not_yet"]),  # no fire, still dull
        ("Je verse l'huile dans le feu et je l'allume",
         [A("fill", "l'huile", "le feu"), A("light", "le feu")], ["done", "done"]),
        ("Je frotte le miroir avec le chiffon", [A("use", "le chiffon plein de sable", "le miroir")], ["done"]),
        ("Je tourne le miroir vers la mer", [A("push", "le miroir")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "navire" and [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_la_lanterne_de_la_grande_muraille(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "La lanterne de la Grande Muraille", [
        ("Bonjour Grand-mère Mei !", [A("talk", "Mamie")], ["done"]),
        ("Je prends la lanterne et le foulard", [A("take", "la lanterne"), A("take", "le foulard")], ["done", "done"]),
        ("Je regarde dans la gueule du dragon", [A("search", "le dragon")], ["done"]),
        ("Je prends la pièce", [A("take", "la pièce")], ["done"]),
        ("Je vais sur le pont", [G("le pont")], ["done"]),
        ("Je pêche une carpe dorée", [A("other", "une carpe")], ["free"]),
        ("Je prends une carpe", [A("take", "une carpe")], ["impossible"]),
        ("Je saute !", [A("jump")], ["clue"]),  # « on ne saute pas dans la rivière »
        ("Je saute dans la rivière", [A("jump", "la rivière")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("jump", "la rivière")], ["end"]),
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je vais à l'atelier de papier", [G("l'atelier")], ["done"]),
        ("Bonjour Petit Lu", [A("talk", "Lu")], ["done"]),
        ("Je prends une feuille", [A("take", "une feuille")], ["not_yet"]),  # seen, still on the rack
        ("Je cherche une feuille bien sèche sur le séchoir", [A("search", "le séchoir")], ["done"]),
        ("Je prends la feuille", [A("take", "la feuille")], ["done"]),
        ("Je retourne au village et à la maison de thé",
         [G("le pont"), G("le village"), G("la maison de thé")], ["done"] * 3),
        ("Je prends un petit pain", [A("take", "un petit pain")], ["impossible"]),  # « c'est une pièce ! »
        ("Je donne la pièce à la dame", [A("give", "la pièce", "Madame Hua")], ["done"]),
        ("Je donne la feuille au vieux monsieur", [A("give", "la feuille", "Maître Wen")], ["done"]),
        ("Je mange le petit pain", [A("eat", "baozi")], ["clue"]),  # someone dreams of it
        ("Je vais dans la forêt de bambous", [G("la place du village"), G("les bambous")], ["done", "done"]),
        ("Je prends des bambous", [A("take", "des bambous")], ["impossible"]),  # Dodu lies on them
        ("Je donne le petit pain au chameau", [A("give", "le petit pain", "le chameau")], ["done"]),
        ("Je prends les bambous", [A("take", "les bambous")], ["done"]),
        ("Je fabrique un cerf-volant avec le foulard et les bambous",
         [A("combine", "le foulard", "les bambous")], ["done"]),
        ("J'attache le message au cerf-volant", [A("combine", "le message", "le cerf-volant")], ["done"]),
        ("Je fais voler le cerf-volant", [A("throw", "le cerf-volant")], ["clue"]),  # no wind here
        ("Je vais à la Grande Muraille", [G("la Grande Muraille")], ["done"]),
        ("Bonjour la grue", [A("talk", "la grue")], ["done"]),
        ("Je monte le grand escalier", [G("le grand escalier")], ["done"]),
        ("Je réveille le garde", [A("talk", "le garde")], ["clue"]),  # he snores
        ("Je prends la clé à sa ceinture", [A("take", "la clé")], ["not_yet"]),  # seen on his belt
        ("Flocon, je ne sais pas quoi faire", [A("help")], ["hint"]),
        ("Je lance le cerf-volant dans le vent", [A("throw", "le cerf-volant")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "cerf_volant" and [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]


def test_le_petit_rover_de_la_lune(tmp_path, monkeypatch):
    game = play(tmp_path, monkeypatch, "Le petit rover de la Lune", [
        ("Bonjour capitaine !", [A("talk", "la capitaine")], ["done"]),
        ("J'appuie sur un bouton", [A("push", "un bouton")], ["clue"]),  # a small one: never the red one
        ("Je sors dehors", [G("dehors")], ["not_yet"]),
        ("J'ouvre la trappe", [A("open", "la trappe")], ["not_yet"]),  # « d'abord le casque ! »
        ("Je prends le casque et je le mets", [A("take", "le casque"), A("wear", "le casque")], ["done", "done"]),
        ("J'ouvre la trappe et je prends la batterie",
         [A("open", "la trappe"), A("take", "la batterie")], ["done", "done"]),
        ("Je sors sur la Lune", [G("dehors")], ["done"]),
        ("Je vais au gros rocher", [G("le gros rocher")], ["done"]),
        ("Je saute de joie", [A("jump")], ["clue"]),  # a small hop
        ("Je fouille le tas de poussière", [A("search", "le tas de poussière")], ["done"]),
        ("Je saute du haut du rocher", [A("jump", "le rocher")], ["warning"]),
        ("(il insiste) Oui, je suis sûr !", [A("jump", "le rocher")], ["end"]),
        ("Oh non ! On recommence juste avant", RETRY, None),
        ("Je prends le ruban et je retourne au module", [A("take", "le ruban"), G("le module")], ["done", "done"]),
        ("Je fais un bisou à Bip", [A("other", "Bip")], ["free"]),
        ("Je donne la batterie à Bip", [A("give", "la batterie", "Bip")], ["not_yet"]),  # wheels stuck
        ("Je pousse Bip", [A("push", "Bip")], ["done"]),
        ("J'ouvre la soute", [A("open", "la soute")], ["done"]),
        ("Je prends la pince et le manche", [A("take", "la pince"), A("take", "le manche")], ["done", "done"]),
        ("Je donne la batterie au robot", [A("give", "la batterie", "le robot")], ["done"]),
        ("Je vais au cratère", [G("la plaine"), G("le cratère")], ["done", "done"]),
        ("Je saute de joie", [A("jump")], ["free"]),
        ("Je descends dans le cratère", [G("en bas")], ["done"]),
        ("Je prends la pierre qui brille", [A("take", "la pierre")], ["not_yet"]),  # deep in the crack
        ("J'attrape la pierre avec la pince", [A("use", "la pince", "la fente")], ["clue"]),  # too short
        ("Je mets la pince au bout du manche", [A("combine", "la pince", "le manche")], ["done"]),
        ("J'attrape la pierre avec la longue pince", [A("use", "la longue pince", "la fente")], ["done"]),
        ("Aide-moi", [A("help")], ["hint"]),
        ("Je remonte jusqu'au module",
         [G("en haut"), G("la plaine"), G("le module"), G("dans le module")], ["done"] * 4),
        ("Je donne la pierre bleue à la capitaine", [A("give", "la pierre bleue", "Lou")], ["end"]),
    ])  # fmt: skip
    assert game.state.ending == "pierre" and [r["outcome"] for r in game.adventure["runs"]] == ["lose", "win"]
