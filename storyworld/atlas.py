"""The atlas: every state the hero can reach from the start, each with how far a win is (fair steps).

Built once per adventure, then a turn's question « can he still win from here? » is a lookup
instead of a search. It explores every move, in every order (no take-first reduction), lucky guesses
included, and counts a win only along the steps a player can do knowing what they met (the solver's
fair play, `storyworld.solver.solve`).

A state off the atlas (after a destroy: the solver never destroys) is not known: `distance` says
None and the caller searches as before.

For now it runs in the shadow of the searches (`Hints.way_to_win`, `Turn.doom`): each answer is
compared with the search's, and what differs is kept in `disagreements`; nothing it says changes
the game yet."""

from __future__ import annotations

import marshal
import time
from collections import deque
from typing import TYPE_CHECKING

from storyworld.model import State

if TYPE_CHECKING:
    from storyworld.world import World

ATLAS_STATES = 100_000  # past this, no atlas: the searches answer alone
KEPT = 50  # disagreements kept, at most


class Atlas:
    """`distance(st)`: fair steps to the nearest win (0: won), -1 when no win is reachable, None when
    the state is off the atlas (or the atlas is not complete)."""

    def __init__(self, w: World, max_states: int = ATLAS_STATES, table: dict | None = None) -> None:
        """Explored from the start, or `table` (state → distance) as kept (`to_bytes`)."""
        t = time.monotonic()
        self.w = w
        self.complete = True
        if table is None:
            table, self.complete = _explore(w, max_states)
        self.table: dict[tuple, int] = table
        self.seconds = time.monotonic() - t
        self.disagreements: list[dict] = []

    @property
    def winnable(self) -> int:
        return sum(d >= 0 for d in self.table.values())

    def to_bytes(self) -> bytes:
        """To keep it (`marshal`: plain values only, nothing runs when it is read back). A cut
        exploration is not kept: it answers nothing."""
        return marshal.dumps(self.table)

    @classmethod
    def from_bytes(cls, w: World, data: bytes) -> Atlas:
        """The atlas as kept, for the same adventure and the same engine (the caller's key)."""
        table = marshal.loads(data)
        if not isinstance(table, dict):
            raise ValueError("not an atlas")
        return cls(w, table=table)

    def distance(self, st: State) -> int | None:
        from storyworld.solver import _key

        if not self.complete:
            return None
        return self.table.get(_key(st, self.w))

    def compare(self, st: State, where: str, path: list | None, complete: bool) -> None:
        """The shadow: what a search found from `st` (a way to win, or none, `complete` if it saw
        every state) against the atlas. Its distance is a shortest one over every order: never longer
        than the search's way (which takes first)."""
        d = self.distance(st)
        if d is None:
            return
        if path:
            ok = 0 <= d <= len(path)
        else:
            ok = d in (-1, 0) or not complete  # 0: already won; a cut search proves nothing
        if not ok and len(self.disagreements) < KEPT:
            self.disagreements.append({"where": where, "place": st.place, "atlas": d,
                                       "search": len(path) if path else (0 if complete else None)})  # fmt: skip


def _explore(w: World, max_states: int) -> tuple[dict[tuple, int], bool]:
    """Every state reachable from the start (every move, in every order, guesses included), then
    the fair distance to a win, backwards from the wins (-1: none). Complete unless cut."""
    from storyworld.solver import _fair, _key, _moves

    s0 = w.start()
    seen: set[tuple] = {_key(s0, w)}
    reverse: dict[tuple, list[tuple]] = {}  # state → the states one fair step before it
    wins: list[tuple] = []
    todo = [s0]
    while todo and len(seen) < max_states:
        st = todo.pop()
        k = _key(st, w)
        if st.ending:
            if w.ending(st, st.ending).get("kind") == "win":
                wins.append(k)
            continue
        for step, n in _moves(w, st, reduce=False, fair=False):
            nk = _key(n, w)
            if _fair(w, st, step):
                reverse.setdefault(nk, []).append(k)
            if nk not in seen:
                seen.add(nk)
                todo.append(n)
    dist: dict[tuple, int] = dict.fromkeys(wins, 0)
    back = deque(wins)
    while back:
        b = back.popleft()
        for a in reverse.get(b, []):
            if a not in dist:
                dist[a] = dist[b] + 1
                back.append(a)
    return {k: dist.get(k, -1) for k in seen}, not todo
