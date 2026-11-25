"""Exact designation: which thing of the world a word names. Never a guess.

The narrator sends ids (`World.in_reach`); a name, an alias or a state's name works too, the articles
aside and singular or plural alike. Several things sharing that name is an ambiguity to ask about,
never a choice made for the player.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.model import (
    State,
)

if TYPE_CHECKING:
    from storyworld.world import World


class Names:
    """Labels, matching, resolution among candidates, and finding in the hero's scope."""

    def __init__(self, world: World) -> None:
        self.w = world

    def labels(self, ident: str) -> list[str]:
        """Every name a thing, a character or a place is known by (its states' names too)."""
        w = self.w
        for table in (w.items, w.chars, w.places):
            if ident in table:
                e = table[ident]
                states = (e.get("states") or {}).values()  # a thing is known by every state's names
                return [e.get("name", ""), ident.replace("_", " "), *e.get("aliases", []),
                        *(x for v in states for x in (v.get("name", ""), *v.get("aliases", [])) if x)]  # fmt: skip
        return [ident]

    def matches(self, said: str, ident: str) -> bool:
        """Exactly this thing: its id, or one of its names (name, aliases, its states' names), the
        articles aside and singular or plural alike (« les algues » is « une algue »). No guessing."""
        if said == ident:
            return True
        n, stems = self.w.lang.norm(said), self.w.lang.stems(said)
        return bool(n) and any(
            self.w.lang.norm(label) == n or (stems and self.w.lang.stems(label) == stems)
            for label in self.labels(ident)
        )

    def resolve(self, said: str, candidates: list[str]) -> tuple[str | None, list[str]]:
        """(id, []) when exactly one candidate is meant; (None, [ids]) when several share that name;
        (None, []) when none: the narrator names things by their id (see `scope_ids`)."""
        if not said:
            return None, []
        hits = [c for c in dict.fromkeys(candidates) if self.matches(said, c)]
        return (hits[0], []) if len(hits) == 1 else (None, hits)

    def find(self, said: str | None, st: State) -> tuple[str | None, list[str]]:
        """Things first (bag, here, characters), the place itself only as a last resort."""
        if not said:
            return None, []
        w = self.w
        things = [*st.inventory, *w.visible_items(st), *w.chars_here(st), *w.held_here(st)]
        found, amb = self.resolve(said, things)
        if found or amb:
            return found, amb
        return self.resolve(said, [st.place])

    def everything(self) -> list[str]:
        """Every id of the adventure: things, characters, places."""
        w = self.w
        return [*w.items, *w.chars, *w.places]
