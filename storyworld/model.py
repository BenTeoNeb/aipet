"""The world model: versions, kinds, normalized things, the state of a game."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

# Kinds of things (Inform's kinds): a thing is taken, the scenery stays, a container holds things and
# opens, a door closes an exit (on both sides).
KINDS = ("thing", "scenery", "container", "door")


# ---- versions -----------------------------------------------------------------------------------
FORMAT = 2  # the format this library reads (see SPEC.md)


def format_of(graph: dict) -> int:
    """The version a graph says it is (`format`, or the older `model`); 1 when it says nothing."""
    v = graph.get("format", graph.get("model", 1))
    return v if isinstance(v, int) else 1


def migrate(graph: dict) -> dict:
    """A graph in the current format: the same object when it already is, else a migrated copy.

    Version 1 → 2: each action's `verb`/`object`/`target` becomes its only trigger; items' booleans
    (`takeable`, `container`, `openable`) become a `kind`; states written as a map with `state`
    become a list (the starting one first); `model` becomes `format`. Whole-description `variants`
    of places are kept (deprecated: read by the engine, to be rewritten as `details`)."""
    if format_of(graph) == FORMAT and "format" in graph and all("triggers" in a for a in graph.get("actions", [])):
        return graph
    g = copy.deepcopy(graph)
    g.pop("model", None)
    g["format"] = FORMAT
    if "source" in g:  # an application's field written before extensions were `x-` fields
        g["x-source"] = g.pop("source")
    for a in g.get("actions", []):
        if "triggers" not in a:
            a["triggers"] = [{k: a.pop(k) for k in ("verb", "object", "target") if k in a}]
    for i in g.get("items", []):
        if "kind" not in i:
            i["kind"] = kind_of(i)
        for k in ("takeable", "container", "openable"):
            i.pop(k, None)
    for t in [*g.get("items", []), *g.get("characters", [])]:
        states = t.get("states")
        if isinstance(states, dict):
            first = t.pop("state", "") or next(iter(states), "")
            t["states"] = [{"id": k, **v} for k, v in sorted(states.items(), key=lambda kv: kv[0] != first)]
    return g


upgrade = migrate  # the former name


def kind_of(item: dict) -> str:
    """An item's kind; a version 1 item (or one written with booleans) has it from its booleans."""
    if item.get("kind") in KINDS:
        return item["kind"]
    if item.get("container"):
        return "container"
    if item.get("openable"):
        return "door"
    return "thing" if item.get("takeable", True) else "scenery"


def _normalized(thing: dict, is_item: bool) -> dict:
    """A thing as the engine reads it (a copy): the kind's properties (takeable, openable, container),
    and its states as a map with the first one as the starting state. The graph itself stays as written."""
    t = copy.deepcopy(thing)
    if is_item:
        kind = t["kind"] = kind_of(t)
        t["takeable"] = kind == "thing"
        t["openable"] = kind in ("container", "door")
        t["container"] = kind == "container"
        # A code or a riddle locks it until it is found (no key).
        t["locked"] = bool(t.get("locked") or (t["openable"] and (t.get("code") or t.get("answer"))))
    states = t.get("states")
    if isinstance(states, list):  # model 2: [{id, name, …}], the first is the start
        t["states"] = {s.get("id", ""): {k: v for k, v in s.items() if k != "id"} for s in states}
        t["state"] = states[0].get("id", "") if states else ""
    return t


def _all_conditions(graph: dict) -> list[str]:
    """Every condition of a graph (actions, exits, details, situated lines)."""
    out = [r for a in graph.get("actions", []) for r in a.get("when") or []]
    out += [r for p in graph.get("places", []) for x in p.get("exits", []) for r in x.get("when") or []]
    for t in [*graph.get("places", []), *graph.get("items", []), *graph.get("characters", [])]:
        out += [r for d in t.get("details") or [] for r in d.get("when") or []]
    out += [r for i in graph.get("items", []) if isinstance(i.get("light"), list) for r in i["light"]]
    return [r for r in out if isinstance(r, str)]


def exit_rules(x: dict) -> list[str]:
    """What must hold to go through an exit: its conditions, and its door open."""
    return [*x.get("when", []), *([f"open:{x['door']}"] if x.get("door") else [])]


# ---- conditions and effects ---------------------------------------------------------------------


@dataclass
class State:
    place: str
    inventory: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    items_at: dict[str, str] = field(default_factory=dict)  # item → place id, "" (nowhere) or "gone"
    chars_at: dict[str, str] = field(default_factory=dict)
    visited: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)  # places seen lit (a dark place visited blind is not)
    known: list[str] = field(default_factory=list)  # clues met (`puzzles[].clue_in` of codes and riddles)
    tried: list[str] = field(default_factory=list)  # the things of the last tries that led nowhere (for hints)
    ending: str | None = None
    turns: int = 0
    stalled: int = 0  # turns without progress (for the reminders and hints)
    reminded: bool = False  # a reminder of what can be done was given since the last progress
    warned: list[str] = field(default_factory=list)  # losing actions already announced once
    hint_level: int = 0
    score: int = 0
    notes: dict[str, list[str]] = field(default_factory=dict)  # invented details to keep (per place)
    done_actions: list[str] = field(default_factory=list)
    # Things' properties changed since the start (open, locked, state); the others are as written
    # (`World.prop`). Items picked up at least once lose their initial appearance (Inform's « handled »).
    props: dict[str, dict] = field(default_factory=dict)
    handled: list[str] = field(default_factory=list)
    # The adventure can't be won any more (a needed thing destroyed): {lost, text, before (the state
    # just before)}. It ends when the child runs into it, not when it happens.
    doomed: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> State:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "seen" not in known:  # saved before darkness: every visited place was seen
            known["seen"] = list(known.get("visited") or [])
        return cls(**copy.deepcopy(known))

    def key(self) -> tuple:
        """What matters for the solver (not turns, score or notes)."""
        return (
            self.place,
            frozenset(self.inventory),
            frozenset(self.flags),
            tuple(sorted(self.items_at.items())),
            tuple(sorted(self.chars_at.items())),
            self.ending,
            tuple(sorted((k, tuple(sorted(v.items()))) for k, v in self.props.items())),
        )


# ---- fields ----
STATE_FIELDS = {"id", "name", "aliases", "description", "initial", "says"}
SITUATED = ("says", "initial")  # a text, or [{when, text}]: the first that holds
TEXT_FIELDS = {"open", "locked", "unlock", "closed", "empty"}
SENSE_FIELDS = {"listen", "smell", "touch"}
CODE_FIELDS = {"press", "right", "wrong", "done", "hint"}
ANSWER_FIELDS = {"question", "accept", "right", "wrong", "hint", "effects"}
