"""Understanding the child with a decision model (TypeSafe's Jev, through OpenRouter's Decisions API).

Jev does not write: given the situation (the place, what is in reach by id, the places seen, what the
pet and the child just said) and typed questions (how many actions, then each action's verb, thing,
target and place, each a choice among the ids of this turn), it answers with probabilities, in about
0.3 s. On the intent benchmark (701 sentences labelled by hand, Nov. 2026), when its confidence is
0.7 or more (half the turns), it is right 97 % of the time.

The aim: Jev understands, the engine plays, the narrator only tells (one model call instead of two);
when Jev is unsure, the narrator asks the child, with the alternatives Jev hesitated between. For now
it runs in the shadow (`[adventure] intent = "jev-shadow"`): each turn, its decision is logged next
to the narrator's, and changes nothing."""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

import httpx

from aipet import engines
from aipet.games.adventure_engine import State, World, _key, canonical_verb

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"
CONFIDENT = 0.7  # at or above: it would decide alone (97 % right on the benchmark)
CLOSE = 0.15  # two options this close: an alternative worth asking about
SLOTS = 3
TIMEOUT = 5.0

# Model-facing (French).
VERBS = {
    "go": "aller, se déplacer, entrer, sortir, retourner quelque part", "take": "prendre, ramasser, attraper, cueillir",
    "drop": "poser, lâcher", "give": "donner, offrir, montrer une chose à quelqu'un",
    "use": "utiliser une chose (sur une autre), mettre, peindre, frotter",
    "combine": "assembler, attacher, mélanger deux choses", "open": "ouvrir", "close": "fermer",
    "talk": "parler à quelqu'un, saluer, appeler", "ask": "demander quelque chose à quelqu'un",
    "answer": "répondre à une devinette ou un mot de passe", "look": "regarder autour, décrire le lieu",
    "examine": "regarder de près une chose", "search": "fouiller, chercher ici",
    "inventory": "ce qu'il y a dans son sac", "push": "pousser, appuyer, presser (un bouton)", "pull": "tirer",
    "light": "allumer, éclairer", "throw": "lancer, jeter (vers quelqu'un)", "hit": "taper, frapper",
    "destroy": "casser, effacer, détruire pour de bon", "eat": "manger", "drink": "boire",
    "climb": "grimper, monter dans ou sur une chose", "swim": "nager, plonger", "jump": "sauter",
    "touch": "toucher, caresser, chatouiller", "listen": "écouter", "smell": "sentir, renifler", "fill": "remplir",
    "read": "lire", "wear": "porter, enfiler", "where": "demander où est une chose",
    "help": "demander de l'aide, un indice", "wait": "attendre", "sit": "s'asseoir", "sing": "chanter",
    "dance": "danser", "other": "autre geste imaginaire",
}  # fmt: skip
RULES = (
    "Découpe sa phrase en actions de jeu, dans l'ordre. Règles : parler à, toucher, regarder quelqu'un → objet = "
    "la personne ; donner, lancer, utiliser X sur Y → objet = X, cible = Y ; aller quelque part → vers = le lieu "
    "(voisin ou déjà vu) ; « lui », « y », « ça » renvoient à ce que le narrateur ou l'enfant vient de nommer ; "
    "un code de couleurs ou de boutons → une action push par bouton. Chaque action n'a que ses propres choses. "
    "Un déplacement dit en plusieurs mots (« je monte l'échelle et je vais sur la terrasse », « je sors et je "
    "vais au pont ») = UNE action go vers le lieu final. Demander de l'aide ou un indice à Flocon = l'action help. "
    "Regarder une chose précise = examine avec cette chose ; regarder autour = look. Dans « je l'allume au feu », "
    "« l' » est l'objet (ce qu'on allume) et le feu la cible. Une devinette : answer, objet = aucun, cible = qui "
    "la pose. Bavardage ou question hors du jeu → 0 action. Si la phrase parle de « ça », « le truc », « le "
    "machin » sans que rien ne soit nommé avant, ou si elle est incompréhensible → « pas clair »."
)
NONE = {"aucun": "aucune chose de la liste pour cette action (rien, ou une chose imaginaire)"}
FIELDS = (("objet", "object"), ("cible", "target"), ("vers", "to"))
FIELDS_OF = {"go": {"to"}, "climb": {"object", "to"}, "swim": {"to"}, "jump": {"to"}, "answer": {"object", "target"},
             "look": set(), "inventory": set(), "help": set(), "wait": set(), "dance": set(), "sing": {"object"},
             "sit": set()}  # fmt: skip


