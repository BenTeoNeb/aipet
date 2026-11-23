"""The solver: every reachable state, the ways to win, and the agreement between the solver and the turn."""

from __future__ import annotations

from collections import deque
from functools import partial
from typing import TYPE_CHECKING

from storyworld.hints import (
    _trigger_for,
)
from storyworld.model import (
    State,
)

if TYPE_CHECKING:
    from storyworld.world import World

MAX_STATES = 60_000  # a bigger adventure is refused (« too big to check »)


# ---- solver -------------------------------------------------------------------------------------
def _moves(w: World, st: State, reduce: bool):
    """Every useful thing the hero can do from `st`: (step, next state). With `reduce`, an object
    that can safely be taken is taken first, without exploring the other orders."""
    takeable = [i for i in w.visible_items(st) if w.items[i].get("takeable", True)]
    eager = [i for i in takeable if i in w.eager_ok] if reduce else []
    for i in eager[:1] or takeable:
        n = State.from_dict(st.to_dict())
        n.items_at[i] = "inv"
        n.inventory.append(i)
        yield ("take", i), n
    if eager:
        return

    can_open = partial(w.can_open, st)

    for x in w.exits(st):  # a closed door opens on the way (`World._through`)
        door = x.get("door")
        if (
            x.get("to") in w.places
            and w.holds(st, x.get("when"))
            and (not door or w.prop(st, door, "open") or can_open(door))
        ):
            n = State.from_dict(st.to_dict())
            if door:
                w.apply(n, [f"open:{door}"])
            w.enter(n, x["to"])
            yield ("go", x["to"]), n
    doors = {d for ds in w.doors.values() for d in ds}
    for i in w.visible_items(st):
        if w.items[i].get("openable") and i not in doors and can_open(i):
            n = State.from_dict(st.to_dict())
            w.apply(n, [f"open:{i}"])
            yield ("open", i), n
    for b in w.visible_items(st):  # the next button of a code (a wrong one only starts it again)
        box = w.buttons.get(b)
        if box and not w.prop(st, box, "open") and w.items[box]["code"]["press"][w.prop(st, box, "pressed") or 0] == b:
            n = State.from_dict(st.to_dict())
            w.press(n, b)
            yield ("press", b), n
    for r in w.riddles_here(st):  # the right answer (a wrong one changes nothing)
        n = State.from_dict(st.to_dict())
        w.solve_riddle(n, r)
        yield ("answer", r), n
    scope = set(w.scope(st))
    tried: set[str] = set()
    for a in w.rules:
        if a["id"] in tried or a.get("place") and a["place"] != st.place:
            continue
        if a["id"] in st.done_actions and not a.get("repeatable") and a["id"] not in w.idempotent_ids:
            continue
        if any(x and x not in scope for x in (a.get("object"), a.get("target"))):
            continue
        if not w.holds(st, a.get("when")) or w.red_herring(a) or w.achieved(a, st):
            continue
        tried.add(a["id"])
        n = State.from_dict(st.to_dict())
        t = _trigger_for(w, a["id"], st)  # the words a narrator sends (`intent_for`): the turn takes what they name
        w.builtins.implicit_take(n, t.get("verb", ""), t.get("object"), t.get("target"))
        w.apply(n, a.get("effects", []))
        n.done_actions.append(a["id"])
        yield ("act", a["id"]), n


def _key(st: State, w: World) -> tuple:
    """Done actions matter only when doing them again would change something (gains, losses); the
    others are skipped once their effects hold (`World.achieved`), whichever variant did it."""
    done = frozenset(a for a in st.done_actions if a not in w.idempotent_ids)
    return (*st.key(), done, frozenset(st.visited) if w.uses_visited else None)


