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
def _moves(w: World, st: State, reduce: bool, fair: bool = True):
    """Every useful thing the hero can do from `st`: (step, next state), with the clues met there.
    With `reduce`, an object that can safely be taken is taken first, without exploring the other
    orders. `fair`: a code or a riddle is played only once its clue is known (a child doesn't know
    the answer before meeting it)."""
    for step, n in _all_moves(w, st, reduce, fair):
        w.react(st, n)
        w.learn(n)
        yield step, n


def _all_moves(w: World, st: State, reduce: bool, fair: bool):
    takeable = [i for i in w.visible_items(st) if w.items[i].get("takeable", True)]
    eager = [i for i in takeable if i in w.eager_ok] if reduce else []
    for i in eager[:1] or takeable:
        n = st.copy()
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
            n = st.copy()
            if door:
                w.apply(n, [f"open:{door}"])
            w.enter(n, x["to"])
            yield ("go", x["to"]), n
    doors = {d for ds in w.doors.values() for d in ds}
    for i in w.visible_items(st):
        if w.items[i].get("openable") and i not in doors and can_open(i):
            n = st.copy()
            w.apply(n, [f"open:{i}"])
            yield ("open", i), n
    for b in w.visible_items(st):  # the next button of a code (a wrong one only starts it again)
        box = w.buttons.get(b)
        if (box and not w.prop(st, box, "open") and (not fair or w.knows(st, box))
                and w.items[box]["code"]["press"][w.prop(st, box, "pressed") or 0] == b):  # fmt: skip
            n = st.copy()
            w.press(n, b)
            yield ("press", b), n
    for r in w.riddles_here(st):  # the right answer (a wrong one changes nothing)
        if fair and not w.knows(st, r):
            continue
        n = st.copy()
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
        n = st.copy()
        # The trigger a child would say (`intent_for`), and what the turn takes then: another trigger
        # that takes more is that take, then this.
        w.builtins.take_for(n, _trigger_for(w, a["id"], st))
        w.apply(n, a.get("effects", []))
        n.done_actions.append(a["id"])
        yield ("act", a["id"]), n


def _key(st: State, w: World) -> tuple:
    """Done actions matter only when doing them again would change something (gains, losses); the
    others are skipped once their effects hold (`World.achieved`), whichever variant did it."""
    done = frozenset(a for a in st.done_actions if a not in w.idempotent_ids)
    return (*st.key(), done, frozenset(st.visited) if w.uses_visited else None, frozenset(st.known))


def _fair(w: World, st: State, step: tuple) -> bool:
    """Could a child do this step knowing only what they met (a code or an answer needs its clue)?"""
    kind, ident = step
    if kind == "press":
        return w.knows(st, w.buttons.get(ident, ident))
    return kind != "answer" or w.knows(st, ident)


def solve(w: World, start: State | None = None, first_path: bool = False, max_states: int = MAX_STATES) -> dict:
    """Explore every reachable state. Returns {wins: {ending: shortest length}, loses: set, stuck:
    an example path to a state from which no win is reachable, path: shortest path to a win,
    states: count, complete: False if the exploration was cut}.

    Fair play: every state is explored, even those a lucky guess reaches (a code pressed before its
    clue is met); a win counts only along steps a child can do knowing what they met. A state a guess
    reaches from which no fair way wins is stuck.

    Taking an object first, without trying the other orders, is only exact when it can never be
    missed later: the object is never forbidden (« !has: ») nor hidden, and the hero can always walk
    back (`World.eager_ok`). A path search (`first_path`) always takes first, fairly: it only needs
    one way."""
    s0 = (start or w.start()).copy()
    k0 = _key(s0, w)
    parent: dict[tuple, tuple[tuple | None, tuple | None]] = {k0: (None, None)}
    states = {k0: s0}
    edges: dict[tuple, list[tuple[tuple, tuple, bool]]] = {}  # state → (next, step, fair)
    queue = deque([k0])
    loses: set[str] = set()
    fired: set[str] = set()

    def won(k: tuple) -> bool:
        s = states[k]
        return bool(s.ending) and w.ending(s, s.ending or "").get("kind") == "win"

    found: tuple | None = None
    while queue and len(states) < max_states:
        k = queue.popleft()
        st = states[k]
        if st.ending:
            if won(k):
                if first_path:
                    found = k
                    break
            else:
                loses.add(st.ending)
            continue
        for step, n in _moves(w, st, reduce=True, fair=first_path):
            if step[0] == "act":
                fired.add(step[1])
            nk = _key(n, w)
            edges.setdefault(k, []).append((nk, step, first_path or _fair(w, st, step)))
            if nk not in states:
                states[nk], parent[nk] = n, (k, step)
                queue.append(nk)
    complete = not queue
    out: dict = {"loses": loses, "states": len(states), "complete": complete, "fired": fired}
    if first_path:
        out["wins"] = {states[found].ending: len(_path(parent, found))} if found else {}
        if found:
            out["path"] = _path(parent, found)
            out["shortest"] = len(out["path"])
        return out
    # The ways to win a child can follow: breadth first over the fair steps.
    fair_parent: dict[tuple, tuple[tuple | None, tuple | None]] = {k0: (None, None)}
    todo = deque([k0])
    wins: dict[str, int] = {}
    win_at: dict[str, tuple] = {}
    while todo:
        k = todo.popleft()
        if won(k) and states[k].ending not in wins:
            win_at[states[k].ending or ""] = k
            wins[states[k].ending or ""] = len(_path(fair_parent, k))
        for nk, step, fair in edges.get(k, []):
            if fair and nk not in fair_parent:
                fair_parent[nk] = (k, step)
                todo.append(nk)
    out["wins"] = wins
    if win_at:
        best = min(win_at.values(), key=lambda k: len(_path(fair_parent, k)))
        out["path"] = _path(fair_parent, best)
        out["shortest"] = len(out["path"])
        out["win_paths"] = {e: _path(fair_parent, k) for e, k in win_at.items()}
    if not complete:
        return out
    # States from which a win is still reachable fairly (backwards from the wins, along fair steps).
    reverse: dict[tuple, list[tuple]] = {}
    for a, bs in edges.items():
        for b, _, fair in bs:
            if fair:
                reverse.setdefault(b, []).append(a)
    good = {k for k in states if won(k)}
    back = deque(good)
    while back:
        for a in reverse.get(back.popleft(), []):
            if a not in good:
                good.add(a)
                back.append(a)
    stuck = [k for k, s in states.items() if not s.ending and k not in good]
    if stuck:
        out["stuck"] = _path(parent, min(stuck, key=lambda k: len(_path(parent, k))))
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
        for step, nxt in _moves(w, st, reduce=reduce, fair=False):  # the states a lucky guess reaches too
            trial = st.copy()
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
