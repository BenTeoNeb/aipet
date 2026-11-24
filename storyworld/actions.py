"""The author's actions: what they are (a clue, a risk, already done) and which one an intent does.

Matching an intent to a scripted action ranks every rule (one per trigger) on two scales, then
chooses among the ranked ones.

Verb tier (the child's canonical verb against the trigger's):

    ======  =====  ==============================================================================
    tier    name   when
    ======  =====  ==============================================================================
    2       EXACT  the same verb
    1       NEAR   the same class (APPLY, TALK, LOOK_AT): never for a risky action nor « hit »
    1       REMAP  no verb match, but the things say it (TADS' remap): the things rank at least
                   PLUS_TOOL and the trigger has a target, or « take » names a thing to catch
                   with a tool the hero holds; never a NO_REMAP verb, a risky action, nor taking
                   a thing that can be taken
    0       NONE   not this action
    ======  =====  ==============================================================================

Things rank (what the child named against the trigger's object and target):

    ======  =========  ===========================================================================
    rank    name       when
    ======  =========  ===========================================================================
    3       SAME       the same things, in any order (the place named aside)
    2       PLUS_TOOL  the same things plus a tool the hero holds (« ouvrir la porte avec la clé »)
    1       PARTIAL    one of them left out but here, or only the tool the action needs
                       (« utiliser la clé » at the door)
    0       NONE       not this action
    ======  =========  ===========================================================================

A PARTIAL match with a verb short of EXACT is too far (« allumer la lumière » must not feed the
fish), unless the verb is « use » or the hero holds the tool to catch with.

The choice, in order: the highest verb tier that holds a real step that can happen (again), else
the highest tier at all; within it, what can run now; then the most things; a clue never hides a
real action on the same things. Equal ranks keep the author's order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from storyworld.model import (
    State,
)
from storyworld.rules import (
    args_of,
    of_kind,
    parse_all,
)
from storyworld.verbs import (
    APPLY,
    IDEMPOTENT,
    LOOK_AT,
    NO_REMAP,
    TALK,
)

if TYPE_CHECKING:
    from storyworld.world import World

# Verb tiers.
VERB_NONE, VERB_NEAR, VERB_EXACT = 0, 1, 2
VERB_CLASSES = (APPLY, TALK, LOOK_AT)  # a verb of the same class is near
# Things ranks.
THINGS_NONE, THINGS_PARTIAL, THINGS_PLUS_TOOL, THINGS_SAME = 0, 1, 2, 3


@dataclass(frozen=True)
class Match:
    """A rule the intent may mean, with its verb tier and things rank."""

    verb: int
    things: int
    rule: dict


class Actions:
    """Predicates on the author's actions, and the matching of an intent to one of them."""

    def __init__(self, world: World) -> None:
        self.w = world

    # ------------------------------------------------------------ what an action is
    def red_herring(self, a: dict) -> bool:
        """A clue or a joke, never a step: no effect, or a condition no effect can ever make true
        (« flag:jamais »). Told as story, it is not a failed try and the solver ignores it."""
        if not a.get("effects"):
            return True
        return any(r not in self.w.settable for r in args_of(a.get("when"), "flag"))

    def idempotent(self, a: dict) -> bool:
        """Doing it again changes nothing (flags, reveals, moves): « fouiller » after « ouvrir »."""
        return all(r.kind in IDEMPOTENT for r in parse_all(a.get("effects")))

    def achieved(self, a: dict, st: State) -> bool:
        """Its effects are already true (another way of doing the same thing worked)."""
        w = self.w
        effects = a.get("effects", [])
        if not effects or not self.idempotent(a) or of_kind(effects, "goto", "end"):
            return False
        for r in parse_all(effects):
            kind, arg = r.kind, r.arg
            if kind == "flag" and arg not in st.flags:
                return False
            if kind == "reveal" and (st.items_at.get(arg) or "") in ("", *w.chars):
                return False
            if kind == "move" and st.chars_at.get(arg) != r.last:
                return False
            if kind == "open" and not w.prop(st, arg, "open"):
                return False
            if kind == "unlock" and w.prop(st, arg, "locked"):
                return False
            if kind == "follow" and not w.prop(st, arg, "follows"):
                return False
            if kind == "set" and w.prop(st, arg, "state") != r.last:
                return False
            if kind == "refill" and w.uses_left(st, arg) < w.max_uses(arg):
                return False
        return True

    def risky(self, a: dict) -> bool:
        """An action that loses the game: only its exact verb triggers it."""
        return any(self.w.endings.get(e, {}).get("kind") == "lose" for e in args_of(a.get("effects"), "end"))

    def again(self, a: dict, st: State) -> bool:
        """Can it happen (again)? Once, unless repeatable; idempotent ones whenever their effect is undone."""
        once = a["id"] not in st.done_actions or a.get("repeatable") or a["id"] in self.w.idempotent_ids
        return bool(once) and not self.achieved(a, st)

    # ------------------------------------------------------------ ranking an intent
    def verb_tier(self, a: dict, verb: str) -> int:
        av = a.get("verb", "")
        if av == verb:
            return VERB_EXACT
        if self.risky(a) or "hit" in (av, verb):  # never lose, or hit someone, by a near-synonym
            return VERB_NONE
        if any(av in group and verb in group for group in VERB_CLASSES):
            return VERB_NEAR
        return VERB_NONE

    def things_rank(self, a: dict, obj: str | None, tgt: str | None, st: State) -> int:
        """How closely the things named are the trigger's (see the module's table)."""
        wanted = {x for x in (a.get("object"), a.get("target")) if x}
        said = {x for x in (obj, tgt) if x}
        if st.place in said and st.place not in wanted:  # « utiliser la canne sur l'étang » at the pond
            said.discard(st.place)
        if said == wanted:
            return THINGS_SAME
        if wanted < said and all(x in st.inventory for x in said - wanted):
            return THINGS_PLUS_TOOL
        scope = self.w.scope(st)
        if said and said < wanted and all(x in scope for x in wanted - said):
            return THINGS_PARTIAL
        needs = args_of(a.get("when"), "has")
        if said and said <= needs and all(x in scope for x in wanted):  # « utiliser la clé » → the door
            return THINGS_PARTIAL
        return THINGS_NONE

    def held_tool(self, a: dict, obj: str | None, tgt: str | None, st: State) -> bool:
        """Only a thing (not someone) to catch is named, and the hero holds the rest (« attraper les
        lucioles » with the jar in the bag)."""
        wanted = {x for x in (a.get("object"), a.get("target")) if x}
        said = {x for x in (obj, tgt) if x}
        return bool(said) and said < wanted and all(x in self.w.items and x not in st.inventory for x in said) and all(
            x in st.inventory for x in wanted - said)  # fmt: skip

    def remap_allowed(self, a: dict, verb: str, obj: str | None, st: State) -> bool:
        """May the child's verb do this action because the things match? Never for a loss, a blow or
        a look; and taking a thing that can be taken is taking it."""
        if verb in NO_REMAP or a.get("verb") in NO_REMAP or self.risky(a):
            return False
        w = self.w
        return not (verb == "take" and obj in w.visible_items(st) and w.items.get(obj, {}).get("takeable"))

    def match(self, a: dict, verb: str, obj: str | None, tgt: str | None, st: State) -> Match | None:
        """The rule's ranks for this intent, or None when it is not meant."""
        tier, things = self.verb_tier(a, verb), self.things_rank(a, obj, tgt, st)
        held = things == THINGS_PARTIAL and verb == "take" and self.held_tool(a, obj, tgt, st)
        things_say_it = things >= THINGS_PLUS_TOOL and a.get("target") or held
        if tier == VERB_NONE and things_say_it and self.remap_allowed(a, verb, obj, st):
            tier = VERB_NEAR  # REMAP
        if things == THINGS_PARTIAL and tier < VERB_EXACT and verb != "use" and not held:
            return None  # half the things AND another verb: too far
        if tier and things:
            return Match(tier, things, a)
        return None

    def ranked(self, st: State, verb: str, obj: str | None, tgt: str | None) -> list[Match]:
        """Every rule the intent may mean, in the author's order (rules bound to another place aside)."""
        out = []
        for a in self.w.rules:
            if a.get("place") and a["place"] != st.place:
                continue
            if m := self.match(a, verb, obj, tgt, st):
                out.append(m)
        return out

    # ------------------------------------------------------------ choosing and doing
    def scripted(self, st: State, verb: str, obj: str | None, tgt: str | None) -> dict | None:
        """The author's action this intent does, done (or why not); None when none is meant."""
        ranked = self.ranked(st, verb, obj, tgt)
        if not ranked:
            return None
        # The child's verb first (its tier), then: what can run now, the closest things, real steps
        # before clues — a clue on the same things must not hide the real action.
        steps = [m for m in ranked if self.again(m.rule, st) and not self.red_herring(m.rule)]
        tier = max(m.verb for m in (steps or ranked))  # a done action or a clue never hides a real step
        fresh = [m for m in ranked if m.verb == tier and self.again(m.rule, st)]
        ready = [m for m in fresh if self.w.holds(st, m.rule.get("when")) and not self.red_herring(m.rule)]
        if ready:
            return self._run_best(st, ready)
        if not fresh:
            return {"outcome": "done", "already": True, "text": self.w.say("It's already done."), "changes": []}
        return self._not_now(st, fresh)

    def _run_best(self, st: State, ready: list[Match]) -> dict:
        """The closest of the actions that can run now: warned first if it loses, asked about when
        several want another thing, else done."""
        w = self.w
        best = max(m.things for m in ready)
        top = list({m.rule["id"]: m.rule for m in ready if m.things == best}.values())  # one per action
        risky = [a for a in top if self.risky(a) and a["id"] not in st.warned]
        if risky:  # the first time: a warning, the child may still do it (losing stays possible)
            st.warned.append(risky[0]["id"])
            return {"outcome": "warning", "text": risky[0].get("warning") or w.lang.warning}
        if best == THINGS_PARTIAL and len({a.get("target") or a.get("object") for a in top}) > 1:
            return {"outcome": "ask", "question": w.say("with what / on what?"),
                    "choices": [w.name(a.get("target") or a.get("object") or "", st) for a in top]}  # fmt: skip
        return self.do(st, top[0])

    def do(self, st: State, a: dict) -> dict:
        """Do an action: its effects, its points, its text (and the arrival when it moves the hero)."""
        w = self.w
        taken = w.builtins.take_for(st, a)
        changes = [w.say("gained: {thing}", thing=w.name(i, st)) for i in taken] + w.apply(st, a.get("effects", []))
        st.done_actions.append(a["id"])
        st.score += int(a.get("points", 5))
        out: dict = {"outcome": "done", "text": a.get("text", ""), "changes": changes}
        if of_kind(a.get("effects"), "goto"):
            out["arrival"] = w.view.describe(st)
        return out

    def _empty(self, st: State, real: list[dict]) -> str:
        """An action that would run but for a thing with no use left: what that thing says when empty."""
        w = self.w
        for a in real:
            failing = [r for r in parse_all(a.get("when")) if not w.holds(st, [str(r)])]
            if failing and all(r.kind == "charged" and not r.negated for r in failing):
                x = failing[0].arg
                return w.items.get(x, {}).get("texts", {}).get("empty") or w.say(
                    "{Name} is empty.", Name=w.lang.cap(w.name(x, st))
                )
        return ""

    def _not_now(self, st: State, fresh: list[Match]) -> dict:
        """Nothing can run now: a clue told as story, or the author's nudge (« not yet »)."""
        best = max(m.things for m in fresh)
        top = [m.rule for m in fresh if m.things == best]
        clues = [a for a in top if self.red_herring(a)]
        real = [a for a in top if not self.red_herring(a)]
        if clues and not real:  # a joke or a clue the author wrote: told as story
            a = clues[0]
            return {"outcome": "clue", "text": (a.get("text") if not a.get("effects") and self.w.holds(st, a.get("when"))
                                                else a.get("not_yet")) or a.get("text", "")}  # fmt: skip
        if empty := self._empty(st, real):
            return {"outcome": "not_yet", "text": empty}
        nudge = next((a.get("not_yet") for a in real if a.get("not_yet")), "")
        return {"outcome": "not_yet", "text": nudge or self.w.say("Not yet… something is missing.")}