def request(w: World, st: State, said: str, before: str = "", told: str = "") -> dict:
    """The Decisions request for this turn: the situation, and one choice per question, among this
    turn's ids."""
    reach = w.in_reach(st)
    things = {i: n for i, n in reach.items() if not n.startswith("→")}
    ways = {i: n for i, n in reach.items() if n.startswith("→")}
    far = {p: f"lieu déjà vu : {w.name(p, st)}" for p in w.places
           if p in st.visited and p != st.place and p not in ways}  # fmt: skip
    riddles = w.riddles_here(st)

    def listed(d: dict) -> str:
        return "; ".join(f"{i} = {n}" for i, n in d.items()) or "aucun"

    state = (
        "Un enfant de 5 à 8 ans joue à voix haute (reconnaissance vocale, phrases parfois maladroites) à une "
        f"aventure dont il est le héros.\nLieu : {w.name(st.place, st)}.\n"
        f"Le narrateur vient de dire : « {told[-500:]} »\n"
        + (f"Juste avant, l'enfant avait dit : « {before} »\n" if before else "")
        + f"Choses et personnages à portée (id = nom) : {listed(things)}\n"
        f"Chemins d'ici (id = nom) : {listed(ways)}\n"
        f"Lieux déjà vus, plus loin (id = nom) : {listed(far)}\n"
        + (f"Devinette posée ici par : {', '.join(riddles)}\n" if riddles else "")
        + f"\nL'enfant dit maintenant : « {said} »\n\n{RULES}"
    )
    other = {"autre": "une chose qui n'est pas dans la liste"}
    q: dict[str, Any] = {"nombre": {"type": "choice", "instructions": "combien d'actions de jeu la phrase demande",
                                    "criteria": {"0": "aucune : bavardage, question hors du jeu", "1": "une action",
                                                 "2": "deux actions", "3": "trois actions ou plus",
                                                 "?": "pas clair : on ne sait pas de quoi il parle, il faut lui demander"}}}  # fmt: skip
    for n in range(1, SLOTS + 1):
        q[f"a{n}_verbe"] = {"type": "choice", "instructions": f"le verbe de l'action {n}",
                            "criteria": {**VERBS, "rien": f"pas d'action {n}"}}  # fmt: skip
        q[f"a{n}_objet"] = {"type": "choice", "criteria": {**things, **NONE, **other}, "instructions":
                            f"l'objet de l'action {n} (la chose prise, donnée, utilisée, regardée, ou la personne à qui on parle)"}  # fmt: skip
        q[f"a{n}_cible"] = {"type": "choice", "criteria": {**things, **NONE, **other}, "instructions":
                            f"la cible de l'action {n} (à qui on donne, sur quoi on utilise ou lance), sinon aucun"}  # fmt: skip
        q[f"a{n}_vers"] = {"type": "choice", "criteria": {**ways, **far, **NONE, "autre": "un lieu inconnu"},
                           "instructions": f"le lieu où va l'action {n} si c'est un déplacement, sinon aucun"}  # fmt: skip
    return {"model": MODEL, "provider": {"zdr": True}, "state": state, "questions": q}


