"""The built-in behaviours: what every adventure does without the author writing it (Inform's
standard rules): opening and unlocking, taking (from containers too), refusing to drop or close,
destroying, code locks, riddles, the hero's bag, the decor, looking and talking. Going and
« where is… » are in `storyworld.ways`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.locale.fr import (
    BAG,
    _norm,
    _norm_keep,
    _stems,
)
from storyworld.model import (
    State,
)
from storyworld.verbs import (
    APPLY,
    HELD_VERBS,
    LOOK_AT,
    OPENERS,
    PRESSERS,
    SENSES,
    SYMMETRIC,
    TALK,
)

if TYPE_CHECKING:
    from storyworld.world import World


class Builtins:
    """The standard behaviours of the turn, when no scripted action applies."""

    def __init__(self, world: World) -> None:
        self.w = world

    def builtin(self, st: State, verb: str, obj: str | None, tgt: str | None) -> dict:
        """What a verb does by itself on resolved things (no scripted action took it)."""
        w = self.w
        if verb == "destroy" and obj:
            return self.destroy(st, obj)
        if obj in w.buttons and verb in PRESSERS:
            return self.press(st, obj)
        if verb == "take" and obj:
            return self.take(st, obj)
        openable = obj if obj and w.items.get(obj, {}).get("openable") else None
        keyed = False  # « utiliser la clé sur la porte », « ouvrir la porte avec la clé »
        if verb in APPLY and tgt and w.items.get(tgt, {}).get("openable") and w.items[tgt].get("key") == obj:
            openable, keyed = tgt, True
        elif verb in APPLY and tgt and openable and w.items[openable].get("key") == tgt:
            keyed = True
        elif verb in APPLY and obj and not tgt:  # « utiliser la clé » at the door: what it unlocks here
            locks = [i for i in w.visible_items(st) if w.items[i].get("key") == obj and not w.prop(st, i, "open")]
            if len(locks) == 1:
                openable, keyed = locks[0], True
        if openable and (verb in OPENERS or keyed or verb == "search" and not w.prop(st, openable, "open")):
            return self.open(st, openable)  # fouiller ou tirer un tiroir fermé, c'est l'ouvrir
        if verb == "close" and openable:  # never needed, and it could lock the hero out: it stays open
            return {"outcome": "impossible", "text": w.say("Better leave it open: it might still come in handy!")}
        if verb == "drop" and obj in st.inventory:
            # Never needed, and it could strand an object behind a one-way path: the hero keeps it.
            return {"outcome": "impossible", "text": w.say("You'd rather keep it in your bag: it might come in handy!")}
        if verb == "light" and obj == st.place and w.dark(st):  # « j'allume la lumière » with nothing to light
            return {"outcome": "not_yet", "text": w.say("It's too dark to see that: you'd need some light.")}
        if verb in SENSES and obj and (felt := self.sense(st, verb, obj)):
            return felt
        if verb in LOOK_AT:
            return self.look_at(st, obj)
        if verb in TALK and obj and (said := self.talk(st, obj)):
            return said
        out = self.free(*(w.name(i, st) for i in (obj, tgt) if i))
        if truth := " ".join(t for t in (w.view.truth(st, i) for i in (obj, tgt) if i) if t):
            out.update({"to_know": truth, "note": w.lang.free_note + " " + w.lang.truth_note})
        return out

    def free(self, *about: str | None) -> dict:
        """The child's own play: the narrator invents it, the world doesn't change."""
        return {"outcome": "free", "about": [a for a in about if a], "note": self.w.lang.free_note}

    # ------------------------------------------------------------ opening, taking, destroying
    def open(self, st: State, obj: str) -> dict:
        """Open a thing (Inform's opening; Locksmith: a locked one opens with its key in the bag)."""
        w = self.w
        item = w.items.get(obj, {})
        texts = item.get("texts", {})
        if not item.get("openable"):
            return {"outcome": "impossible", "text": w.say("It doesn't open.")}
        if w.prop(st, obj, "open"):
            return {"outcome": "done", "already": True, "text": w.say("It's already open.")}
        unlocked = False
        if w.prop(st, obj, "locked"):
            if item.get("key") not in st.inventory:
                why = (w.say("You need to find the right code.") if item.get("code")
                       else item["answer"].get("question", "") if item.get("answer") else w.say("It's locked."))  # fmt: skip
                return {"outcome": "not_yet", "text": texts.get("locked") or why}
            unlocked = True
        w.set_prop(st, obj, "open", True)
        w.set_prop(st, obj, "locked", False)
        st.score += 5
        inside = [i for i, at in st.items_at.items() if at == obj]
        out: dict = {"outcome": "done", "text": texts.get("unlock" if unlocked else "open", "")}
        if inside:
            out["changes"] = [w.say("inside: {thing}", thing=w.name(i, st)) for i in inside]
        return out

    def take(self, st: State, obj: str) -> dict:
        """Take a thing here (Inform's taking): not from someone's hands, nor what is fixed."""
        w = self.w
        if obj in st.inventory:
            return {"outcome": "done", "text": w.say("You already have {thing}.", thing=w.name(obj, st))}
        item = w.items.get(obj)
        if st.items_at.get(obj) in w.chars:  # in someone's hands: a puzzle, not a refusal
            return {"outcome": "not_yet", "text": w.say("{who} has it. You need to find a way to get it.",
                                                        who=w.name(st.items_at[obj], st))}  # fmt: skip
        if item and item.get("takeable", True) and obj in w.visible_items(st):
            self._pick_up(st, obj)
            st.score += 5
            return {"outcome": "done", "changes": [w.say("gained: {thing}", thing=w.name(obj, st))]}
        why = (item or {}).get("fixed") or w.say("You can't take that with you.")
        return {"outcome": "impossible", "text": why}

    def _pick_up(self, st: State, x: str) -> None:
        st.items_at[x] = "inv"
        st.inventory.append(x)
        if x not in st.handled:
            st.handled.append(x)

    def implicit_take(self, st: State, verb: str, obj: str | None, tgt: str | None) -> list[str]:
        """Giving, using or throwing a thing that lies here takes it first (Inform's implicit taking):
        « je donne l'algue au crabe » with the seaweed at his feet."""
        w = self.w
        held_nouns = [obj] + ([tgt] if verb in SYMMETRIC else [])
        taken = []
        for x in held_nouns:
            if verb in HELD_VERBS and x in w.items and x not in st.inventory and w.items[x].get("takeable") \
                    and x in w.visible_items(st):  # fmt: skip
                self._pick_up(st, x)
                taken.append(x)
        return taken

    def in_closed(self, st: State, said: list[str]) -> tuple[str, str] | None:
        """(item, container): a thing named that is in a closed container here (« les allumettes »
        while the drawer is shut)."""
        w = self.w
        boxes = {i for i in w.visible_items(st) if w.items[i].get("container") and not w.prop(st, i, "open")}
        inside = [i for i, at in st.items_at.items() if at in boxes]
        for m in said:
            found, _ = w.names.resolve(m, inside)
            if found:
                return found, st.items_at[found]
        return None

    def from_closed(self, st: State, verb: str, item: str, box: str) -> dict:
        """Taking what a closed container holds opens it first when it can (Inform's implicit
        opening); otherwise: it is in there, closed."""
        w = self.w
        if verb == "take" and w.can_open(st, box):
            opened = self.open(st, box)
            took = self.builtin(st, "take", item, None)
            return {**took, "text": " ".join(t for t in (opened.get("text"), took.get("text")) if t)}
        locked = w.prop(st, box, "locked") and w.items[box].get("key") not in st.inventory
        why = (" " + (w.items[box].get("texts", {}).get("locked") or w.say("It's locked."))) if locked else ""
        return {"outcome": "not_yet", "text": w.say("It's in {box}, and it's closed.", box=w.name(box, st)) + why}

    def destroy(self, st: State, obj: str) -> dict:
        """Making a thing gone for good, when it is plausible (a drawing in the sand, a key thrown into
        the sea): never a character, nor what is too sturdy (`sturdy`, containers, doors)."""
        w = self.w
        if obj in w.chars:
            return {
                "outcome": "impossible",
                "text": w.say("You can't make {who} vanish like that!", who=w.name(obj, st)),
            }
        item = w.items.get(obj)
        if not item:
            return self.free(w.name(obj, st))
        if st.items_at.get(obj) in w.chars:
            return {"outcome": "not_yet", "text": w.say("{who} has it.", who=w.name(st.items_at[obj], st))}
        if item.get("sturdy") or item.get("openable") or (not item.get("takeable") and not item.get("fragile")):
            # a thing one carries can be lost; the scenery only when it is `fragile` (a drawing in the sand)
            return {"outcome": "impossible", "text": item.get("sturdy") or w.say("It's far too sturdy for that.")}
        if obj in st.inventory:
            st.inventory.remove(obj)
        st.items_at[obj] = "gone"
        return {"outcome": "done", "changes": [w.say("gone: {thing}", thing=w.name(obj, st))],
                "note": w.lang.destroy_note, "lost": obj}  # fmt: skip

    # ------------------------------------------------------------ looking, talking
    def look_at(self, st: State, obj: str | None) -> dict:
        """Looking at a thing (its description, memories, what it holds), or around."""
        w = self.w
        if not obj or obj == st.place:
            return {"outcome": "done", **w.view.describe(st)}
        t = w.look(obj, st)
        out: dict = {"outcome": "done", "description": w.text(t.get("description", ""), t.get("details"), st)}
        if st.notes.get(obj):
            out["memories"] = st.notes[obj]
        if w.max_uses(obj):
            out["uses_left"] = w.uses_left(st, obj)
        if t.get("openable") or any(at == obj for at in st.items_at.values()):
            inside = [w.name(i, st) for i, at in st.items_at.items() if at == obj]
            out["inside"] = (
                (inside or [w.say("nothing")])
                if w.prop(st, obj, "open") or not t.get("openable")
                else [w.say("closed")]
            )
        return out

    def sense(self, st: State, verb: str, obj: str) -> dict | None:
        """Listening, smelling, touching: what the author wrote the hero hears, smells or feels there
        (`senses`), the narrator says it (None: nothing written, the narrator imagines)."""
        w = self.w
        t = w.places.get(obj) if obj == st.place else w.look(obj, st)
        text = ((t or {}).get("senses") or {}).get(verb)
        return {"outcome": "done", "description": text} if text else None

    def talk(self, st: State, obj: str) -> dict | None:
        """Talking to someone: their riddle first, else what they say (None: not someone to talk to)."""
        w = self.w
        if obj in w.riddles and not w.prop(st, obj, "answered"):
            return {"outcome": "done", "says": w.look(obj, st).get("says", ""),
                    "question": w.thing(obj)["answer"].get("question", ""),
                    "note": w.say("A riddle: ask the question, and pass the answer with the verb answer.")}  # fmt: skip
        if obj in w.chars:
            return {"outcome": "done", "says": w.look(obj, st).get("says", "…")}
        return None

    def inventory(self, st: State) -> dict:
        w = self.w

        def named(i: str) -> str:
            if not w.max_uses(i):
                return w.name(i, st)
            return w.say("{thing} ({n} uses left)", thing=w.name(i, st), n=w.uses_left(st, i))

        return {"outcome": "done", "inventory": [named(i) for i in st.inventory] or [w.say("nothing")]}

    # ------------------------------------------------------------ code locks, riddles
    def press(self, st: State, button: str) -> dict:
        """A button of a code lock: the right one moves the code on (the last opens the thing), a
        wrong one starts it again. The code's progress is the thing's `pressed` property."""
        w = self.w
        box = w.buttons[button]
        code = w.items[box]["code"]
        if w.prop(st, box, "open"):
            return {"outcome": "done", "already": True,
                    "text": w.say("{Name} is already open.", Name=w.lang.cap(w.name(box, st)))}  # fmt: skip
        press, n = code.get("press", []), w.prop(st, box, "pressed") or 0
        if button == press[n]:
            n += 1
            if n < len(press):
                w.set_prop(st, box, "pressed", n)
                return {"outcome": "done", "text": code.get("right") or w.say("Click!"), "code": f"{n} / {len(press)}"}
            w.set_prop(st, box, "pressed", 0)
            st.score += 10
            changes = w.apply(st, [f"open:{box}"])
            text = code.get("done") or w.items[box].get("texts", {}).get("open") or w.say("Clack! It opens.")
            return {"outcome": "done", "text": text, "changes": changes}
        w.set_prop(st, box, "pressed", 1 if button == press[0] else 0)
        return {"outcome": "not_yet", "text": code.get("wrong") or w.say("Bzzz! That's not the right order: it all starts again."),
                "note": w.say("Wrong order: the code starts again. The order is somewhere in the adventure.")}  # fmt: skip

    def riddles_here(self, st: State) -> list[str]:
        """The riddles still unanswered here (a character, or a door or chest that speaks)."""
        w = self.w
        here = set(w.chars_here(st)) | set(w.visible_items(st))
        return [r for r in w.riddles if r in here and not w.prop(st, r, "answered")]

    def right_answer(self, who: str, said: str) -> bool:
        """Is what the child said one of the accepted answers (« un peigne », « c'est le peigne ! »)?"""
        words = _stems(said)
        return any(_stems(a) and _stems(a) <= words for a in self.w.thing(who)["answer"].get("accept", []))

    def solve_riddle(self, st: State, who: str) -> dict:
        w = self.w
        ans = w.thing(who)["answer"]
        w.set_prop(st, who, "answered", True)
        st.score += 10
        changes = w.apply(
            st, ans.get("effects", []) + ([f"open:{who}"] if w.items.get(who, {}).get("openable") else [])
        )
        return {"outcome": "done", "text": ans.get("right") or w.say("Well done, that's it!"), "changes": changes}

    def answer(self, st: State, said: str, to: str) -> dict:
        """The child's answer to a riddle or a password, in their words."""
        w = self.w
        here = self.riddles_here(st)
        who = w.names.resolve(to, here)[0] if to else (here[0] if len(here) == 1 else None)
        if not here:
            return self.free(said)
        if who is None:
            return {"outcome": "ask", "question": w.say("who are you answering?"),
                    "choices": [w.name(r, st) for r in here]}  # fmt: skip
        if self.right_answer(who, said):
            return self.solve_riddle(st, who)
        ans = w.thing(who)["answer"]
        return {"outcome": "not_yet", "text": ans.get("wrong") or w.say("Hmm… no, that's not it. Think again!"),
                "question": ans.get("question", "")}  # fmt: skip

    # ------------------------------------------------------------ the bag, the decor
    def bag(self, said: str | None, st: State) -> bool:
        """The hero's own bag (« mon sac », « ma poche »), unless a thing of the adventure is meant (the
        pockets of the coat)."""
        if not said or (_norm(said) not in BAG and _norm_keep(said) not in BAG):
            return False
        found, amb = self.w.names.find(said, st)
        return not found and not amb

    def decor(self, st: State, said: list[str]) -> str:
        """The decor of the place a word names (« un gros caillou » → « cailloux »), or ""."""
        w = self.w
        words = _stems(" ".join(said))
        for d in w.places.get(st.place, {}).get("decor", []):
            if _stems(d) & words:
                return w.lang.bare(d)
        return ""
