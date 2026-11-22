"""Properties of the storyworld library that must hold for any adventure, not only for examples:
the turn does what the solver counts on (every edge), checking and playing never crash on odd input,
states survive JSON, migration is stable."""

import json
import random
from pathlib import Path

import pytest

from aipet.games.adventure import library
from aipet.games.adventure_patterns import PATTERNS
from storyworld import State, World, disagreements, migrate, solve, validate

REFERENCE = json.loads((Path(__file__).parent.parent / "docs/adventure/model/le-phare-de-piko.json").read_text())
SMALL = [REFERENCE, *(g for g, _ in PATTERNS.values()), *(g for g in library() if g["difficulty"] == "facile")]


@pytest.mark.parametrize("graph", SMALL, ids=lambda g: g["title"][:30])
def test_every_solver_edge_is_what_the_turn_does(graph):
    assert disagreements(World(graph), reduce=False) == []


@pytest.mark.parametrize("graph", SMALL, ids=lambda g: g["title"][:30])
def test_taking_first_never_changes_the_verdict(graph):
    """The solver's eager pickup is a reduction: winnable, losable and stuck stay the same."""
    w = World(graph)
    full = _explore(w)
    reduced = solve(w)
    assert set(reduced["wins"]) == full["wins"] and set(reduced["loses"]) == full["loses"]
    assert bool(reduced.get("stuck")) == full["stuck"]


def _explore(w: World) -> dict:
    from storyworld.solver import _key, _moves

    s0 = w.start()
    seen, todo, edges, states = {_key(s0, w)}, [s0], {}, {_key(s0, w): s0}
    while todo:
        st = todo.pop()
        if st.ending:
            continue
        for _, n in _moves(w, st, reduce=False):
            k = _key(n, w)
            edges.setdefault(_key(st, w), []).append(k)
            if k not in seen:
                seen.add(k)
                states[k] = n
                todo.append(n)
    wins = {s.ending for s in states.values() if s.ending and w.ending(s, s.ending).get("kind") == "win"}
    loses = {s.ending for s in states.values() if s.ending and w.ending(s, s.ending).get("kind") == "lose"}
    good = {k for k, s in states.items() if s.ending in wins}
    changed = True
    while changed:
        changed = False
        for a, bs in edges.items():
            if a not in good and any(b in good for b in bs):
                good.add(a)
                changed = True
    stuck = any(not s.ending and k not in good for k, s in states.items())
    return {"wins": wins, "loses": loses, "stuck": stuck}


ODD = [None, 3, 2.5, True, [], {}, [1, "a"], "", " ", "!", ":", "has:", "x" * 300, "é", "\u0000", "pinces", "porte"]


def _odd(rng: random.Random):
    return rng.choice(ODD)


def test_validate_never_raises_on_broken_graphs():
    rng = random.Random(7)
    for _ in range(300):
        g = json.loads(json.dumps(REFERENCE))
        for _ in range(rng.randint(1, 6)):
            section = rng.choice(["places", "items", "characters", "actions", "endings"])
            if not g[section]:
                continue
            entity = rng.choice(g[section])
            key = rng.choice(list(entity) + ["when", "effects", "triggers", "states", "at", "exits"])
            entity[key] = _odd(rng)
        diags, _ = validate(g)  # never raises: every problem is a diagnostic
        assert all(d.code and d.message for d in diags)


def test_a_turn_never_raises_on_odd_intents():
    rng = random.Random(11)
    w = World(REFERENCE)
    st = w.start()
    verbs = ["go", "take", "give", "use", "open", "push", "destroy", "where", "answer", "talk", "help", "xyz", 3, None]
    places = list(w.places)
    for _ in range(600):
        intent = {"verb": rng.choice(verbs), "object": _odd(rng) if rng.random() < 0.5 else rng.choice(list(w.items)),
                  "target": _odd(rng), "to": rng.choice(places + ODD)}  # fmt: skip
        out = w.act(st, intent)
        assert out["outcome"]
        if st.ending:
            st = w.start()
        st.place = rng.choice(places)


def test_a_state_survives_json():
    w = World(REFERENCE)
    st = w.start()
    for intent in [{"verb": "go", "to": "rochers"}, {"verb": "give", "object": "algue", "target": "pinces"},
                   {"verb": "take", "object": "cle"}]:  # fmt: skip
        w.act(st, intent)
    again = State.from_dict(json.loads(json.dumps(st.to_dict())))
    assert again.key() == st.key() and again.to_dict() == st.to_dict()


@pytest.mark.parametrize("graph", library(), ids=lambda g: g["title"][:30])
def test_migration_is_stable_and_keeps_the_verdict(graph):
    once = migrate(graph)
    assert migrate(once) is once  # already current: returned as is
    before, after = solve(World(graph)), solve(World(once))
    assert before["wins"] == after["wins"] and before["loses"] == after["loses"] and before["states"] == after["states"]
