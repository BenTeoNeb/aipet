"""What the narrator is told: the place as the hero sees it, what can be named, what is true, the
memories kept, and a reminder of what can be done. It reads the world; it never changes it (but for
the notes the narrator asks to keep)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from storyworld.model import (
    State,
)
from storyworld.rules import (
    args_of,
)
from storyworld.verbs import (
    NOTES_KEPT,
)

if TYPE_CHECKING:
    from storyworld.world import World


class View:
    """The narrator's view of the world."""

    def __init__(self, world: World) -> None:
        self.w = world

    # ------------------------------------------------------------ the place
    def describe(self, st: State) -> dict:
        """What the hero sees here (for the narrator)."""
        w = self.w
        p = w.places.get(st.place, {})
        dark = w.dark(st)
        return {
            "place": p.get("name", st.place),
            **({"in_the_dark": w.lang.dark_note} if dark else {}),
            **({"senses": list(p["senses"].values())} if dark and isinstance(p.get("senses"), dict) else {}),
            "description": self.place_text(st),
            "here": [w.name(i, st) for i in w.visible_items(st)],
            "people": [w.name(c, st) for c in w.chars_here(st)],
            **({"held": held} if (held := self.held_words(st)) else {}),
            "exits": [
                {
                    "to_label": x.get("label") or w.name(x.get("to", "")),
                    "direction": x.get("dir", ""),
                    **({} if w.passable(st, x) else {"closed": self.blocked(st, x)}),
                }
                for x in w.exits(st)
            ],  # fmt: skip
            **({"decor": p["decor"]} if p.get("decor") and not dark else {}),
            **({"personas": personas} if (personas := self.personas(st)) else {}),
            **({"progress": progress} if (progress := self.progress(st)) else {}),
            **({"memories": notes} if (notes := self.notes_here(st)) else {}),
            "in_reach": self.in_reach(st),
            **({"worth_exploring": explore} if (explore := self.worth_exploring(st)) else {}),
        }

    def place_text(self, st: State) -> str:
        """The place's description: base, details that hold, then the initial appearance of what is
        here (things never moved, people) — Inform's room description."""
        w = self.w
        p = w.places.get(st.place, {})
        if w.dark(st):  # Inform's « Darkness »: what the author says is felt there, nothing seen
            return p["dark"] if isinstance(p.get("dark"), str) else w.say("It's dark here.")
        text = p.get("description", "")
        for v in p.get("variants", []):  # version 1: whole descriptions
            if w.holds(st, v.get("when")):
                text = v.get("description", text)
        parts = [w.text(text, p.get("details"), st)]
        for i in w.visible_items(st):
            t = w.look(i, st)
            if t.get("initial") and i not in st.handled and st.items_at.get(i) == w.items[i].get("at"):
                parts.append(t["initial"])
        parts += [
            w.look(c, st)["initial"]
            for c in w.chars_here(st)
            if w.look(c, st).get("initial") and not w.prop(st, c, "follows")
        ]  # a companion is with you
        return " ".join(x for x in parts if x)

    def gm_screen(self, st: State, about: set[str] | None = None) -> list[dict]:
        """The game master's screen: the steps the hero is ready for here (their conditions hold, their
        things are in reach), each with what it achieves; with `about`, only the steps on those things
        (what the child is reaching for). The narrator never tells them; a creative idea of the child
        that achieves one of them may be played as that step (`improvise`)."""
        w = self.w
        goals = {z.get("action"): z.get("goal", "") for z in w.g.get("puzzles", []) if isinstance(z, dict)}
        scope = set(w.scope(st))
        out: dict[str, dict] = {}
        for a in w.rules:
            if a["id"] in out or (a.get("place") and a["place"] != st.place):
                continue
            things = [x for x in (a.get("object"), a.get("target")) if x]
            if not things or not all(x in scope for x in things) or not self._ready(st, a):
                continue
            if about is not None and not about & set(things):
                continue
            if w.scripts.risky(a) or w.scripts.red_herring(a) or not w.scripts.again(a, st):
                continue
            goal = goals.get(a["id"]) or a.get("hint") or ""
            out[a["id"]] = {"step": a["id"], "goal": goal, "with": [w.name(x, st) for x in things]}
        return list(out.values())

    def _ready(self, st: State, rule: dict) -> bool:
        """Its conditions hold once its trigger has picked up what lies here (the turn takes first,
        `Turn.perform`: « je cueille les framboises et je les lance à Patapouf »)."""
        w = self.w
        if not w.builtins.would_take(st, rule.get("verb", ""), rule.get("object"), rule.get("target")):
            return w.holds(st, rule.get("when"))
        trial = st.copy()
        w.builtins.take_for(trial, rule)
        return w.holds(trial, rule.get("when"))

    def progress(self, st: State) -> dict:
        """Where the story stands, for the pacing (the puzzles of the chart solved, and the act: the
        beginning, the middle, the finale), never what is left to do."""
        w = self.w
        puzzles = [
            z["action"] for z in w.g.get("puzzles", []) if isinstance(z, dict) and isinstance(z.get("action"), str)
        ]
        if not puzzles:
            return {}

        def solved(x: str) -> bool:
            if x in w.riddles:
                return bool(w.prop(st, x, "answered"))
            if w.items.get(x, {}).get("openable"):  # a code lock, a locked door
                return bool(w.prop(st, x, "open"))
            return x in st.done_actions

        n = sum(map(solved, puzzles))
        act = "beginning" if n * 3 < len(puzzles) else "middle" if n * 3 < 2 * len(puzzles) else "finale"
        return {"solved": n, "of": len(puzzles), "act": w.say(act)}

    def blocked(self, st: State, x: dict) -> str:
        """Why a way out is shut (its door, or the author's words)."""
        w = self.w
        door = x.get("door")
        if door and not w.prop(st, door, "open"):
            texts = w.items.get(door, {}).get("texts", {})
            locked = w.prop(st, door, "locked")
            closed = "{Name} is locked." if locked else "{Name} is closed."
            return texts.get("locked" if locked else "closed") or w.say(closed, Name=w.lang.cap(w.name(door, st)))
        return x.get("blocked") or w.say("blocked")

    def personas(self, st: State) -> dict[str, str]:
        """How the people here are played (their `persona`), for the narrator's improvisation."""
        w = self.w
        return {w.name(c, st): p for c in w.chars_here(st) if (p := w.look(c, st).get("persona"))}

    def worth_exploring(self, st: State) -> list[str]:
        """Things here that something can be done with (an action, a pickup, a character to talk to):
        so the narrator invites curiosity, not only the way out. Never says what to do with them."""
        w = self.w
        here = set(w.scope(st))
        used = {x for a in w.rules if not w.scripts.red_herring(a) for x in (a.get("object"), a.get("target")) if x}
        shut = {i for i in w.visible_items(st) if w.items[i].get("openable") and not w.prop(st, i, "open")}
        things = [i for i in w.visible_items(st) if i in used | shut or w.items[i].get("takeable", True)]
        return [w.name(x, st) for x in [*w.chars_here(st), *things] if x in here][:4]

    def in_reach(self, st: State) -> dict[str, str]:
        """What the narrator can name, by id: the things here, the people, what they hold, the bag,
        and the ways out (« → »). It sends these ids; the engine never guesses from words."""
        w = self.w
        out = {i: w.name(i, st) for i in [*w.visible_items(st), *w.chars_here(st), *w.held_here(st)]}
        out |= {i: w.say("{thing} (in your bag)", thing=w.name(i, st)) for i in st.inventory}
        out |= {x["to"]: f"→ {x.get('label') or w.name(x['to'])} ({x.get('dir', '')})".replace(" ()", "")
                for x in w.exits(st) if x.get("to") in w.places}  # fmt: skip
        return out

    def held_words(self, st: State) -> list[str]:
        """« Le crabe tient la clé »: what the characters here hold, in words."""
        w = self.w
        return [w.say("{who} holds {thing}", who=w.name(st.items_at[i], st), thing=w.name(i, st))
                for i in w.held_here(st)]  # fmt: skip

    # ------------------------------------------------------------ what is shown, what was seen
    def out_of_reach(self, st: State, said: list[str]) -> str | None:
        """An object described here but not reachable yet (the hat on its hook, the whistle in the
        magpie's nest): an action here will give it. « Not here » would contradict the description."""
        w = self.w
        here = set(w.scope(st))
        coming = set()
        for a in w.rules:
            if a.get("place") == st.place or {a.get("object"), a.get("target")} & here - {None, st.place}:
                coming |= args_of(a.get("effects"), "reveal", "gain")
        coming = {i for i in coming if i in w.items and st.items_at.get(i) == ""}
        shown = self.w.lang.stems(
            self.describe(st)["description"] + " " + " ".join(w.look(t, st).get("description", "") for t in here)
        )
        for m in said:
            found, _ = w.names.resolve(m, sorted(coming))
            if found and any(
                self.w.lang.stems(label) and self.w.lang.stems(label) <= shown for label in w.names.labels(found)
            ):
                return found  # named in what he sees here: « tu le vois » (not for a secret under the hay)
        return None

    def seen_elsewhere(self, st: State, ident: str) -> bool:
        """The hero saw it in a place he has seen lit, not here."""
        where = st.items_at.get(ident) or st.chars_at.get(ident) or ""
        return where in st.seen and where != st.place

    # ------------------------------------------------------------ memories
    def note(self, st: State, text: str, about: str = "") -> str:
        """Keep what happened that the world doesn't model (a nickname, a thing played with, a detail
        the narrator invented), about a place, a character or a thing (« about »), so it stays true.
        The last few are kept. Returns whose it is."""
        w = self.w
        who = (w.names.resolve(about, w.names.everything())[0] if about else None) or st.place
        notes = st.notes.setdefault(who, [])
        if text.strip() and text.strip()[:160] not in notes:
            notes.append(text.strip()[:160])
            del notes[:-NOTES_KEPT]
        return who

    def notes_here(self, st: State) -> list[str]:
        """The memories of this place, and of the people and things around."""
        w = self.w
        out = list(st.notes.get(st.place, []))
        for x in [*w.chars_here(st), *w.visible_items(st), *st.inventory]:
            out += [w.say("{about}: {note}", about=w.name(x, st), note=n) for n in st.notes.get(x, [])]
        return out

    # ------------------------------------------------------------ what is true, what can be done
    def truth(self, st: State, ident: str) -> str:
        """What is true about a thing the child acts on in their own way (a locked door, a code, a
        riddle, someone holding it): the narrator invents the scene, and says this."""
        w = self.w
        item = w.items.get(ident, {})
        if item.get("openable") and not w.prop(st, ident, "open"):
            if item.get("code"):
                return item.get("texts", {}).get("locked") or w.say(
                    "{Name} has a code: you need to find the right one.", Name=w.lang.cap(w.name(ident, st))
                )
            if item.get("answer") and not w.prop(st, ident, "answered"):
                return w.say("{Name} only opens with the answer to its riddle: {question}",
                             Name=w.lang.cap(w.name(ident, st)), question=item["answer"].get("question", ""))  # fmt: skip
            if w.prop(st, ident, "locked"):
                if item.get("key") in st.inventory:
                    return w.say("It's locked, and {key} is in your bag.", key=w.name(item["key"], st))
                return item.get("texts", {}).get("locked") or w.say("It's locked: you'd need a way to open it.")
        if ident in w.chars and w.thing(ident).get("answer") and not w.prop(st, ident, "answered"):
            return w.say("{Name} is waiting for the answer to their riddle.", Name=w.lang.cap(w.name(ident, st)))
        if st.items_at.get(ident) in w.chars:
            return w.say("{who} has it.", who=w.name(st.items_at[ident], st))
        return ""

    def options(self, st: State) -> dict:
        """What can be done here, to remind a child who goes round in circles: the ways, the things,
        the characters, the bag, and a few things to try (never which one wins)."""
        w = self.w
        here = self.describe(st)
        tries = [w.say("open {thing}", thing=w.name(i, st)) for i in w.visible_items(st)
                 if w.items[i].get("openable") and not w.prop(st, i, "open")]  # fmt: skip
        for a in w.rules:
            if (a.get("place") and a["place"] != st.place) or w.scripts.risky(a) or w.scripts.red_herring(a):
                continue
            if any(x and x not in w.scope(st) for x in (a.get("object"), a.get("target"))):
                continue
            if w.scripts.achieved(a, st) or (a["id"] in st.done_actions and a["id"] not in w.idempotent_ids):
                continue
            verb = w.lang.verb(a.get("verb", ""), "")
            obj = w.name(a.get("object", ""), st) if a.get("object") else ""
            if verb and obj:
                tries.append(w.say("{verb} {thing}", verb=verb, thing=obj))
        return {
            "exits": [f"{x['to_label']} ({x['direction']})" for x in here["exits"] if "closed" not in x],
            "here": here["here"], "people": here["people"],
            "bag": [w.name(i, st) for i in st.inventory],
            "ideas": list(dict.fromkeys(tries))[:3],
        }  # fmt: skip
