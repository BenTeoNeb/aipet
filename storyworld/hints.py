"""Hints, from the solver's next useful step: stronger each time; and the policy of when to give them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.model import (
    State,
)
from storyworld.rules import (
    args_of,
)

if TYPE_CHECKING:
    from storyworld.world import World

HINT_STATES = 20_000  # a hint is searched during the conversation: keep it quick


def useful_steps(w: World, path: list[tuple]) -> list[tuple]:
    """The path without picking up things it never uses (the solver takes everything it sees)."""
    needed: set[str] = set()
    for kind, ident in path:
        if kind == "act":
            for a in (x for x in w.rules if x["id"] == ident):
                needed |= {x for x in (a.get("object"), a.get("target")) if x}
                needed |= args_of(a.get("when"), "has")
        elif kind == "open":
            needed |= {w.items[ident].get("key", "")}
        elif kind == "go":
            if w.places.get(ident, {}).get("dark"):
                needed |= set(w.lights)  # to see there
            for p in w.places.values():
                for x in p.get("exits", []):
                    if x.get("to") == ident:
                        needed |= args_of(x.get("when"), "has")
                        needed |= {w.items.get(x.get("door", ""), {}).get("key", "")}
    return [s for s in path if s[0] != "take" or s[1] in needed]


def hint_text(w: World, step: tuple, level: int, st: State | None = None) -> str:
    """Level 1 points at where the clue is (the puzzle chart's `clue_in`), else at the bag or around;
    level 2 at the thing (or the author's `hint`); level 3 says what to do. Names as they are now
    (« un bocal de lucioles »)."""
    kind, say, of, cap = step[0], w.say, w.lang.of, w.lang.cap
    if level == 1 and (pointer := _clue_pointer(w, step, st)):
        return pointer
    if kind == "go":
        place = w.name(step[1], st)
        return [say("What if you went to look somewhere else?"),
                say("Maybe there's something near {of_place}…", of_place=of(place)),
                say("Go to {place}.", place=place)][level - 1]  # fmt: skip
    if kind == "take":
        item = w.name(step[1], st)
        return [say("Take a good look around you."), say("Did you notice {thing}?", thing=item),
                say("Take {thing}.", thing=item)][level - 1]  # fmt: skip
    if kind == "answer":
        who, ans = w.name(step[1], st), w.thing(step[1])["answer"]
        clue = ans.get("hint") or say(
            "Listen carefully to the question of {of_who}… the answer may be hidden in the adventure.", of_who=of(who)
        )
        return [say("What if you talked to {who}?", who=who), clue,
                say("Answer: “{answer}”.", answer=ans["accept"][0])][level - 1]  # fmt: skip
    if kind == "press":
        box = w.buttons[step[1]]
        clue = w.items[box]["code"].get("hint") or say(
            "{Name} has a code… Do you remember what you saw?", Name=cap(w.name(box, st))
        )
        return [say("Take a good look around you."), clue,
                say("Press {thing}.", thing=w.name(step[1], st))][level - 1]  # fmt: skip
    if kind == "open":
        item = w.name(step[1], st)
        return [say("Take a good look around you."), say("And {thing}, what's inside?", thing=item),
                say("Open {thing}.", thing=item)][level - 1]  # fmt: skip
    a = _trigger_for(w, step[1], st)
    obj = w.name(a.get("object", ""), st) if a.get("object") else ""
    tgt = w.name(a.get("target", ""), st) if a.get("target") else ""
    verb = w.lang.verb(a.get("verb", ""))
    return [
        say("Think of what's in your bag, and who or what it could be useful for."),
        a.get("hint")
        or (say("And {thing}, what could it be useful for here?", thing=obj) if obj
            else say("Try something with what's here.")),
        say("Try: {verb} {thing}, with {target}.", verb=verb, thing=obj, target=tgt) if tgt
        else say("Try: {verb} {thing}.", verb=verb, thing=obj),
    ][level - 1]  # fmt: skip


def _clue_pointer(w: World, step: tuple, st: State | None) -> str:
    """« Et si tu retournais voir le dessin sur la plage ? »: where the author put the clue of the
    puzzle this step solves (`puzzles[].clue_in`), unless it is right here."""
    kind, ident = step
    solves = {"act": ident, "answer": ident, "press": w.buttons.get(ident, "")}.get(kind)
    clue = next((z.get("clue_in") for z in w.g.get("puzzles", []) if solves and z.get("action") == solves), "")
    if not clue or (st and (clue == st.place or clue in w.scope(st))):
        return ""
    name, say, of = w.name(clue, st), w.say, w.lang.of
    if clue in w.places:
        return say("What if you went back near {of_place}? There's a clue there.", of_place=of(name))
    if clue in w.chars:
        return say("What if you talked to {who} again? They may have given you a clue.", who=name)
    where = st.items_at.get(clue, "") if st else w.items.get(clue, {}).get("at", "")
    if where in w.places and (not st or where != st.place):
        return say("What if you went back to see {thing}, near {of_place}? There's a clue.",
                   thing=name, of_place=of(w.name(where)))  # fmt: skip
    return say("What if you looked at {thing} again? There's a clue.", thing=name)


def _trigger_for(w: World, action: str, st: State | None) -> dict:
    """The trigger to hint: the one whose things are at hand, the bag's first (« les allumettes »
    rather than « la lampe » when the hero holds the matches)."""
    rules = [x for x in w.rules if x["id"] == action]
    if not rules or st is None:
        return rules[0] if rules else {}
    scope = set(w.scope(st))

    def score(r: dict) -> tuple:
        things = [x for x in (r.get("object"), r.get("target")) if x]
        return (all(x in scope for x in things), any(x in st.inventory for x in things))

    return max(rules, key=score)  # max keeps the first of equals: the author's order


class Hints:
    """The hint policy: after a few tries that lead nowhere, a reminder of what can be done, then
    hints for the next useful step towards a win, stronger each time."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.cache: dict[tuple, list[tuple]] = {}  # a state's way to win, found once (voice latency)

    def way_to_win(self, st: State) -> list[tuple]:
        from storyworld.solver import _key, solve

        k = _key(st, self.w)
        if k not in self.cache:
            if len(self.cache) > 200:
                self.cache.clear()
            self.cache[k] = solve(self.w, st, first_path=True, max_states=HINT_STATES).get("path") or []
        return self.cache[k]

    def towards_tries(self, st: State) -> list[tuple]:
        """The useful steps towards the puzzle the child is trying (what their last tries were about):
        the shortest way to a step on those things, if it is on a way to win (InvisiClues answer the
        question the player has, not the next one on the designer's path)."""
        from storyworld.solver import _key, _moves

        w = self.w
        tried = set(st.tried)
        if not tried:
            return []

        def about(step: tuple) -> bool:
            kind, ident = step
            if kind == "act":
                return any({r.get("object"), r.get("target")} & tried for r in w.rules if r["id"] == ident)
            return kind in ("open", "answer") and ident in tried or kind == "press" and w.buttons.get(ident) in tried

        start = State.from_dict(st.to_dict())
        seen = {_key(start, w)}
        frontier: list[tuple[State, list[tuple]]] = [(start, [])]
        while frontier and len(seen) < HINT_STATES // 4:
            nxt: list[tuple[State, list[tuple]]] = []
            for s, path in frontier:
                for step, n in _moves(w, s, reduce=True):
                    if about(step) and self.way_to_win(n):  # the try leads somewhere that can still win
                        return useful_steps(w, [*path, step]) or [step]
                    if (k := _key(n, w)) not in seen and not n.ending:
                        seen.add(k)
                        nxt.append((n, [*path, step]))
            frontier = nxt
        return []

    def nudge(self, st: State) -> dict:
        """After a few tries that lead nowhere: first a reminder of what can be done, then hints."""
        if st.stalled < self.w.policy["hint_after"]:
            return {}
        if not st.reminded:
            st.reminded, st.stalled = True, 0
            return {"reminder": self.w.view.options(st)}
        hint = self.hint(st, force=True)
        return {"hint_given": hint} if hint else {}

    def hint(self, st: State, force: bool = False) -> str:
        """A hint for the next useful step towards a win, stronger each time ("" = none)."""

        w = self.w
        if not force and st.stalled < w.policy["hint_after"]:
            return ""
        st.stalled = 0  # even without a path: not searched again at every turn
        path = self.towards_tries(st) or useful_steps(w, self.way_to_win(st))
        if not path:
            return ""
        st.hint_level = min(3, st.hint_level + 1)
        step = path[0]
        if step[0] == "go":  # a walk: name where it leads, not the first room on the way
            walk = 0
            while walk + 1 < len(path) and path[walk + 1][0] == "go":
                walk += 1
            step = path[walk]
        return hint_text(w, step, st.hint_level, st)
