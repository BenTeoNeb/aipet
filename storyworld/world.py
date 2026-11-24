"""The world: what is true (the model and its rules) and what is around the hero.

`World` is the library's facade. It holds the adventure, normalized (places, things, characters,
the author's actions and endings, the doors, code locks and riddles), the state's properties and
conditions, and what is around the hero. The rest is done by focused components it owns, each given
the world (`Names(world)`):

- `names` (`storyworld.names.Names`): which thing a word names, exactly;
- `scripts` (`storyworld.actions.Actions`): the author's actions, and which one an intent does;
- `builtins` (`storyworld.builtins.Builtins`): opening, taking, code locks, riddles, the bag…;
- `ways` (`storyworld.ways.Ways`): going, and « where is… »;
- `view` (`storyworld.view.View`): what the narrator is told;
- `turn` (`storyworld.turn.Turn`): `act()`, the turn's pipeline;
- `hints` (`storyworld.hints.Hints`): reminders and hints.

The methods below the model only delegate to them, so the application keeps one object.
"""

from __future__ import annotations

import copy

from storyworld.actions import Actions
from storyworld.atlas import Atlas
from storyworld.builtins import Builtins
from storyworld.hints import Hints
from storyworld.locale import Language
from storyworld.locale.fr import FRENCH
from storyworld.model import SITUATED, State, _all_conditions, _normalized, exit_rules, migrate
from storyworld.names import Names
from storyworld.rules import args_of, of_kind, parse_all
from storyworld.turn import Turn
from storyworld.verbs import STUCK
from storyworld.view import View
from storyworld.ways import Ways

HINT_AFTER = 3  # tries that lead nowhere before a reminder, then hints (when no policy says)


