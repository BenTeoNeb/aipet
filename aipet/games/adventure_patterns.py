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
