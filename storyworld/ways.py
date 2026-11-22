"""Going and « where is… »: the ways out named by the child (a destination, a label, a direction, or
someone over there), going through them (a closed door opens on the way when it can), and where a
thing or a place is, from what the hero knows."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from storyworld.locale.fr import (
    _norm,
    _way,
)
from storyworld.model import (
    State,
)

if TYPE_CHECKING:
    from storyworld.world import World


class Ways:
    """The built-in going, and the answer to « where is… »."""

    def __init__(self, world: World) -> None:
        self.w = world

    def go(self, st: State, to: str) -> dict:
        """Go a way named by its destination, label or direction, or towards someone or something."""
        w = self.w
        exits = w.exits(st)
        if not exits:
            return {"outcome": "no_exit", "text": w.say("There's no way out over there.")}
        words = _way(to)
        if len(exits) == 1 and (not words or _norm(to) in ("sortir", "dehors", "partir")):
            return self.through(st, exits[0])

        def names(x: dict) -> list[str]:  # an exit is named by its destination, its label or its direction
            return [x.get("to", ""), x.get("label", ""), x.get("dir", ""), *w.names.labels(x.get("to", ""))]

        scored = [(int(to == x.get("to") or any(lw and lw == words for lw in map(_way, names(x)))), x) for x in exits]
        top = max(sc for sc, _ in scored)
        if not top:  # « chez la taupe », « vers le levier »: where someone or something is
            return self._towards(st, to, words, exits)
        best_exits = [x for sc, x in scored if sc == top]
        if len({x.get("to") for x in best_exits}) > 1:
            return {
                "outcome": "ask",
                "question": w.say("which way?"),
                "choices": [x.get("label", "") for x in best_exits],
            }
        return self.through(st, best_exits[0])

    def _towards(self, st: State, to: str, words: set[str], exits: list[dict]) -> dict:
        """No way is named: the way to where someone or something is, or it is right here."""
        w = self.w
        resolve = w.names.resolve
        near = {x.get("to"): x for x in exits}
        who = [c for c, p in st.chars_at.items() if p in near] + [i for i, p in st.items_at.items() if p in near]
        core = " ".join(sorted(words))  # « chez Mamie » → Mamie
        found = resolve(to, who)[0] or resolve(core, who)[0]
        if found:
            return self.through(st, near[st.chars_at.get(found) or st.items_at[found]])
        around = [*w.visible_items(st), *w.chars_here(st)]
        thing = resolve(to, around)[0] or resolve(core, around)[0]
        if thing:  # « aller voir l'âne »: it is right here (the narrator sends climb to get on or in)
            return {"outcome": "done", "text": w.say("You're right next to {thing}.", thing=w.name(thing, st)),
                    "note": w.say("It's not a way: it's here. To get on it or in it, the verb is climb.")}  # fmt: skip
        return {"outcome": "no_exit", "text": w.say("You can't go that way."), **w.view.describe(st)}

    def through(self, st: State, x: dict) -> dict:
        """Go through an exit: its conditions, its door (opened on the way if it can be), the arrival."""
        w = self.w
        if not w.holds(st, x.get("when")):
            return {"outcome": "not_yet", "text": x.get("blocked") or w.say("The way is blocked.")}
        opened: dict = {}
        door = x.get("door")
        if door and not w.prop(st, door, "open"):  # a closed door: opened on the way, if it can be
            opened = w.builtins.open(st, door)
            if opened["outcome"] != "done":
                return opened
        if w.enter(st, x["to"]):
            st.score += 2
        return {
            "outcome": "done",
            **({"text": opened["text"]} if opened.get("text") else {}),
            "arrival": w.view.describe(st),
        }

    def first_step(self, st: State, to: str) -> str:
        """The first way out towards a place (as the crow walks, conditions aside)."""
        w = self.w
        seen, todo = {st.place}, deque([(st.place, "")])
        while todo:
            here, first = todo.popleft()
            for x in w.places.get(here, {}).get("exits", []):
                nxt = x.get("to")
                if nxt in seen or nxt not in w.places:
                    continue
                step = first or f"{x.get('label') or w.name(nxt)} ({x.get('dir', '')})".replace(" ()", "")
                if nxt == to:
                    return step
                seen.add(nxt)
                todo.append((nxt, step))
        return ""

    def where(self, st: State, said: str) -> dict:
        """« Où est le crabe ? »: what the hero knows (seen, in the bag), never a guess."""
        w = self.w
        note = w.say("Say only this: never guess where things are.")
        found, _ = w.names.resolve(said, w.names.everything())
        if not found:
            return {"outcome": "done", "where": w.say("You know of nothing like that in this adventure."), "note": note}
        return {"outcome": "done", "where": self._where_place(st, found) if found in w.places
                else self._where_thing(st, found), "note": note}  # fmt: skip

    def _where_place(self, st: State, found: str) -> str:
        w = self.w
        name = w.name(found, st)
        if found == st.place:
            return w.say("You are there: {name}.", name=name)
        if found not in st.visited:
            return w.say("You haven't been there yet: explore to find {name}!", name=name)
        way = self.first_step(st, found)
        return (
            w.say("To go to {name}: {way}.", name=name, way=way)
            if way
            else w.say("{Name}, you've already been there.", Name=w.lang.cap(name))
        )

    def _where_thing(self, st: State, found: str) -> str:
        w = self.w
        name = w.name(found, st)
        Name = w.lang.cap(name)
        at = st.items_at.get(found) or st.chars_at.get(found) or ""
        if found in st.inventory:
            return w.say("{Name} is in your bag.", Name=Name)
        if at == "gone":
            return w.say("{Name} is gone for good.", Name=Name)
        if found in w.scope(st) or found in w.held_here(st):
            return w.say("{Name} is right here.", Name=Name)
        place = at
        while place and place not in w.places:  # in a container, or held by someone
            place = st.items_at.get(place) or st.chars_at.get(place) or ""
        if place in st.visited and place != st.place:
            return w.say("You saw {name} near {of_place}.", name=name, of_place=w.lang.of(w.name(place)))
        return w.say("You haven't seen {name} yet. Explore, or ask someone!", name=name)
