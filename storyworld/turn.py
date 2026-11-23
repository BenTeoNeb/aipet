"""The turn: one intent the narrator understood, played as a pipeline.

coerce the intent → its canonical verb → special verbs (look, inventory, help, where, the bag,
go, answer) → resolve the object and the target (several meant: ask; none: a closed container,
the decor, imagination, out of reach, or absent) → implicit take → the author's action → the
built-in behaviour → bookkeeping (turns, progress, stalled, a doom after a destroy, the moment a
doomed game ends, tries before a hint, the ending).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.locale.fr import (
    canonical_verb,
)
from storyworld.model import (
    State,
)
from storyworld.solver import (
    solve,
)
from storyworld.verbs import (
    FAILED,
    LOOK_AT,
    NEEDS_A_THING,
    SENSES,
    STUCK,
    TALK,
    WAYS,
)

if TYPE_CHECKING:
    from storyworld.world import World


class Turn:
    """`act()` and its stages."""

    def __init__(self, world: World) -> None:
        self.w = world

    def act(self, st: State, intent: dict) -> dict:
        """One intent: {verb, object, target, to}. Mutates `st`; returns the outcome for the narrator."""
        if st.ending:
            return self.over(st)
        intent = self.coerce(intent)
        verb = canonical_verb(intent.get("verb", ""))
        before_all = st.to_dict() if verb == "destroy" else None
        before = self.progress_mark(st)
        st.turns += 1
        res = self.play(st, verb, intent)
        self.count_progress(st, res, before)
        place = self.w.places.get(st.place, {}).get("name", st.place)
        res.setdefault("you_are", place)  # the narrator never loses track
        self.detect_doom(st, res, before_all)
        self.end_if_doomed(st, verb, res)
        self.tell_ending(st, res)
        return res

    # ------------------------------------------------------------ before
    def over(self, st: State) -> dict:
        """The adventure has ended: nothing more happens."""
        kind = self.w.ending(st, st.ending or "").get("kind", "lose")
        return {"outcome": "end", "ending": kind, "note": self.w.say("The adventure is already over.")}

    @staticmethod
    def coerce(intent: dict) -> dict:
        """A model's odd types become words; missing values are dropped."""
        return {k: v if isinstance(v, str) else " ".join(map(str, v)) if isinstance(v, list) else str(v)
                for k, v in intent.items() if v is not None}  # fmt: skip

    @staticmethod
    def progress_mark(st: State) -> tuple:
        """What counts as progress (a code's pressed buttons aside)."""
        props = {i: {k: v for k, v in d.items() if k != "pressed"} for i, d in st.props.items()}
        return len(st.inventory), len(st.flags), len(st.visited), len(st.done_actions), repr(props)

    # ------------------------------------------------------------ the stages
    def play(self, st: State, verb: str, intent: dict) -> dict:
        """The intent's outcome, through the stages (the first that answers)."""
        special = self.special(st, verb, intent)
        if special is not None:
            return special
        verb, intent = self.into_bag(st, verb, intent)
        moved = self.go_or_answer(st, verb, intent)
        if moved is not None:
            return moved
        if verb in ("search", *SENSES) and not intent.get("object"):
            intent = {**intent, "object": st.place}  # « je cherche partout », « j'écoute »: here
        return self.on_things(st, verb, intent)

    def special(self, st: State, verb: str, intent: dict) -> dict | None:
        """Verbs that need no thing: look around, the inventory, help, where, the hero's own bag."""
        w = self.w
        if verb == "look" and not intent.get("object"):
            return {"outcome": "done", **w.view.describe(st)}
        if verb == "inventory":
            return w.builtins.inventory(st)
        if verb == "help":
            return {"outcome": "hint", "hint": w.hint(st, force=True) or w.say("Take a good look around you.")}
        if verb == "where":
            return w.ways.where(st, intent.get("object") or intent.get("to") or "")
        if w.builtins.bag(intent.get("object"), st):  # « j'ouvre mon sac », « je pose mon sac »
            if verb in ("drop", "throw", "destroy", "give"):
                return {"outcome": "impossible",
                        "text": w.say("You'd rather keep your bag on your back: it might still come in handy!")}  # fmt: skip
            return w.builtins.inventory(st)
        return None

    def into_bag(self, st: State, verb: str, intent: dict) -> tuple[str, dict]:
        """« je mets l'algue dans mon sac »: taking it."""
        if self.w.builtins.bag(intent.get("target"), st) and intent.get("object"):
            return "take", {"verb": "take", "object": intent["object"]}
        return verb, intent

    def go_or_answer(self, st: State, verb: str, intent: dict) -> dict | None:
        """Going somewhere (« monter en haut », « nager jusqu'à l'île »), or answering a riddle."""
        w = self.w
        if verb == "go" or verb in WAYS and intent.get("to") and not intent.get("object"):
            return w.ways.go(st, intent.get("to") or intent.get("object") or "")
        if verb == "answer":
            return w.builtins.answer(st, intent.get("object") or intent.get("said") or "", intent.get("target") or "")
        return None

    def on_things(self, st: State, verb: str, intent: dict) -> dict:
        """Resolve the object and the target, then act on them (or say why not)."""
        w = self.w
        obj, amb = w.names.find(intent.get("object"), st)
        if amb:
            return {"outcome": "ask", "question": w.say("which one?"), "choices": [w.name(i, st) for i in amb]}
        tgt, amb_t = w.names.find(intent.get("target"), st)
        if amb_t:
            return {
                "outcome": "ask",
                "question": w.say("on what / to whom?"),
                "choices": [w.name(i, st) for i in amb_t],
            }
        named = (
            ((intent.get("object"), obj),)
            if verb in TALK
            else ((intent.get("object"), obj), (intent.get("target"), tgt))
        )
        missing = [said for said, found in named if said and not found]
        if missing:
            return self.missing(st, verb, intent, missing)
        return self.perform(st, verb, obj, tgt)

    def missing(self, st: State, verb: str, intent: dict, missing: list[str]) -> dict:
        """Words that name nothing in reach: in a closed container, the decor, imagination, seen but
        out of reach, or absent."""
        w = self.w
        if w.dark(st) and (verb in NEEDS_A_THING or verb in LOOK_AT or verb in TALK or verb in ("touch", "climb")):
            return {"outcome": "not_yet", "text": w.say("It's too dark to see that: you'd need some light."),
                    "note": w.lang.dark_note}  # fmt: skip
        if inside := w.builtins.in_closed(st, missing):
            return w.builtins.from_closed(st, verb, *inside)
        if decor := w.builtins.decor(st, missing):  # « je prends un galet » on the beach: there are some
            out = w.builtins.free(intent.get("object"), intent.get("target"))
            out.update({"to_know": w.say("There are {decor} here, it's scenery: he can take some or play "
                                         "with them, but it doesn't help solve the adventure (nothing is "
                                         "gained).", decor=decor),
                        "note": w.lang.free_note + " " + w.lang.truth_note})  # fmt: skip
            return out
        if verb not in NEEDS_A_THING:  # « chanter une chanson », « regarder le ciel »
            return w.builtins.free(intent.get("object"), intent.get("target"))
        if seen_here := w.view.out_of_reach(st, missing):
            name = w.name(seen_here, st)
            text = (
                "You see {name}, but you can't catch them like that. You need to find a way."
                if w.lang.plural(name)
                else "You see {name}, but you can't catch it like that. You need to find a way."
            )
            return {"outcome": "not_yet", "text": w.say(text, name=name)}
        everything = w.names.everything()
        seen = [i for i in (w.names.resolve(m, everything)[0] for m in missing) if i and w.view.seen_elsewhere(st, i)]
        return {"outcome": "absent", "not_here": missing, "note": w.lang.absent_note
                + (w.say(" (He saw it elsewhere in the adventure.)") if seen else "")}  # fmt: skip

    def perform(self, st: State, verb: str, obj: str | None, tgt: str | None) -> dict:
        """Implicit take, then the author's action, else the built-in behaviour."""
        w = self.w
        taken = w.builtins.implicit_take(st, verb, obj, tgt)
        res = w.scripts.scripted(st, verb, obj, tgt) or w.builtins.builtin(st, verb, obj, tgt)
        if taken:
            res["changes"] = [w.say("gained: {thing}", thing=w.name(i, st)) for i in taken] + res.get("changes", [])
        return res

    # ------------------------------------------------------------ bookkeeping
    def count_progress(self, st: State, res: dict, before: tuple) -> None:
        """Progress resets the hints; a try that didn't work (not a chat, a look or imagination) counts."""
        if self.progress_mark(st) != before:
            st.stalled, st.hint_level, st.reminded = 0, 0, False
        elif res.get("outcome") in FAILED:
            st.stalled += 1

    def detect_doom(self, st: State, res: dict, before_all: dict | None) -> None:
        """After a destroy, is the adventure still winnable? (`lost` never reaches the narrator.)"""
        if before_all is not None and res.get("lost") and not st.doomed:
            self.doom(st, res.pop("lost"), before_all)
        res.pop("lost", None)

    def doom(self, st: State, lost: str, before: dict) -> None:
        """After a thing is destroyed: can a win still be reached? If not, the adventure will end
        when the child runs into it (Sierra's « dead man walking », but told then, not later)."""
        w = self.w
        found = solve(w, st, first_path=True)  # rare (after a destroy): the full search, not the hints' quick one
        if not found.get("path") and found.get("complete"):
            name = w.name(lost, st)
            st.doomed = {"lost": lost, "before": before,
                         "text": w.say("Without {thing}, there's no going further: the mission ends here.",
                                       thing=name)}  # fmt: skip

    def end_if_doomed(self, st: State, verb: str, res: dict) -> None:
        """A doomed game ends when the child runs into it; otherwise a failed try tells how many
        are left before a hint."""
        failed = res.get("outcome") in FAILED
        runs_into_it = failed or res.get("outcome") == "absent" or verb == "help"
        if st.doomed and not st.ending and (runs_into_it or st.stalled >= self.w.spec["hint_after"]):
            st.ending = STUCK  # he runs into it now: the mission stops here
        elif failed and not st.ending:
            res["tries_before_hint"] = max(0, self.w.spec["hint_after"] - st.stalled)

    def tell_ending(self, st: State, res: dict) -> None:
        """The ending reached, if any (a win scores)."""
        if st.ending:
            e = self.w.ending(st, st.ending)
            res.update(outcome="end", ending=e.get("kind", "win"), ending_text=e.get("text", ""))
            if e.get("kind") == "win":
                st.score += 20