def solve(w: World, start: State | None = None, first_path: bool = False, max_states: int = MAX_STATES) -> dict:
    """Explore every reachable state. Returns {wins: {ending: shortest length}, loses: set, stuck:
    an example path to a state from which no win is reachable, path: shortest path to a win,
    states: count, complete: False if the exploration was cut}.

    Taking an object first, without trying the other orders, is only exact when it can never be
    missed later: the object is never forbidden (« !has: ») nor hidden, and the hero can always walk
    back (`World.eager_ok`). A path search (`first_path`) always takes first: it only needs one way."""
    s0 = State.from_dict((start or w.start()).to_dict())
    k0 = _key(s0, w)
    parent: dict[tuple, tuple[tuple | None, tuple | None]] = {k0: (None, None)}
    states = {k0: s0}
    edges: dict[tuple, list[tuple]] = {}
    queue = deque([k0])
    wins: dict[str, int] = {}
    loses: set[str] = set()
    depth = {k0: 0}
    best_win: tuple | None = None
    win_at: dict[str, tuple] = {}
    fired: set[str] = set()
    while queue and len(states) < max_states:
        k = queue.popleft()
        st = states[k]
        if st.ending:
            e = w.ending(st, st.ending)
            if e.get("kind") == "win":
                if st.ending not in wins:
                    wins[st.ending] = depth[k]
                    win_at[st.ending] = k
                if best_win is None:
                    best_win = k
                    if first_path:
                        break
            else:
                loses.add(st.ending)
            continue
        for step, n in _moves(w, st, reduce=True):
            if step[0] == "act":
                fired.add(step[1])
            nk = _key(n, w)
            edges.setdefault(k, []).append(nk)
            if nk not in states:
                states[nk], parent[nk], depth[nk] = n, (k, step), depth[k] + 1
                queue.append(nk)
    complete = not queue
    out: dict = {"wins": wins, "loses": loses, "states": len(states), "complete": complete, "fired": fired}
    if best_win:
        out["path"] = _path(parent, best_win)
        out["shortest"] = len(out["path"])
        out["win_paths"] = {e: _path(parent, k) for e, k in win_at.items()}
    if first_path or not complete:
        return out
    # States from which a win is still reachable (backwards from the wins).
    reverse: dict[tuple, list[tuple]] = {}
    for a, bs in edges.items():
        for b in bs:
            reverse.setdefault(b, []).append(a)
    good = {k for k, s in states.items() if s.ending and w.ending(s, s.ending).get("kind") == "win"}
    todo = deque(good)
    while todo:
        for a in reverse.get(todo.popleft(), []):
            if a not in good:
                good.add(a)
                todo.append(a)
    stuck = [k for k, s in states.items() if not s.ending and k not in good]
    if stuck:
        out["stuck"] = _path(parent, min(stuck, key=lambda k: depth[k]))
    return out


def _path(parent: dict, k: tuple) -> list[tuple]:
    steps = []
    while parent[k][0] is not None:
        k, step = parent[k]
        steps.append(step)
    return steps[::-1]


def intent_for(w: World, st: State, step: tuple) -> dict:
    """How a narrator would send a solver step to `World.act` (by ids)."""
    kind, ident = step
    if kind == "go":
        return {"verb": "go", "to": ident}
    if kind in ("take", "open"):
        return {"verb": kind, "object": ident}
    if kind == "press":
        return {"verb": "push", "object": ident}
    if kind == "answer":
        return {"verb": "answer", "object": w.thing(ident)["answer"]["accept"][0], "target": ident}
    t = _trigger_for(w, ident, st)
    return {k: t.get(k, "") for k in ("verb", "object", "target")}


def disagreements(w: World, max_states: int = MAX_STATES, reduce: bool = True) -> list[str]:
    """Every move the solver counts on, played through `World.act` from every reachable state: where
    the turn does something else (the checker and the game must agree on every edge, not only on
    the ways to win). `reduce`: the moves the solver explores (what `check` proves with); without it,
    every move (slower: for the tests)."""
    s0 = w.start()
    seen, todo = {_key(s0, w)}, [s0]
    out: list[str] = []
    while todo and len(seen) < max_states and len(out) < 5:
        st = todo.pop()
        if st.ending:
            continue
        for step, nxt in _moves(w, st, reduce=reduce):
            trial = State.from_dict(st.to_dict())
            trial.warned = [a["id"] for a in w.actions]  # a loss is warned once first: past that
            intent = intent_for(w, trial, step)
            res = w.act(trial, intent)
            if _key(trial, w) != _key(nxt, w):
                said = " ".join(str(v) for v in intent.values() if v)
                out.append(f"from {st.place} ({', '.join(st.inventory) or 'empty bag'}), {step[0]} {step[1]} "
                           f"(« {said} ») answers {res.get('outcome')}: {res.get('text', '')}"[:300])  # fmt: skip
            if (k := _key(nxt, w)) not in seen:
                seen.add(k)
                todo.append(nxt)
    return out


def replay(w: World, path: list[tuple]) -> str:
    """Play a solver's path through `World.act`, as a child would say it: "" when every step does
    what the solver did, else the first step that doesn't (the solver and the turn must agree)."""
    st = w.start()
    for n, (kind, ident) in enumerate(path, 1):
        intent = intent_for(w, st, (kind, ident))
        before = list(st.done_actions)
        res = w.act(st, intent)
        said = " ".join(str(v) for v in intent.values() if v)
        if res.get("outcome") not in ("done", "end") or res.get("already"):
            return f"step {n} ({kind} {ident}, said « {said} ») answers {res.get('outcome')}: {res.get('text', '')}"
        done = st.done_actions[len(before) :]
        if kind == "act" and done != [ident]:
            return f"step {n}: « {said} » does {done or 'nothing'} instead of action {ident}"
    return ""