def read(answers: dict, said: str) -> dict:
    """The decision: {actions, confidence, unclear, hesitations}. `actions` [] = no game action (chat,
    or unclear). Each verb keeps only its fields; an answer is the child's own words.
    `hesitations`: the fields where two options were close (what to ask the child about)."""
    top = answers["nombre"]
    conf = top.get("confidence", 1.0)
    hesitations = _close("nombre", top)
    if top["choice"] == "?":
        return {"actions": [], "confidence": conf, "unclear": True, "hesitations": hesitations}
    actions = []
    for n in range(1, int(top["choice"]) + 1):
        verb = answers[f"a{n}_verbe"]["choice"]
        if verb == "rien":
            break
        x: dict[str, str] = {"verb": verb}
        for fr, en in FIELDS:
            a = answers[f"a{n}_{fr}"]
            conf = min(conf, a.get("confidence", 1.0))
            hesitations += _close(f"a{n}_{fr}", a)
            if a["choice"] not in ("aucun", "autre"):
                x[en] = a["choice"]
        conf = min(conf, answers[f"a{n}_verbe"].get("confidence", 1.0))
        hesitations += _close(f"a{n}_verbe", answers[f"a{n}_verbe"])
        if x.get("target") and x.get("target") == x.get("object"):
            x.pop("target")
        if verb == "answer":
            x = {"verb": "answer", "object": said, "target": x.get("target") or x.get("object", "")}
        keep = FIELDS_OF.get(verb, {"object", "target"})
        actions.append({k: v for k, v in x.items() if k == "verb" or k in keep})
    return {"actions": actions, "confidence": conf, "unclear": False, "hesitations": hesitations}


def _close(name: str, answer: dict) -> list[dict]:
    probs = sorted((answer.get("probabilities") or {}).items(), key=lambda kv: -kv[1])
    if len(probs) >= 2 and probs[0][1] - probs[1][1] <= CLOSE and probs[0][1] < 0.8:
        return [{"question": name, "between": [probs[0][0], probs[1][0]]}]
    return []


def decide(cfg: dict, w: World, st: State, said: str, before: str = "", told: str = "") -> dict:
    """Ask Jev (one HTTP call). Adds `seconds` and `cost`; `error` instead when it failed."""
    key = engines._api_key(engines.settings(cfg, "conversation"), "openrouter")
    t = time.monotonic()
    try:
        r = httpx.post(DECISIONS_URL, json=request(w, st, said, before, told), timeout=TIMEOUT,
                       headers={"Authorization": f"Bearer {key}"})  # fmt: skip
        r.raise_for_status()
        body = r.json()
        out = read(body["answers"], said)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "seconds": round(time.monotonic() - t, 2)}
    return {**out, "seconds": round(time.monotonic() - t, 2), "cost": body.get("usage", {}).get("cost", 0)}


# ---- comparing two ways to play the same sentence -------------------------------------------------
def heard(res: dict) -> str:
    """What the child would be told: the outcome, the engine's texts, the things it is about."""
    keep = ("outcome", "text", "description", "about", "changes", "question", "not_here", "ending")
    return json.dumps({k: res[k] for k in keep if res.get(k)}, sort_keys=True, ensure_ascii=False, default=str)


def walk(w: World, st: State, actions: list[dict]) -> list[dict]:
    """A go to a far place already seen becomes the walk there, along ways open now."""
    out, trial = [], st.copy()
    for a in actions:
        to = str(a.get("to") or "")
        steps = [a]
        if canonical_verb(a.get("verb", "")) == "go" and to in w.places and to not in {x["to"] for x in w.exits(trial)}:
            steps = [{"verb": "go", "to": p} for p in (_route(w, trial, to) or [to])]
        for s in steps:
            out.append(s)
            w.act(trial, s)
    return out


def _route(w: World, st: State, to: str) -> list[str] | None:
    seen: dict[str, list[str]] = {st.place: []}
    todo = deque([st])
    while todo:
        s = todo.popleft()
        if s.place == to:
            return seen[s.place]
        for x in w.exits(s):
            n = s.copy()
            if w.act(n, {"verb": "go", "to": x["to"]}).get("outcome") == "done" and n.place not in seen:
                seen[n.place] = [*seen[s.place], n.place]
                todo.append(n)
    return None


def outcome(w: World, st: State, actions: list[dict]) -> tuple:
    """(the world after, what the child is told), playing on a copy; stops at the first that fails."""
    trial, told = st.copy(), []
    for a in walk(w, st, actions):
        res = w.act(trial, {k: v for k, v in a.items() if v})
        told.append(heard(res))
        if res.get("outcome") not in ("done", "hint", "free"):
            break
    return _key(trial, w), tuple(told)


def agrees(w: World, st: State, jev: list[dict], narrator: list[dict]) -> bool:
    """Would Jev's actions do what the narrator's did (the same world, the same told)?"""
    if not jev or not narrator:
        return not jev and not narrator
    return outcome(w, st, jev) == outcome(w, st, narrator)
