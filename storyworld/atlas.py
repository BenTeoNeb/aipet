"""The atlas: every state the hero can reach from the start, each with how far a win is (fair steps).

Built once per adventure, then a turn's question « can he still win from here? » is a lookup
instead of a search. It explores every move, in every order (no take-first reduction), lucky guesses
included, and counts a win only along the steps a child can do knowing what they met (the solver's
fair play, `storyworld.solver.solve`).

A state off the atlas (after a destroy: the solver never destroys) is not known: `distance` says
None and the caller searches as before.

For now it runs in the shadow of the searches (`Hints.way_to_win`, `Turn.doom`): each answer is
compared with the search's, and what differs is kept in `disagreements`; nothing it says changes
the game yet."""

from __future__ import annotations

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

    def __init__(self, w: World, max_states: int = ATLAS_STATES) -> None:
        from storyworld.solver import _fair, _key, _moves

        t = time.monotonic()
        self.w = w
        s0 = w.start()
        k0 = _key(s0, w)
        self.seen: set[tuple] = {k0}
        reverse: dict[tuple, list[tuple]] = {}  # state → the states one fair step before it
        wins: list[tuple] = []
        todo = [s0]
        while todo and len(self.seen) < max_states:
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
                if nk not in self.seen:
                    self.seen.add(nk)
                    todo.append(n)
        self.complete = not todo
        self.dist: dict[tuple, int] = dict.fromkeys(wins, 0)
        back = deque(wins)
        while back:
            b = back.popleft()
            for a in reverse.get(b, []):
                if a not in self.dist:
                    self.dist[a] = self.dist[b] + 1
                    back.append(a)
        self.seconds = time.monotonic() - t
        self.disagreements: list[dict] = []

    def distance(self, st: State) -> int | None:
        from storyworld.solver import _key

        if not self.complete:
            return None
        k = _key(st, self.w)
        if k not in self.seen:
            return None
        return self.dist.get(k, -1)

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
