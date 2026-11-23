"""The catalogue of puzzle patterns (escape games, gamebooks, point-and-click): one tiny adventure per
pattern, written in model 2, that the checker accepts (winnable, never stuck, the solver and the turn
agree) and that a child wins by speaking (`spoken`). tests/test_adventure_patterns.py plays them all;
the explorer page shows them; the generator's prompt names the same recipes
(docs/adventure/model/README.md)."""


def place(pid, *exits, **kw):
    return {"id": pid, "name": f"le lieu {pid}", "description": f"Le lieu {pid}.",
            "exits": [{"to": t, "label": f"vers {t}", "dir": d, **x} for t, d, *rest in exits
                      for x in (rest[0] if rest else {},)], **kw}  # fmt: skip


def thing(tid, at, kind="thing", **kw):
    return {"id": tid, "kind": kind, "name": f"le {tid}", "aliases": [tid], "at": at,
            "description": f"Un {tid}.", **kw}  # fmt: skip


def person(cid, at, **kw):
    return {"id": cid, "name": cid.capitalize(), "aliases": [cid], "at": at, "description": f"{cid}.",
            "says": "Bonjour !", **kw}  # fmt: skip


def act(aid, triggers, effects, when=(), **kw):
    return {"id": aid, "triggers": [dict(zip(("verb", "object", "target"), t, strict=False)) for t in triggers],
            "when": list(when), "effects": effects, "text": f"{aid} !", **kw}  # fmt: skip


def adventure(places, items, characters, actions, start="a", loses=()):
    return {"title": "patron", "difficulty": "facile", "format": 2, "start": {"place": start, "inventory": []},
            "places": places, "items": items, "characters": characters, "actions": actions,
            "endings": [{"id": "gagne", "kind": "win", "text": "Gagné !"},
                        *({"id": x, "kind": "lose", "text": "Perdu."} for x in loses)]}  # fmt: skip


# Two places, a and b, both ways.
AB = [place("a", ("b", "est")), place("b", ("a", "ouest"))]