class World:
    """An adventure: what is true in a state, and what is around the hero (see the module)."""

    def __init__(self, graph: dict, lang: Language | None = None, policy: dict | None = None):
        self.names = Names(self)
        self.scripts = Actions(self)  # `actions` is the author's list
        self.builtins = Builtins(self)
        self.ways = Ways(self)
        self.view = View(self)
        self.turn = Turn(self)
        self.hints = Hints(self)
        self.atlas: Atlas | None = None  # every reachable state and how far a win is (`build_atlas`)
        self.lang = lang or FRENCH  # every message for the player or the narrator goes through it
        self.g = graph = migrate(graph)
        # The engine reads normalized copies (kinds' properties, states as a map, doors on both sides);
        # `self.g` stays as written (the explorer page shows it).
        self.places = {p["id"]: copy.deepcopy(p) for p in graph.get("places", []) if "id" in p}
        self.items = {i["id"]: _normalized(i, True) for i in graph.get("items", []) if "id" in i}
        self.chars = {c["id"]: _normalized(c, False) for c in graph.get("characters", []) if "id" in c}
        self.mirrored = self._mirror_doors()
        # Code locks (a mini puzzle: press the buttons in the right order): button → the thing it opens.
        self.buttons: dict[str, str] = {b: i for i, it in self.items.items() if it.get("code")
                                        for b in it["code"].get("press", [])}  # fmt: skip
        # Riddles and passwords: who asks (a character, or a door or chest that speaks).
        self.riddles = [i for i in [*self.chars, *self.items] if self.thing(i).get("answer")]
        # An action that spends a use needs one left (`charged:`), implicitly: copies, the graph stays as written.
        self.actions = [self._needs_a_use(a) for a in graph.get("actions", [])]
        # One rule per trigger: what the matching ranks. A rule carries its action's id, conditions
        # and effects, so whichever trigger worked, the action is done once.
        self.rules = [{**{k: v for k, v in a.items() if k != "triggers"}, **t}
                      for a in self.actions for t in (a.get("triggers") or [{}])]  # fmt: skip
        self.endings = {e["id"]: e for e in graph.get("endings", [])}
        self.difficulty = graph.get("difficulty", "facile")
        # An application's policy (`profiles.py` has the pet's): only `hint_after` is read while playing.
        self.policy = {"hint_after": HINT_AFTER, **(policy or {})}
        self.uses_visited = any(r.kind == "visited" for t in _all_conditions(graph) for r in parse_all([t]))
        self.doors: dict[str, list[str]] = {}  # place → the doors of its exits (seen from both sides)
        for p in self.places.values():
            for x in p.get("exits", []):
                if x.get("door") in self.items:
                    self.doors.setdefault(p["id"], []).append(x["door"])
        rules = [r for a in self.actions for r in a.get("when", [])]
        rules += [r for i in self.items.values() if isinstance(i.get("light"), list) for r in i["light"]]
        rules += [r for p in self.places.values() for x in p.get("exits", []) for r in exit_rules(x)]
        # Reactions after a built-in step (`after`), as written: {(take|open|enter, id): reaction}.
        self.reactions = {(k, t["id"]): r for t in [*self.items.values(), *self.places.values()]
                          if isinstance(t.get("after"), dict) for k, r in t["after"].items() if isinstance(r, dict)}  # fmt: skip
        effects = (
            [a.get("effects") for a in self.actions]
            + [self.thing(r)["answer"].get("effects") for r in self.riddles]
            + [r.get("effects") for r in self.reactions.values()]
        )
        self.settable = {f for e in effects for f in args_of(e, "flag")}
        self.idempotent_ids = {a["id"] for a in self.actions if self.idempotent(a)}
        self.eager_ok = self._eager_ok(rules)
        self.lights = [i for i, it in self.items.items() if it.get("light")]
        # What a child must have met to know a code or a riddle's answer (our extension of Gilbert's
        # charts): the clue of its puzzle. A win counts only once it is known (`solver._fair`).
        chart: dict[str, str] = {str(z.get("action")): z["clue_in"] for z in graph.get("puzzles", [])
                                 if isinstance(z, dict) and isinstance(z.get("clue_in"), str)}  # fmt: skip
        gated = [i for i, it in self.items.items() if it.get("code")] + self.riddles
        self.clue_of: dict[str, str] = {x: chart[x] for x in gated if chart.get(x)}
        self.clues = set(self.clue_of.values())

    @staticmethod
    def _needs_a_use(a: dict) -> dict:
        if not isinstance(a, dict):
            return a
        when: list = a["when"] if isinstance(a.get("when"), list) else []
        extra = [f"charged:{x}" for x in sorted(args_of(a.get("effects"), "spend")) if f"charged:{x}" not in when]
        return {**a, "when": [*when, *extra]} if extra else a

    def _mirror_doors(self) -> list[str]:
        """A door is written once, on one side: the way back goes through the same door. A door
        that is nowhere yet stands at the first place it closes. Returns the conflicts (two doors on
        one way) for `check`."""
        conflicts = []
        for p in self.places.values():
            for x in p.get("exits", []):
                door = x.get("door")
                if not door:
                    continue
                for b in self.places.get(x.get("to"), {}).get("exits", []):
                    if b.get("to") != p["id"]:
                        continue
                    if not b.get("door"):
                        b["door"] = door
                    elif b["door"] != door:
                        conflicts.append(f"{p['id']}<->{x.get('to')}: doors {door} and {b['door']}")
                if door in self.items and not self.items[door].get("at"):
                    self.items[door]["at"] = p["id"]
        return conflicts

    def _eager_ok(self, rules: list[str]) -> set[str]:
        """Objects the solver may take as soon as it sees them: never forbidden (« !has: »), never
        hidden, in a world where the hero can always walk back (every exit has a way back that needs
        no object, and nothing teleports or closes again)."""
        effects = [e for a in self.actions for e in a.get("effects", [])]
        effects += [e for r in self.reactions.values() for e in r.get("effects") or []]
        if of_kind(effects, "goto"):
            return set()
        every = [r for p in self.places.values() for x in p.get("exits", []) for r in exit_rules(x)]
        if not all(r.kind in ("flag", "open") and not r.negated for r in parse_all(every)):  # set once, never undone
            return set()
        sure = self._flags_sure()
        for p in self.places.values():
            for x in p.get("exits", []):
                back = [b for b in self.places.get(x.get("to"), {}).get("exits", []) if b.get("to") == p["id"]]
                known = set(exit_rules(x)) | sure[p["id"]]  # flags never unset: still true on the way back
                if not any(set(exit_rules(b)) <= known for b in back):
                    return set()
        reacting = {i for k, i in self.reactions if k == "take"}  # taking it changes the world: not first
        # A reaction that asks what the hero holds (« has:filet » when the chest opens) depends on when
        # it was taken: the other orders are played.
        when = [r for x in self.reactions.values() for r in x.get("when") or []]
        held = args_of(when, "has") | args_of(when, "has", negated=True)
        return set(self.items) - args_of(rules, "has", negated=True) - held - self._maybe_in_the_dark() - reacting

    def _maybe_in_the_dark(self) -> set[str]:
        """Items that may lie in a dark place: seen there by a light, they may be unseen later (the
        light burnt, lost, put out), so taking them first is not always as good."""
        dark = {p for p, place in self.places.items() if place.get("dark")}
        if not dark:
            return set()

        def where(i: str) -> str:
            at, seen = self.items[i].get("at") or "", {i}
            while at in self.items and at not in seen:  # in a container
                seen.add(at)
                at = self.items[at].get("at") or ""
            return at

        dark_doors = {d for p in dark for d in self.doors.get(p, [])}
        followers = args_of([e for a in self.actions for e in a.get("effects") or []], "follow")

        def there(x: str) -> bool:
            """Can it be in reach in a dark place (lit by then): the place, a thing carried in, a
            companion, something lying there, one of its doors."""
            if x in dark or x in followers or x in dark_doors:  # a character there is unseen, but a companion
                return True
            return x in self.items and (self.items[x].get("takeable") or where(x) in dark)

        def may_be_dark(a: dict) -> bool:
            if a.get("place"):
                return a["place"] in dark
            return any(
                all(there(x) for x in (t.get("object"), t.get("target")) if x) for t in a.get("triggers") or [{}]
            )

        revealed = {i for a in self.actions if may_be_dark(a) for i in args_of(a.get("effects"), "reveal")}
        revealed |= {
            e for r in self.riddles if there(r) for e in args_of(self.thing(r)["answer"].get("effects"), "reveal")
        }
        return {i for i in self.items if where(i) in dark or i in revealed}

    def _flags_sure(self) -> dict[str, set[str]]:
        """For each place, the exit flags that are always set when the hero is there (every way in
        needs them); flags are never unset in a world the solver reduces."""
        every = {r for p in self.places.values() for x in p.get("exits", []) for r in exit_rules(x)}
        start = self.g.get("start", {}).get("place")
        sure = {p: (set() if p == start else set(every)) for p in self.places}
        changed = True
        while changed:
            changed = False
            for p in self.places:
                if p == start:
                    continue
                ways = [set(exit_rules(x)) | sure[q] for q, place in self.places.items()
                        for x in place.get("exits", []) if x.get("to") == p]  # fmt: skip
                new = set.intersection(*ways) if ways else set()
                if new != sure[p]:
                    sure[p], changed = new, True
        return sure

    # ------------------------------------------------------------ names, properties
    def say(self, msgid: str, **kw) -> str:
        """A message in the world's language: `msgid` is the English template (gettext style)."""
        return self.lang.say(msgid, **kw)

    def thing(self, ident: str) -> dict:
        return self.items.get(ident) or self.chars.get(ident) or {}

    def prop(self, st: State, ident: str, key: str):
        """A thing's property now: open, locked, state (changed in the state, else as written)."""
        changed = st.props.get(ident, {})
        if key in changed:
            return changed[key]
        return self.thing(ident).get(key, "" if key == "state" else False)

    def set_prop(self, st: State, ident: str, key: str, value):
        """Change a property; set back to what is written, it is forgotten (one state, one key)."""
        written = self.thing(ident).get(key, "" if key == "state" else False)
        if key == "uses":
            written = self.max_uses(ident)
        if value == written and type(value) is type(written) or key == "pressed" and not value:
            st.props.get(ident, {}).pop(key, None)
            if not st.props.get(ident, True):
                del st.props[ident]
            return
        st.props.setdefault(ident, {})[key] = value

    def look(self, ident: str, st: State | None = None) -> dict:
        """A thing as it is now: its current state may rename or redescribe it."""
        t = self.thing(ident) or self.places.get(ident, {})
        state = self.prop(st, ident, "state") if st and t.get("states") else t.get("state", "")
        t = {**t, **(t.get("states") or {}).get(state, {})} if state else t
        if st is not None and any(isinstance(t.get(k), list) for k in SITUATED):
            t = {**t, **{k: self.situated(t[k], st) for k in SITUATED if isinstance(t.get(k), list)}}
        return t

    def situated(self, lines: list[dict], st: State) -> str:
        """A line that depends on the situation (storylets): the first whose conditions hold
        (« says »: [{when: [has:cle], text: « Vite, au phare ! »}, {text: « Il faut rallumer… »}])."""
        return next((x.get("text", "") for x in lines if self.holds(st, x.get("when"))), "")

    def name(self, ident: str, st: State | None = None) -> str:
        if ident in self.items or ident in self.chars or ident in self.places:
            return self.look(ident, st).get("name", ident)
        return ident

    def text(self, base: str, details: list[dict] | None, st: State) -> str:
        """A text composed of its base and the details that hold (or their `else`)."""
        parts = [base] if base else []
        for d in details or []:
            t = d.get("text", "") if self.holds(st, d.get("when")) else d.get("else", "")
            if t:
                parts.append(t)
        return " ".join(parts)

    def start(self) -> State:
        s = self.g.get("start", {})
        st = State(place=s.get("place", next(iter(self.places), "")))
        st.items_at = {i: it.get("at", "") or "" for i, it in self.items.items()}
        for i in s.get("inventory", []):
            if i in self.items:
                st.inventory.append(i)
                st.items_at[i] = "inv"
        st.chars_at = {c: ch.get("at", "") for c, ch in self.chars.items()}
        st.visited = [st.place]
        st.seen = [] if self.dark(st) else [st.place]
        self.learn(st)
        return st

    def learn(self, st: State) -> None:
        """Clues met: a place seen lit, a thing or someone in reach (the drawing in the sand, the
        character who asks). Once met, known for good."""
        if not self.clues:
            return
        around = set(self.scope(st)) - ({st.place} if self.dark(st) else set())
        for c in sorted(self.clues & around - set(st.known)):
            st.known.append(c)

    def react(self, before: State, st: State) -> tuple[list[str], list[str]]:
        """What the world does after a built-in step (Inform's « after » rules): taking a thing, opening
        it, entering a place may have a reaction (`after: {take|open|enter: {when, effects, text}}`),
        once. Played by the turn and by the solver alike, on what changed, in the order it happens
        (a chest opens, then the hero takes the egg), again while a reaction makes something else
        happen. Returns (texts, changes)."""
        texts: list[str] = []
        changes: list[str] = []
        fired = True
        while fired:
            fired = False
            happened = [
                ("open", i) for i in self.items if self.prop(st, i, "open") and not self.prop(before, i, "open")
            ]
            happened += [("enter", st.place)] if st.place != before.place else []
            happened += [("take", i) for i in st.inventory if i not in before.inventory]
            for kind, ident in happened:
                r = self.reactions.get((kind, ident))
                mark = f"after:{kind}:{ident}"
                if not isinstance(r, dict) or mark in st.done_actions or not self.holds(st, r.get("when")):
                    continue
                st.done_actions.append(mark)
                changes += self.apply(st, r.get("effects") or [])
                if r.get("text"):
                    texts.append(r["text"])
                fired = True
        return texts, changes

    def restore(self, saved: dict) -> State:
        """A saved state, as it is played now: a save from before the clues were kept (no `known`)
        gets the clues it had met (in the places it saw, the things it held, the people it met)."""
        st = State.from_dict(saved)
        if "known" not in saved and self.clues:
            met = set(st.seen) | set(st.handled) | set(st.inventory)
            met |= {i for i, at in st.items_at.items() if at in st.seen}
            met |= {c for c, at in st.chars_at.items() if at in st.seen}
            st.known = sorted(self.clues & met)
            self.learn(st)
        return st

    def knows(self, st: State, puzzle: str) -> bool:
        """Has the hero met what tells the code or the answer (no clue written: yes)?"""
        clue = self.clue_of.get(puzzle)
        return not clue or clue in st.known

    # ------------------------------------------------------------ conditions / effects
    def holds(self, st: State, rules: list[str] | None) -> bool:
        for r in parse_all(rules):
            neg, kind, args, a = r.negated, r.kind, r.args, r.arg
            if kind == "has":
                ok = a in st.inventory
            elif kind == "flag":
                ok = a in st.flags
            elif kind == "at":
                ok = st.place == a
            elif kind == "visited":
                ok = a in st.visited
            elif kind == "here":
                ok = st.chars_at.get(a) == st.place
            elif kind in ("open", "locked"):
                ok = bool(self.prop(st, a, kind))
            elif kind == "is":
                ok = len(args) > 1 and self.prop(st, a, "state") == args[1]
            elif kind == "charged":
                ok = self.uses_left(st, a) > 0
            else:
                ok = False  # an unknown condition never holds (the checker reports it)
            if ok == neg:
                return False
        return True

    def apply(self, st: State, effects: list[str]) -> list[str]:
        """Apply effects; returns what changed, in words (the world's language) for the narrator."""
        changes = []
        for r in parse_all(effects):
            kind, args, a = r.kind, r.args, r.arg
            if kind == "gain" and a in self.items and a not in st.inventory:
                st.inventory.append(a)
                st.items_at[a] = "inv"
                if a not in st.handled:
                    st.handled.append(a)
                changes.append(self.say("gained: {thing}", thing=self.name(a, st)))
            elif kind == "lose" and a in st.inventory:
                st.inventory.remove(a)
                st.items_at[a] = "gone"
                changes.append(self.say("lost: {thing}", thing=self.name(a, st)))
            elif kind == "flag" and a not in st.flags:
                st.flags.append(a)
            elif kind == "reveal" and a in self.items and (st.items_at.get(a) or "") in ("", *self.chars):
                held = st.items_at.get(a) or ""  # nowhere yet, or held by someone who lets it go
                st.items_at[a] = st.place
                changes.append(self.say("{who} lets go: {thing}", who=self.name(held, st), thing=self.name(a, st))
                               if held else self.say("appears here: {thing}", thing=self.name(a, st)))  # fmt: skip
            elif kind == "move" and a in self.chars and len(args) > 1:
                st.chars_at[a] = args[1]
                self.set_prop(st, a, "follows", False)  # sent somewhere: stops following
            elif kind == "follow" and a in self.chars:
                st.chars_at[a] = st.place
                self.set_prop(st, a, "follows", True)
                changes.append(self.say("follows you: {who}", who=self.name(a, st)))
            elif kind == "goto" and a in self.places:
                self.enter(st, a, changes)
            elif kind == "end" and a in self.endings:
                st.ending = a
            elif kind == "open" and a in self.items:
                if not self.prop(st, a, "open"):
                    changes += [
                        self.say("inside: {thing}", thing=self.name(i, st)) for i, at in st.items_at.items() if at == a
                    ]
                self.set_prop(st, a, "open", True)
                self.set_prop(st, a, "locked", False)
            elif kind == "unlock" and a in self.items:
                self.set_prop(st, a, "locked", False)
            elif kind == "set" and self.thing(a) and len(args) > 1:
                self.set_prop(st, a, "state", args[1])
            elif kind == "spend" and self.uses_left(st, a) > 0:
                changes += self.spend(st, a)
            elif kind == "refill" and a in self.items and self.uses_left(st, a) < self.max_uses(a):
                self.set_prop(st, a, "uses", self.max_uses(a))
                changes.append(self.say("full again: {thing}", thing=self.name(a, st)))
        return changes

    # ------------------------------------------------------------ uses, light
    def max_uses(self, ident: str) -> int:
        n = self.items.get(ident, {}).get("uses")
        return n if isinstance(n, int) and not isinstance(n, bool) else 0

    def uses_left(self, st: State, ident: str) -> int:
        """How many times it can still be used (a watering can, a lantern's oil); 0 when it has no uses."""
        n = st.props.get(ident, {}).get("uses")
        return n if isinstance(n, int) and not isinstance(n, bool) else self.max_uses(ident)

    def spend(self, st: State, ident: str) -> list[str]:
        """One use less; what changed, in words (empty: its `texts.empty`)."""
        left = self.uses_left(st, ident) - 1
        self.set_prop(st, ident, "uses", left)
        name = self.name(ident, st)
        if left:
            return [self.say("{thing}: {n} left", thing=name, n=left)]
        return [self.items[ident].get("texts", {}).get("empty") or self.say("empty: {thing}", thing=name)]

    def shines(self, st: State, ident: str) -> bool:
        """A light that gives light now: its conditions hold and, if it has uses, one is left."""
        light = self.items.get(ident, {}).get("light")
        if not light or (self.max_uses(ident) and not self.uses_left(st, ident)):
            return False
        return light is True or (isinstance(light, list) and self.holds(st, light))

    def dark(self, st: State, place: str | None = None) -> bool:
        """A dark place with no light in the bag or on the ground there (Inform's darkness)."""
        place = place or st.place
        if not self.places.get(place, {}).get("dark"):
            return False
        near = [i for i in self.lights if i in st.inventory or st.items_at.get(i) == place]
        return not any(self.shines(st, i) for i in near)

    def burn(self, st: State) -> list[str]:
        """Leaving a dark place lit by a light that has uses spends one of them (unless a light
        without uses was there too)."""
        p = st.place
        if not self.places.get(p, {}).get("dark") or self.dark(st):
            return []
        near = [i for i in self.lights if (i in st.inventory or st.items_at.get(i) == p) and self.shines(st, i)]
        if any(not self.max_uses(i) for i in near):
            return []
        return self.spend(st, near[0])

    # ------------------------------------------------------------ what is around
    def visible_items(self, st: State) -> list[str]:
        """Here: on the ground, in an open container here, and the doors of the ways out (only the
        doors, felt in the dark)."""
        if self.dark(st):
            return list(self.doors.get(st.place, []))
        seen = [i for i, at in st.items_at.items() if at == st.place]
        for c in seen:  # grows while open containers show what they hold
            if self.prop(st, c, "open") or not self.items.get(c, {}).get("openable"):
                seen += [i for i, at in st.items_at.items() if at == c and i not in seen]
        return seen + [d for d in self.doors.get(st.place, []) if d not in seen]

    def enter(self, st: State, place: str, changes: list[str] | None = None) -> bool:
        """The hero arrives somewhere, with the companions who follow (True: a new place). Leaving a
        dark place burns a use of the light (`changes` gets what it says)."""
        burnt = self.burn(st)
        if changes is not None:
            changes += burnt
        st.place = place
        for c in self.chars:
            if self.prop(st, c, "follows"):
                st.chars_at[c] = place
        if place in st.visited:
            return False
        st.visited.append(place)
        return True

    def held_here(self, st: State) -> list[str]:
        """Things the characters here hold (the key in the crab's claw): seen, not taken."""
        here = set(self.chars_here(st))
        return [i for i, at in st.items_at.items() if at in here]

    def can_open(self, st: State, ident: str) -> bool:
        """Closed and openable now: unlocked, or its key is in the bag (Locksmith)."""
        if not self.items.get(ident, {}).get("openable") or self.prop(st, ident, "open"):
            return False
        return not self.prop(st, ident, "locked") or self.items[ident].get("key") in st.inventory

    def passable(self, st: State, x: dict) -> bool:
        """Can the hero go this way now (a closed door opens on the way if it can)."""
        door = x.get("door")
        door_ok = not door or self.prop(st, door, "open") or self.can_open(st, door)
        return door_ok and self.holds(st, x.get("when"))

    def chars_here(self, st: State) -> list[str]:
        """The people here (in the dark, only the companions who hold the hero's hand)."""
        here = [c for c, at in st.chars_at.items() if at == st.place]
        return [c for c in here if self.prop(st, c, "follows")] if here and self.dark(st) else here

    def exits(self, st: State) -> list[dict]:
        return list(self.places.get(st.place, {}).get("exits", []))

    def scope(self, st: State) -> list[str]:
        """What can be acted on: the bag, what is here, the people and what they hold, the place (the
        same for the turn and the solver)."""
        return [*st.inventory, *self.visible_items(st), *self.chars_here(st), *self.held_here(st), st.place]

    def ending(self, st: State, eid: str) -> dict:
        """An ending of the graph, or the built-in one when the adventure can't be won any more."""
        if eid == STUCK:
            return {"id": STUCK, "kind": "lose", "text": st.doomed.get("text", "")}
        return self.endings.get(eid, {})

    # ------------------------------------------------------------ names (`Names`)
    def resolve(self, said: str, candidates: list[str]) -> tuple[str | None, list[str]]:
        return self.names.resolve(said, candidates)

    def everything(self) -> list[str]:
        return self.names.everything()

    def _labels(self, ident: str) -> list[str]:
        return self.names.labels(ident)

    def _matches(self, said: str, ident: str) -> bool:
        return self.names.matches(said, ident)

    def _find(self, said: str | None, st: State) -> tuple[str | None, list[str]]:
        return self.names.find(said, st)

    # ------------------------------------------------------------ the author's actions (`Actions`)
    def red_herring(self, a: dict) -> bool:
        return self.scripts.red_herring(a)

    def idempotent(self, a: dict) -> bool:
        return self.scripts.idempotent(a)

    def achieved(self, a: dict, st: State) -> bool:
        return self.scripts.achieved(a, st)

    def risky(self, a: dict) -> bool:
        return self.scripts.risky(a)

    def _scripted(self, st: State, verb: str, obj: str | None, tgt: str | None) -> dict | None:
        return self.scripts.scripted(st, verb, obj, tgt)

    # ------------------------------------------------------------ built-in behaviours (`Builtins`, `Ways`)
    def riddles_here(self, st: State) -> list[str]:
        return self.builtins.riddles_here(st)

    def right_answer(self, who: str, said: str) -> bool:
        return self.builtins.right_answer(who, said)

    def solve_riddle(self, st: State, who: str) -> dict:
        return self.builtins.solve_riddle(st, who)

    def press(self, st: State, button: str) -> dict:
        return self.builtins.press(st, button)

    def _open(self, st: State, obj: str) -> dict:
        return self.builtins.open(st, obj)

    def _in_closed(self, st: State, said: list[str]) -> tuple[str, str] | None:
        return self.builtins.in_closed(st, said)

    def _free(self, *about: str | None) -> dict:
        return self.builtins.free(*about)

    def _decor(self, st: State, said: list[str]) -> str:
        return self.builtins.decor(st, said)

    def _bag(self, said: str | None, st: State) -> bool:
        return self.builtins.bag(said, st)

    def _go(self, st: State, to: str) -> dict:
        return self.ways.go(st, to)

    def _through(self, st: State, x: dict) -> dict:
        return self.ways.through(st, x)

    def _where(self, st: State, said: str) -> dict:
        return self.ways.where(st, said)

    # ------------------------------------------------------------ what the narrator is told (`View`)
    def describe(self, st: State) -> dict:
        """What the hero sees here (for the narrator)."""
        return self.view.describe(st)

    def _describe(self, st: State) -> dict:
        return self.view.describe(st)

    def place_text(self, st: State) -> str:
        return self.view.place_text(st)

    def blocked(self, st: State, x: dict) -> str:
        return self.view.blocked(st, x)

    def in_reach(self, st: State) -> dict[str, str]:
        return self.view.in_reach(st)

    def _held_words(self, st: State) -> list[str]:
        return self.view.held_words(st)

    def _out_of_reach(self, st: State, said: list[str]) -> str | None:
        return self.view.out_of_reach(st, said)

    def note(self, st: State, text: str, about: str = "") -> str:
        return self.view.note(st, text, about)

    def notes_here(self, st: State) -> list[str]:
        return self.view.notes_here(st)

    def truth(self, st: State, ident: str) -> str:
        return self.view.truth(st, ident)

    def options(self, st: State) -> dict:
        """A reminder of what can be done here."""
        return self.view.options(st)

    def _options(self, st: State) -> dict:
        return self.view.options(st)

    # ------------------------------------------------------------ the turn (`Turn`), hints (`Hints`)
    def act(self, st: State, intent: dict) -> dict:
        """One intent: {verb, object, target, to}. Mutates `st`; returns the outcome for the narrator."""
        return self.turn.act(st, intent)

    def _act(self, st: State, verb: str, intent: dict) -> dict:
        return self.turn.play(st, verb, intent)

    def _doom(self, st: State, lost: str, before: dict) -> None:
        self.turn.doom(st, lost, before)

    def nudge(self, st: State) -> dict:
        """After a few tries that lead nowhere: first a reminder of what can be done, then hints."""
        return self.hints.nudge(st)

    def hint(self, st: State, force: bool = False) -> str:
        """A hint for the next useful step towards a win, stronger each time ("" = none)."""
        return self.hints.hint(st, force)
