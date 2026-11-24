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
from storyworld.locale.fr import FRENCH

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


# ---- the turn never leaves the solver's states (what « never stuck » needs) ----------------------
def _all_states(w: World, limit: int = 3000) -> dict:
    """Every state the solver can reach, every move (no reduction, codes and riddles played even
    before their clue: a child may guess)."""
    from storyworld.solver import _key, _moves

    s0 = w.start()
    states, todo = {_key(s0, w): s0}, [s0]
    while todo and len(states) < limit:
        st = todo.pop()
        if st.ending:
            continue
        for _, n in _moves(w, st, reduce=False, fair=False):
            if (k := _key(n, w)) not in states:
                states[k] = n
                todo.append(n)
    return states


def _intents(w: World, st: State) -> list[dict]:
    """What a narrator may send there: every verb on every thing in reach (two things for the verbs
    that apply one to another), every way, every riddle's answers. Destroying is outside the proof."""
    from storyworld.verbs import APPLY

    verbs = [v for v in FRENCH.verbs if v not in ("destroy", "improvise", "go", "answer")]
    things = [*w.scope(st)]
    out = [{"verb": v, "object": x} for v in verbs for x in [None, *things]]
    out += [{"verb": v, "object": x, "target": y} for v in sorted(APPLY) for x in things for y in things if x != y]
    out += [{"verb": "go", "to": x.get("to")} for x in w.exits(st)]
    for r in w.riddles_here(st):
        out += [{"verb": "answer", "object": a, "target": r} for a in [*w.thing(r)["answer"]["accept"], "zut"]]
    return out


CLOSED = [REFERENCE, *(g for g, _ in PATTERNS.values())]


@pytest.mark.parametrize("graph", CLOSED, ids=lambda g: g["title"][:30] + str(len(g["places"])))
def test_the_turn_never_leaves_the_solvers_states(graph):
    from storyworld.solver import _key

    w = World(graph)
    states = _all_states(w)
    live = [s for s in states.values() if not s.ending]
    for st in random.Random(3).sample(live, min(len(live), 40)):
        for intent in _intents(w, st):
            trial = State.from_dict(st.to_dict())
            trial.warned = [a["id"] for a in w.actions]  # past the one warning before a loss
            w.act(trial, intent)
            assert _key(trial, w) in states, (st.place, intent)


@pytest.mark.parametrize("graph", [g for g in library() if g["difficulty"] == "facile"], ids=lambda g: g["title"][:30])
def test_the_turn_never_leaves_the_solvers_states_in_the_bank(graph):
    from storyworld.solver import _key

    w = World(graph)
    states = _all_states(w)
    rng = random.Random(5)
    for st in rng.sample([s for s in states.values() if not s.ending], 25):
        for intent in _intents(w, st):
            trial = State.from_dict(st.to_dict())
            trial.warned = [a["id"] for a in w.actions]
            w.act(trial, intent)
            assert _key(trial, w) in states, (st.place, intent)


# ---- the take-first reduction, on random worlds (dark places, lights, uses) ----------------------
def _random_world(rng: random.Random) -> dict:
    from aipet.games.adventure_patterns import act, adventure, person, place, thing

    ps = ["a", "b", "c"]
    door = rng.random() < 0.4
    places = [place("a", ("b", "est")),
              place("b", ("a", "ouest"), ("c", "est", *([{"door": "porte"}] if door else [])), dark=rng.random() < 0.6),
              place("c", ("b", "ouest"), dark=rng.random() < 0.5)]  # fmt: skip

    def uses():
        return {"uses": rng.choice([1, 2])} if rng.random() < 0.6 else {}

    items = [thing("lampe", rng.choice(ps), light=rng.choice([True, ["is:lampe:on"]]),
                   states=[{"id": "off", "name": "la lampe"}, {"id": "on", "name": "la lampe"}], **uses()),
             thing("bougie", rng.choice([*ps, ""]), light=True, **uses()),
             thing("seau", rng.choice(ps), **uses()), thing("tresor", rng.choice(ps)),
             thing("coffre", rng.choice(ps), "container"), thing("perle", rng.choice(["coffre", "", "roi"]))]  # fmt: skip
    if door:
        items.append(thing("porte", "", "door", **({"locked": True, "key": "tresor"} if rng.random() < 0.5 else {})))
    chars = [person("roi", rng.choice(ps)), person("chat", rng.choice(ps))]
    ids = ["lampe", "bougie", "seau", "tresor", "perle", "roi", "chat"]
    usable = [i["id"] for i in items if i.get("uses")]
    conds = ["has:lampe", "has:seau", "flag:f1", "is:lampe:on", "is:lampe:off", "has:perle", "open:coffre",
             *(f"charged:{u}" for u in usable)]  # fmt: skip
    effs = ["flag:f1", "set:lampe:on", "set:lampe:off", "reveal:bougie", "reveal:perle", "lose:seau", "lose:lampe",
            "follow:chat", "goto:c", "open:coffre", *(f"spend:{u}" for u in usable), *(f"refill:{u}" for u in usable)]  # fmt: skip
    verbs = ["use", "give", "light", "fill", "listen", "push"]
    acts = [act(f"a{n}", [(rng.choice(verbs), *rng.sample(ids, rng.choice([1, 2])))], rng.sample(effs, rng.randint(1, 3)),
                rng.sample(conds, rng.randint(0, 2)), **({"repeatable": True} if rng.random() < 0.3 else {}))
            for n in range(rng.randint(3, 6))]  # fmt: skip
    acts.append(act("fin", [("give", "tresor", "roi")], ["end:gagne"], ["has:tresor"]))

    def reaction():  # Inform's « after »: gated on what the hero holds or what is true
        return {
            "when": rng.sample(conds, rng.randint(0, 2)),
            "effects": rng.sample(effs, rng.randint(1, 2)),
            "text": "!",
        }

    for i in items:
        if i["id"] == "coffre" and rng.random() < 0.4:
            i["after"] = {"open": reaction()}
        elif i["id"] in ("seau", "perle") and rng.random() < 0.3:
            i["after"] = {"take": reaction()}
    if rng.random() < 0.3:
        places[2]["after"] = {"enter": reaction()}
    return adventure(places, items, chars, acts)


def test_taking_first_keeps_the_verdict_on_random_dark_worlds():
    rng = random.Random(23)
    checked = 0
    for _ in range(120):
        g = _random_world(rng)
        w = World(g)
        reduced = solve(w, max_states=1500)
        full = World(g)
        full.eager_ok = set()
        exact = solve(full, max_states=6000)
        if not (reduced["complete"] and exact["complete"]):
            continue
        checked += 1
        assert set(reduced["wins"]) == set(exact["wins"]), g["actions"]
        assert bool(reduced.get("stuck")) == bool(exact.get("stuck")), g["actions"]
    assert checked > 70
