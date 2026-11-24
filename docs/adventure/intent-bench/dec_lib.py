"""Shared by the intent benchmark: worlds by title, a case's state, playing intents (a far place is
walked to along open ways), and the outcome to compare."""
import functools, json
from pathlib import Path
from collections import deque
from aipet.games import adventure as adv
from aipet.games.adventure_engine import World, State, _key, canonical_verb

ROOT = str(Path(__file__).resolve().parents[3])  # the repository

@functools.cache
def graphs():
    g = {x["title"]: x for x in adv.library()}
    g["Le phare de Piko (nouvelle version)"] = json.load(open(f"{ROOT}/docs/adventure/model/le-phare-de-piko.json"))
    return g

@functools.cache
def world(title):
    return World(graphs()[title])

def state(case):
    return State.from_dict(case["state"])

def route(w, st, to):
    """The places to go through to reach `to` (a place id), along ways open now; None if none."""
    seen, todo = {st.place: []}, deque([st])
    while todo:
        s = todo.popleft()
        if s.place == to:
            return seen[s.place]
        for x in w.exits(s):
            n = s.copy()
            if w.act(n, {"verb": "go", "to": x["to"]}).get("outcome") == "done" and n.place not in seen:
                seen[n.place] = seen[s.place] + [n.place]
                todo.append(n)
    return None

def expand(w, st, actions):
    """A go to a far known place becomes the walk there."""
    out, trial = [], st.copy()
    for a in actions:
        to = a.get("to")
        if canonical_verb(a.get("verb", "")) == "go" and to in w.places and to not in {x["to"] for x in w.exits(trial)}:
            path = route(w, trial, to)
            steps = [{"verb": "go", "to": p} for p in (path or [to])]
        else:
            steps = [a]
        for s in steps:
            out.append(s)
            w.act(trial, s)
    return out

def heard(r):
    """What the child would be told: the outcome, the engine's texts, the things it is about."""
    keep = ("outcome", "text", "description", "about", "changes", "question", "not_here", "ending")
    return json.dumps({k: r[k] for k in keep if r.get(k)}, sort_keys=True, ensure_ascii=False, default=str)

def play(case, actions):
    """(the state's key after, the outcomes, what the child is told) — the turn stops at the first that
    fails, as in the game."""
    w, st = world(case["title"]), state(case)
    outs, told = [], []
    for a in expand(w, st, actions):
        r = w.act(st, {k: v for k, v in a.items() if v not in (None, "", "aucun")})
        outs.append(r.get("outcome"))
        told.append(heard(r))
        if r.get("outcome") not in ("done", "hint", "free"):
            break
    return _key(st, w), outs, tuple(told)

def context(case):
    """What a model is shown: the place, what is in reach (ids), the bag, the places known, the narrator's last words."""
    w, st = world(case["title"]), state(case)
    reach = w.in_reach(st)
    known = {p: w.name(p, st) for p in w.places if p in st.visited and p != st.place}
    return {"lieu": w.name(st.place, st), "lieu_id": st.place, "à_portée": reach,
            "sac": {i: w.name(i, st) for i in st.inventory},
            "lieux_connus": known, "dit_avant": case.get("before", ""), "narrateur": case.get("told", ""),
            "enfant": case["said"], "devinettes_ici": [r for r in w.riddles_here(st)]}
