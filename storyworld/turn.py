"""The turn: one intent the narrator understood, played as a pipeline.

coerce the intent → its canonical verb → special verbs (look, inventory, help, where, the bag,
go, answer) → resolve the object and the target (several meant: ask; none: a closed container,
the decor, imagination, out of reach, or absent) → implicit take → the author's action → the
built-in behaviour → bookkeeping (turns, progress, stalled, a doom after a destroy, the moment a
doomed game ends, tries before a hint, the ending).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        verb = self.w.lang.canonical_verb(intent.get("verb", ""))
        before_all = st.to_dict() if verb == "destroy" or st.destroyed else None
        before = self.progress_mark(st)
        st.turns += 1
        if verb == "destroy" and (pause := self.think_twice(st, intent)):
            return pause
        before_play = st.copy()
        res = self.play(st, verb, intent)
        texts, changes = self.w.react(before_play, st)
        if texts or changes:
            res["then"] = texts
            res["changes"] = [*res.get("changes", []), *changes]
            if "arrival" in res:  # the place as it is after the reaction (the dragon flew away)
                res["arrival"] = self.w.view.describe(st)
        self.screen(st, res, intent)
        self.count_progress(st, res, before, intent)
        place = self.w.places.get(st.place, {}).get("name", st.place)
        res.setdefault("you_are", place)  # the narrator never loses track
        if st.place not in st.seen and not self.w.dark(st):
            st.seen.append(st.place)
        self.w.learn(st)
        self.detect_doom(st, res, before_all, before)
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
        """What counts as progress (a code's pressed buttons and uses aside)."""
        props = {
            i: kept
            for i, d in st.props.items()
            if (kept := {k: v for k, v in d.items() if k not in ("pressed", "uses")})
        }
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
            if st.doomed:  # nothing to hint: the mission ends now (`end_if_doomed`)
                return {"outcome": "hint", "hint": ""}
            return {"outcome": "hint", "hint": w.hint(st, force=True) or w.say("Take a good look around you.")}
        if verb == "where":
            return w.ways.where(st, intent.get("object") or intent.get("to") or "")
        if verb == "improvise":
            return self.improvise(st, intent)
        if w.builtins.bag(intent.get("object"), st):  # « j'ouvre mon sac », « je pose mon sac »
            if verb in ("drop", "throw", "destroy", "give"):
                return {"outcome": "impossible",
                        "text": w.say("You'd rather keep your bag on your back: it might still come in handy!")}  # fmt: skip
            return w.builtins.inventory(st)
        return None

    def improvise(self, st: State, intent: dict) -> dict:
        """A creative idea of the child (« je fais un lasso avec la ficelle »), played as a step of the
        game master's screen it achieves: only a step ready now, by one of its triggers whose things
        the idea names (all of them, but what the hero already holds: the child did the work, the
        narrator chose the words). The solver's step is the same, so the adventure stays as proven.
        The author's text is not told: the child's idea is the story. A refused idea counts as a try
        that led nowhere (probing every step is no way through)."""
        w = self.w
        step = intent.get("step", "")
        named = {w.names.find(intent.get(k), st)[0] for k in ("object", "target") if intent.get(k)} - {None}
        rule = None
        if step in {s["step"] for s in w.view.gm_screen(st)}:
            scope = set(w.scope(st))
            for r in (x for x in w.rules if x["id"] == step):
                things = {x for x in (r.get("object"), r.get("target")) if x}
                if things <= scope and named & things and things - set(st.inventory) <= named:
                    rule = r
                    break
        if rule is None:
            st.stalled += 1
            return {**w.builtins.free(intent.get("object"), intent.get("target")), "note": w.lang.improvise_no_note}
        res = w.scripts.do(st, rule)
        res.pop("text", None)
        st.improvised.append(step)
        return {**res, "note": w.lang.improvise_note}

    def screen(self, st: State, res: dict, intent: dict) -> None:
        """The game master's screen, when the child's idea comes close (Riedl's narrative mediation):
        a try that didn't work, or a free moment, about the things of a step ready here. Never all
        the steps at every turn: only the ones the child is reaching for."""
        if (
            res.get("outcome") not in (*FAILED, "free")
            or self.w.lang.canonical_verb(intent.get("verb", "")) == "improvise"
        ):
            return
        named = {f for k in ("object", "target") if intent.get(k) and (f := self.w.names.find(intent.get(k), st)[0])}
        if near := self.w.view.gm_screen(st, named - {st.place}):
            res["gm_screen"] = near

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
        if not w.dark(st) and (
            decor := w.builtins.decor(st, missing)
        ):  # « je prends un galet » on the beach: there are some
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
    def count_progress(self, st: State, res: dict, before: tuple, intent: dict | None = None) -> None:
        """Progress resets the hints; a try that didn't work (not a chat, a look or imagination) counts,
        and what it was about is kept: the hint goes to the puzzle the child is on."""
        if self.progress_mark(st) != before:
            st.stalled, st.hint_level, st.reminded, st.tried = 0, 0, False, []
        elif res.get("outcome") in FAILED:
            st.stalled += 1
            for k in ("object", "target"):
                found = self.w.names.find((intent or {}).get(k), st)[0] if (intent or {}).get(k) else None
                if found and found != st.place:
                    st.tried = [*(x for x in st.tried if x != found), found][-4:]

    def think_twice(self, st: State, intent: dict) -> dict | None:
        """Before a destroy that would make the mission impossible: once, the story makes the hero
        think twice (a character reacts, the thing seems precious), never a « game over » warning;
        if the child does it again, it happens (Nelson: an irrevocable act needs due warning)."""
        w = self.w
        trial = st.copy()
        res = self.play(trial, "destroy", intent)
        lost = res.get("lost")
        if not lost or f"destroy:{lost}" in st.warned or st.doomed:
            return None
        found = solve(w, trial, first_path=True)
        if found.get("path") or not found.get("complete"):
            return None
        st.warned.append(f"destroy:{lost}")
        witness = next(iter(w.chars_here(st)), "")
        return {"outcome": "warning", "precious": w.name(lost, st),
                **({"witness": w.name(witness, st)} if witness else {}),
                "note": w.lang.think_twice_note}  # fmt: skip

    def detect_doom(self, st: State, res: dict, before_all: dict | None, mark: tuple) -> None:
        """After a destroy, is the adventure still winnable? A destroy takes the game off the proven
        ways (the solver never destroys): from then on, every turn that changes something is checked
        again (the second key traded away later). (`lost` never reaches the narrator.)"""
        lost = res.pop("lost", None)
        if before_all is None or st.doomed:
            return
        if lost:
            st.destroyed = lost
            self.doom(st, lost, before_all)
        elif st.destroyed and self.progress_mark(st) != mark:
            self.doom(st, st.destroyed, before_all)

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
        if st.doomed and not st.ending and (runs_into_it or st.stalled >= self.w.policy["hint_after"]):
            st.ending = STUCK  # he runs into it now: the mission stops here
        elif failed and not st.ending:
            res["tries_before_hint"] = max(0, self.w.policy["hint_after"] - st.stalled)

    def tell_ending(self, st: State, res: dict) -> None:
        """The ending reached, if any (a win scores)."""
        if st.ending:
            e = self.w.ending(st, st.ending)
            res.update(outcome="end", ending=e.get("kind", "win"), ending_text=e.get("text", ""))
            if e.get("kind") == "win":
                st.score += 20
