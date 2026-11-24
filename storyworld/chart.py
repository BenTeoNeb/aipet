"""The puzzle dependency chart as it is (Ron Gilbert's), derived from a way to win: each puzzle on
the way (an action, an opening, a riddle, a code) and the earlier puzzles it needs, through the
facts they make true (a thing revealed and taken, a flag, a door opened, a clue met). Its depth is
the longest chain of puzzles; its width, the most puzzles open at once (one level of the chart).
A chart one puzzle wide is a single corridor, one puzzle after another (the checker warns about long
ones: the derived chart and that warning are ours, not Gilbert's)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.model import State
from storyworld.rules import parse_all

if TYPE_CHECKING:
    from storyworld.world import World

PUZZLE_STEPS = ("act", "open", "answer", "press")


def facts(w: World, st: State) -> set[str]:
    """What is true that a step may need: the bag, flags, open things, states, things in the world,
    places reached, clues met."""
    out = {f"has:{i}" for i in st.inventory} | {f"flag:{f}" for f in st.flags}
    out |= {f"visited:{p}" for p in st.visited} | {f"known:{c}" for c in st.known}
    for i, at in st.items_at.items():  # out in the world: not nowhere, held, nor in a closed container
        if at in w.places or at == "inv" or (at in w.items and w.prop(st, at, "open")):
            out.add(f"present:{i}")
    for i, d in st.props.items():
        if d.get("open"):
            out.add(f"open:{i}")
        if d.get("state"):
            out.add(f"is:{i}:{d['state']}")
        if d.get("answered"):
            out.add(f"answered:{i}")
    return out


def needs(w: World, step: tuple) -> set[str]:
    """The facts a step stands on."""
    kind, ident = step
    if kind == "act":
        out = set()
        for a in (r for r in w.rules if r["id"] == ident):
            for r in parse_all(a.get("when")):
                if r.negated:
                    continue
                if r.kind in ("has", "flag", "open"):
                    out.add(f"{r.kind}:{r.arg}")
                elif r.kind == "is":
                    out.add(str(r))
                elif r.kind in ("at", "visited"):
                    out.add(f"visited:{r.arg}")
            for x in (a.get("object"), a.get("target")):
                if x in w.items:
                    out.add(f"present:{x}")
        return out
    if kind == "open":
        key = w.items.get(ident, {}).get("key")
        return {f"has:{key}"} if key else set()
    if kind == "take":
        return {f"present:{ident}"}
    if kind in ("answer", "press"):
        box = w.buttons.get(ident, ident)
        clue = w.clue_of.get(box)
        return {f"known:{clue}"} if clue else set()
    return set()  # go: the way's conditions are the facts of the steps before


def derive(w: World, path: list[tuple]) -> dict:
    """The chart of a way to win: {puzzles: [step ids], needs: {puzzle: [earlier puzzles]}, depth,
    width}. A code (several presses) is one puzzle."""
    from storyworld.solver import _moves  # the solver's own steps, replayed

    st = w.start()
    made: dict[str, int] = {f: -1 for f in facts(w, st)}  # fact → the step that made it true (-1: the start)
    steps: list[tuple] = []
    deps: list[set[int]] = []
    for step in path:
        nxt = next((n for s, n in _moves(w, st, reduce=False, fair=False) if s == step), None)
        if nxt is None:
            break
        here = len(steps)
        steps.append(step)
        need = needs(w, step) | ({f"visited:{st.place}"} if step[0] in PUZZLE_STEPS else set())  # where it happens
        deps.append({made[f] for f in need if made.get(f, -1) >= 0})
        if step[0] == "go":  # a way open now because of earlier steps: it stands on them
            deps[-1] |= {made[f] for f in facts(w, st) if f.startswith(("flag:", "open:", "has:", "is:"))
                         and made.get(f, -1) >= 0 and _guards_way(w, st.place, step[1], f)}  # fmt: skip
        for f in facts(w, nxt) - facts(w, st):
            made[f] = here
        st = nxt

    def puzzle_id(i: int) -> str:
        kind, ident = steps[i]
        return w.buttons.get(ident, ident) if kind == "press" else ident

    def is_puzzle(i: int) -> bool:
        return steps[i][0] in PUZZLE_STEPS

    memo: dict[int, set[str]] = {}

    def puzzles_under(i: int) -> set[str]:
        """The puzzles a step stands on, through the takes and moves in between."""
        if i not in memo:
            memo[i] = set()
            for d in deps[i]:
                memo[i] |= {puzzle_id(d)} if is_puzzle(d) else puzzles_under(d)
        return memo[i]

    order: list[str] = []
    graph: dict[str, set[str]] = {}
    for i in range(len(steps)):
        if is_puzzle(i):
            p = puzzle_id(i)
            if p not in graph:
                order.append(p)
                graph[p] = set()
            graph[p] |= puzzles_under(i) - {p}
    level: dict[str, int] = {}
    for p in order:  # in path order: what a puzzle needs comes before it
        level[p] = 1 + max((level[q] for q in graph[p] if q in level), default=0)
    counts: dict[int, int] = {}
    for v in level.values():
        counts[v] = counts.get(v, 0) + 1
    return {"puzzles": order, "needs": {p: sorted(graph[p]) for p in order},
            "depth": max(level.values(), default=0), "width": max(counts.values(), default=0)}  # fmt: skip


def _guards_way(w: World, here: str, to: str, fact: str) -> bool:
    """Does this fact open the way from `here` to `to` (its conditions, its door, the door's key)?"""
    for x in w.places.get(here, {}).get("exits", []):
        if x.get("to") != to:
            continue
        rules = {str(r) for r in parse_all(x.get("when")) if not r.negated}
        if door := x.get("door"):
            rules |= {f"open:{door}"} | ({f"has:{w.items[door]['key']}"} if w.items.get(door, {}).get("key") else set())
        if fact in rules:
            return True
    return False
