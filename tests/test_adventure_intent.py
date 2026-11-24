"""Jev in the shadow: its answers read into engine actions, compared with the narrator's, logged
(never played). No network: the Decisions API's answers are given."""

import asyncio

from aipet.games import adventure as adv
from aipet.games import adventure_intent as intent
from aipet.games.adventure_engine import World
from aipet.games.common import Host


def choice(c, conf=0.9, **probs):
    return {"type": "choice", "choice": c, "confidence": conf, "probabilities": probs or {c: conf}}


def answers(n, *slots, conf=0.9):
    """slots: (verb, object, target, to) per action."""
    a = {"nombre": choice(str(n), conf)}
    for i in range(1, intent.SLOTS + 1):
        v, o, t, to = slots[i - 1] if i <= len(slots) else ("rien", "aucun", "aucun", "aucun")
        a |= {f"a{i}_verbe": choice(v, conf), f"a{i}_objet": choice(o, conf), f"a{i}_cible": choice(t, conf),
              f"a{i}_vers": choice(to, conf)}  # fmt: skip
    return a


def test_answers_become_actions_each_verb_with_its_own_fields():
    d = intent.read(answers(2, ("give", "algue", "pinces", "plage"), ("go", "pinces", "aucun", "phare")), "…")
    assert d["actions"] == [{"verb": "give", "object": "algue", "target": "pinces"}, {"verb": "go", "to": "phare"}]
    assert d["confidence"] == 0.9 and not d["unclear"]
    riddle = intent.read(answers(1, ("answer", "sphinx", "aucun", "aucun")), "c'est un peigne")
    assert riddle["actions"] == [{"verb": "answer", "object": "c'est un peigne", "target": "sphinx"}]
    same = intent.read(answers(1, ("talk", "piko", "piko", "aucun")), "…")
    assert same["actions"] == [{"verb": "talk", "object": "piko"}]  # the same thing twice: no target


def test_an_unclear_sentence_is_no_action_with_what_it_hesitated_between():
    a = answers(1, ("take", "ane", "aucun", "aucun"))
    a["a1_objet"] = choice("ane", 0.45, ane=0.45, charrette=0.40, aucun=0.15)
    d = intent.read(a, "je monte dessus")
    assert {"question": "a1_objet", "between": ["ane", "charrette"]} in d["hesitations"]
    assert intent.read({**a, "nombre": choice("?", 0.8)}, "le truc là")["unclear"]


class Kid(Host):
    skills = type("S", (), {"active": "adventure"})()

    def player(self):
        return "Zina"

    def last_pet_line(self):
        return "Plumette te regarde."


def test_the_shadow_reads_every_sentence_and_logs_what_the_narrator_did(tmp_path, monkeypatch):
    graph = next(g for g in adv.library() if g["title"] == "Le bonhomme de neige de Plumette")
    monkeypatch.setattr(adv, "library", lambda difficulty=None: [graph])
    game = adv.AdventureGame({"adventure": {"intent": "jev-shadow"}}, tmp_path, Kid())
    asyncio.run(game.start("", graph["difficulty"]))
    sent, events = [], []
    reading = {"Bonjour Plumette !": ("talk", "plumette"), "je regarde": ("talk", "plumette"),
               "t'as peur du noir toi ?": ("look", "aucun")}  # fmt: skip

    class Response:
        def __init__(self, said):
            self.said = said

        def raise_for_status(self):
            pass

        def json(self):
            v, o = reading[self.said]
            return {"answers": answers(1, (v, o, "aucun", "aucun")), "usage": {"cost": 0.0002}}

    def post(url, json, **k):
        sent.append(json)
        return Response(json["state"].split("L'enfant dit maintenant : « ")[1].split(" »")[0])

    monkeypatch.setattr(intent.httpx, "post", post)
    monkeypatch.setattr(adv.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    monkeypatch.setattr(game, "_ev", lambda type_, **kw: events.append((type_, kw)))
    monkeypatch.setattr(intent.engines, "_api_key", lambda *a: "key")

    def turn(said, narrator):
        game.on_user_message(said)
        if narrator:
            game.act(narrator, said=said)

    turn("Bonjour Plumette !", [{"verb": "talk", "object": "plumette"}])
    turn("je regarde", [{"verb": "look"}])  # the narrator did something else
    turn("t'as peur du noir toi ?", None)  # chit-chat: the narrator called no action
    game.stop()
    shadows = [kw for t, kw in events if t == "adventure_intent_shadow"]
    assert [(s["agrees"], s["false_action"]) for s in shadows] == [(True, False), (False, False), (False, True)]
    assert shadows[0]["actions"] == [{"verb": "talk", "object": "plumette"}] and shadows[0]["cost"] == 0.0002
    assert shadows[2]["narrator"] is None and shadows[2]["would_decide"]
    assert "Plumette te regarde." in sent[0]["state"] and sent[0]["provider"] == {"zdr": True}
    assert "Bonjour Plumette" in sent[1]["state"]  # what the child said just before


def test_off_by_default_and_a_failure_never_breaks_the_game(tmp_path, monkeypatch):
    w = World(next(g for g in adv.library() if g["title"] == "Le bonhomme de neige de Plumette"))

    def down(*a, **k):
        raise intent.httpx.ConnectError("down")

    monkeypatch.setattr(intent.httpx, "post", down)
    monkeypatch.setattr(intent.engines, "_api_key", lambda *a: "key")
    assert "error" in intent.decide({}, w, w.start(), "je prends la carotte")
    assert adv.AdventureGame({}, tmp_path, Kid())._intent_mode() == "narrator"