PATTERNS = {
    # A key opens a locked door (Inform's Locksmith: walking through with the key unlocks it).
    "key_and_door": (
        adventure(
            [place("a", ("b", "est", {"door": "porte"})), place("b", ("a", "ouest"))],
            [thing("cle", "a"), thing("porte", "", "door", locked=True, key="cle"), thing("tresor", "b")],
            [],
            [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        ),
        [("take", "la clé"), ("go", "est"), ("take", "le trésor"), ("use", "le trésor")],
    ),
    # Something hidden is found by searching (« fouiller la paille »).
    "hidden_search": (
        adventure(
            AB,
            [thing("paille", "b", "scenery"), thing("bague", "")],
            [person("reine", "a")],
            [
                act("fouiller", [("search", "paille")], ["reveal:bague"]),
                act("rendre", [("give", "bague", "reine")], ["end:gagne"], ["has:bague"]),
            ],
        ),
        [
            ("go", "est"),
            ("search", "la paille"),
            ("take", "la bague"),
            ("go", "ouest"),
            ("give", "la bague", "la reine"),
        ],
    ),
    # Two things make a third (blue + yellow = green).
    "combine": (
        adventure(
            AB,
            [thing("bleu", "a"), thing("jaune", "b"), thing("vert", ""), thing("toile", "a", "scenery")],
            [],
            [
                act(
                    "melanger",
                    [("combine", "bleu", "jaune")],
                    ["lose:bleu", "lose:jaune", "gain:vert"],
                    ["has:bleu", "has:jaune"],
                ),
                act("peindre", [("use", "vert", "toile")], ["end:gagne"], ["has:vert"]),
            ],
        ),
        [
            ("take", "le bleu"),
            ("go", "est"),
            ("take", "le jaune"),
            ("combine", "le jaune", "le bleu"),
            ("go", "ouest"),
            ("use", "le vert", "la toile"),
        ],
    ),
    # A recipe that changes a thing: the tool stays, the thing goes to its next state (and shows what it held).
    "recipe_changes_a_thing": (
        adventure(
            AB,
            [
                thing("ouvre_boite", "a"),
                thing(
                    "conserve",
                    "b",
                    states=[
                        {"id": "fermee", "name": "la conserve fermée"},
                        {"id": "ouverte", "name": "la conserve ouverte"},
                    ],
                ),
                thing("sardine", "", aliases=["sardine", "poisson"]),
            ],
            [person("chat", "b")],
            [
                act(
                    "ouvrir_conserve",
                    [("combine", "ouvre_boite", "conserve"), ("use", "ouvre_boite", "conserve")],
                    ["set:conserve:ouverte", "reveal:sardine"],
                    ["has:ouvre_boite", "is:conserve:fermee"],
                ),
                act("nourrir_chat", [("give", "sardine", "chat")], ["lose:sardine", "end:gagne"], ["has:sardine"]),
            ],
        ),
        [
            ("take", "l'ouvre-boîte"),
            ("go", "est"),
            ("use", "l'ouvre-boîte", "la conserve"),
            ("give", "la sardine", "au chat"),
        ],
    ),
    # A recipe of three: two things are mixed while the third is in the bag (a trigger names two things,
    # `when` asks for the rest); the result is a new thing.
    "recipe_of_three": (
        adventure(
            AB,
            [
                thing("farine", "a"),
                thing("oeuf", "a"),
                thing("lait", "b"),
                thing("pate", ""),
                thing("four", "b", "scenery"),
            ],
            [],
            [
                act(
                    "faire_pate",
                    [("combine", "farine", "oeuf")],
                    ["lose:farine", "lose:oeuf", "lose:lait", "gain:pate"],
                    ["has:farine", "has:oeuf", "has:lait"],
                    not_yet="Il manque quelque chose de liquide pour la pâte.",
                ),
                act("cuire", [("use", "pate", "four")], ["end:gagne"], ["has:pate"]),
            ],
        ),
        [
            ("take", "la farine"),
            ("take", "l'oeuf"),
            ("go", "est"),
            ("take", "le lait"),
            ("combine", "la farine", "l'oeuf"),
            ("use", "la pâte", "le four"),
        ],
    ),
    # A silly recipe (point-and-click humour): an absurd mix is a second way to win, not a trap.
    "silly_recipe": (
        adventure(
            AB,
            [thing("chaussette", "a"), thing("poivre", "a"), thing("bombe", ""), thing("biscuit", "b")],
            [person("dragon", "b", says="Grrr ! Personne ne passe !")],
            [
                act(
                    "bombe_qui_pue",
                    [("combine", "chaussette", "poivre")],
                    ["lose:chaussette", "lose:poivre", "gain:bombe"],
                    ["has:chaussette", "has:poivre"],
                ),
                act(
                    "faire_eternuer",
                    [("throw", "bombe", "dragon"), ("give", "bombe", "dragon")],
                    ["lose:bombe", "end:gagne"],
                    ["has:bombe"],
                ),
                act("amadouer", [("give", "biscuit", "dragon")], ["lose:biscuit", "end:gagne"], ["has:biscuit"]),
            ],
        ),
        [
            ("take", "la chaussette"),
            ("take", "le poivre"),
            ("combine", "la chaussette", "le poivre"),
            ("go", "est"),
            ("throw", "la bombe", "le dragon"),
        ],
    ),
    # A trade: someone holds what the hero needs, and lets it go for something else.
    "trade_with_a_holder": (
        adventure(
            AB,
            [thing("pomme", "b"), thing("cle", "ane")],
            [person("ane", "a")],
            [
                act("troc", [("give", "pomme", "ane")], ["lose:pomme", "reveal:cle"], ["has:pomme"]),
                act("fin", [("use", "cle")], ["end:gagne"], ["has:cle"]),
            ],
        ),
        [
            ("go", "est"),
            ("take", "la pomme"),
            ("go", "ouest"),
            ("give", "la pomme", "l'âne"),
            ("take", "la clé"),
            ("use", "la clé"),
        ],
    ),
    # A character won over in steps (Inform's « can be grumpy, calm or friendly »).
    "character_in_steps": (
        adventure(
            AB,
            [thing("miel", "b"), thing("fleur", "b")],
            [person("ours", "a", states=[{"id": "grognon"}, {"id": "calme"}, {"id": "ami"}])],
            [
                act("miel", [("give", "miel", "ours")], ["set:ours:calme"], ["has:miel", "is:ours:grognon"]),
                act("fleur", [("give", "fleur", "ours")], ["set:ours:ami"], ["has:fleur", "is:ours:calme"]),
                act("fin", [("talk", "ours")], ["end:gagne"], ["is:ours:ami"]),
            ],
        ),
        [
            ("go", "est"),
            ("take", "le miel"),
            ("take", "la fleur"),
            ("go", "ouest"),
            ("give", "le miel", "l'ours"),
            ("give", "la fleur", "l'ours"),
            ("talk", "l'ours"),
        ],
    ),
    # Gather several things (three pearls) before the end.
    "collect": (
        adventure(
            AB,
            [thing("perle1", "a"), thing("perle2", "b"), thing("perle3", "b"), thing("collier", "a", "scenery")],
            [],
            [
                act(
                    "fin",
                    [("use", "perle1", "collier")],
                    ["end:gagne"],
                    ["has:perle1", "has:perle2", "has:perle3"],
                    not_yet="Il manque des perles.",
                )
            ],
        ),
        [
            ("take", "la perle1"),
            ("go", "est"),
            ("take", "la perle2"),
            ("take", "la perle3"),
            ("go", "ouest"),
            ("use", "la perle1", "le collier"),
        ],
    ),
    # A way blocked until something is done (a bridge mended, a dark cave and a lamp).
    "blocked_way": (
        adventure(
            [
                place("a", ("b", "est", {"when": ["has:lampe"], "blocked": "Il fait tout noir !"})),
                place("b", ("a", "ouest")),
            ],
            [thing("lampe", "a"), thing("gemme", "b")],
            [],
            [act("fin", [("use", "gemme")], ["end:gagne"], ["has:gemme"])],
        ),
        [("take", "la lampe"), ("go", "est"), ("take", "la gemme"), ("use", "la gemme")],
    ),
    # A map shows a hidden way.
    "map_reveals_a_way": (
        adventure(
            [
                place("a", ("b", "est", {"when": ["flag:passage"], "blocked": "Rien par là…"})),
                place("b", ("a", "ouest")),
            ],
            [thing("carte", "a"), thing("coffre", "b", "scenery")],
            [],
            [
                act("lire", [("read", "carte")], ["flag:passage"], ["has:carte"]),
                act("fin", [("search", "coffre")], ["end:gagne"]),
            ],
        ),
        [("take", "la carte"), ("read", "la carte"), ("go", "est"), ("search", "le coffre")],
    ),
    # A code: buttons pressed in the order shown somewhere else.
    "code": (
        adventure(
            [
                place("a", ("b", "est"), details=[{"when": [], "text": "Au mur : un soleil, puis une lune."}]),
                place("b", ("a", "ouest")),
            ],
            [
                thing("soleil", "b", "scenery"),
                thing("lune", "b", "scenery"),
                thing("tresor", "boite"),
                thing("boite", "b", "container", code={"press": ["lune", "soleil"], "wrong": "Bzzz !"}),
            ],
            [],
            [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        ),
        [
            ("go", "est"),
            ("push", "le soleil"),
            ("push", "la lune"),
            ("push", "le soleil"),
            ("take", "le trésor"),
            ("use", "le trésor"),
        ],
    ),
    # A riddle or a password, answered in the child's words.
    "riddle": (
        adventure(
            [place("a", ("b", "est", {"door": "porte"})), place("b", ("a", "ouest"))],
            [
                thing(
                    "porte",
                    "",
                    "door",
                    answer={
                        "question": "Qu'est-ce qui a des dents mais ne mord jamais ?",
                        "accept": ["peigne"],
                        "wrong": "Non !",
                    },
                ),
                thing("tresor", "b"),
            ],
            [],
            [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        ),
        [
            ("answer", "une brosse", "la porte"),
            ("answer", "c'est un peigne !", "la porte"),
            ("go", "est"),
            ("take", "le trésor"),
            ("use", "le trésor"),
        ],
    ),
    # A companion follows the hero and helps further on.
    "companion": (
        adventure(
            AB,
            [thing("rocher", "b", "scenery")],
            [person("ours", "a")],
            [
                act("suivre", [("talk", "ours")], ["follow:ours"]),
                act(
                    "fin",
                    [("push", "rocher")],
                    ["end:gagne"],
                    ["here:ours"],
                    not_yet="Trop lourd tout seul… Il faudrait quelqu'un de costaud.",
                ),
            ],
        ),
        [("talk", "l'ours"), ("go", "est"), ("push", "le rocher")],
    ),
    # A dark place (Inform's darkness): without a light, nothing is seen there but the ways out; a lamp
    # lit (a state) shows what is there.
    "light_in_the_dark": (
        adventure(
            [
                place("a", ("b", "est")),
                place(
                    "b",
                    ("a", "ouest"),
                    dark="Il fait noir comme dans un four. Ploc… ploc… quelque chose goutte.",
                    senses={"listen": "Ploc… ploc… de l'eau goutte du plafond."},
                ),
            ],
            [
                thing(
                    "lampe",
                    "a",
                    light=["is:lampe:allumee"],
                    states=[
                        {"id": "eteinte", "name": "la lampe éteinte"},
                        {"id": "allumee", "name": "la lampe allumée"},
                    ],
                ),
                thing("tresor", "b", aliases=["trésor", "tresor"]),
            ],
            [person("roi", "a")],
            [
                act("allumer", [("light", "lampe")], ["set:lampe:allumee"], ["has:lampe", "is:lampe:eteinte"]),
                act("rendre", [("give", "tresor", "roi")], ["lose:tresor", "end:gagne"], ["has:tresor"]),
            ],
        ),
        [
            ("take", "la lampe"),
            ("light", "la lampe"),
            ("go", "est"),
            ("take", "le trésor"),
            ("go", "ouest"),
            ("give", "le trésor", "au roi"),
        ],
    ),
    # Uses, and refilling: a watering can waters once, then it is filled again at the fountain.
    "uses_and_refill": (
        adventure(
            AB,
            [
                thing("arrosoir", "a", uses=1, texts={"empty": "L'arrosoir est vide : plus une goutte !"}),
                thing("rose", "a", "scenery"),
                thing("tulipe", "b", "scenery"),
                thing("fontaine", "b", "scenery"),
            ],
            [],
            [
                act("arroser_rose", [("use", "arrosoir", "rose")], ["spend:arrosoir", "flag:rose"], ["has:arrosoir"]),
                act(
                    "arroser_tulipe",
                    [("use", "arrosoir", "tulipe")],
                    ["spend:arrosoir", "end:gagne"],
                    ["has:arrosoir", "flag:rose"],
                    not_yet="La rose d'abord, elle a très soif !",
                ),
                act("remplir", [("fill", "arrosoir", "fontaine")], ["refill:arrosoir"], ["has:arrosoir"]),
            ],
        ),
        [
            ("take", "l'arrosoir"),
            ("use", "l'arrosoir", "la rose"),
            ("go", "est"),
            ("use", "l'arrosoir", "la tulipe"),
            ("fill", "l'arrosoir", "la fontaine"),
            ("use", "l'arrosoir", "la tulipe"),
        ],
    ),
    # A light that burns: a lantern with one use lights a dark place, and goes out when the hero leaves it;
    # the fireflies fill it again (never stuck: the ways out are felt in the dark).
    "lantern_that_burns": (
        adventure(
            [
                place("a", ("b", "ouest"), ("c", "est", {"door": "grille"})),
                place("b", ("a", "est"), dark=True),
                place("c", ("a", "ouest"), dark=True),
            ],
            [
                thing("lanterne", "a", light=True, uses=1, texts={"empty": "La lanterne s'éteint doucement…"}),
                thing("lucioles", "a", "scenery"),
                thing("cle", "b", aliases=["clé", "cle"]),
                thing("grille", "", "door", locked=True, key="cle"),
                thing("tresor", "c", aliases=["trésor", "tresor"]),
            ],
            [person("roi", "a")],
            [
                act("remplir", [("fill", "lanterne", "lucioles")], ["refill:lanterne"], ["has:lanterne"]),
                act("rendre", [("give", "tresor", "roi")], ["lose:tresor", "end:gagne"], ["has:tresor"]),
            ],
        ),
        [
            ("take", "la lanterne"),
            ("go", "ouest"),
            ("take", "la clé"),
            ("go", "est"),
            ("fill", "la lanterne", "les lucioles"),
            ("go", "est"),
            ("take", "le trésor"),
            ("go", "ouest"),
            ("give", "le trésor", "au roi"),
        ],
    ),
    # Listening (a voice game's sense): the wall sounds hollow where the hiding place is.
    "listen_for_it": (
        adventure(
            AB,
            [
                thing("mur", "b", "scenery", senses={"touch": "Des pierres froides et lisses."}),
                thing("cachette", "", "container", open=True),
                thing("bague", "cachette"),
            ],
            [person("reine", "a", says="J'ai caché ma bague dans le mur… mais où ? Écoute bien, ça sonne creux !")],
            [
                act(
                    "ecouter_mur",
                    [("listen", "mur"), ("hit", "mur")],
                    ["reveal:cachette"],
                    text="Toc toc… ici, ça sonne creux ! Une pierre bouge : une cachette !",
                ),
                act("rendre", [("give", "bague", "reine")], ["lose:bague", "end:gagne"], ["has:bague"]),
            ],
        ),
        [
            ("go", "est"),
            ("listen", "le mur"),
            ("take", "la bague"),
            ("go", "ouest"),
            ("give", "la bague", "à la reine"),
        ],
    ),
    # A choice that closes another way, and a folly announced before it loses.
    "choice_and_folly": (
        adventure(
            AB,
            [thing("pont", "b", "scenery"), thing("barque", "b", "scenery")],
            [],
            [act("pont", [("climb", "pont")], ["end:gagne"]), act("barque", [("climb", "barque")], ["end:noye"])],
            loses=["noye"],
        ),
        [("go", "est"), ("climb", "la barque"), ("climb", "le pont")],  # warned once, then the bridge
    ),
}
