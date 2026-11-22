"""Conditions and effects: the format's mini-language, parsed once into typed rules.

A condition is `[!]kind:arg[:arg]` (« has:cle », « !is:bocal:plein »); an effect is `kind:arg[:arg]`
(« gain:cle », « set:pinces:content »). Each kind has a fixed arity and says what its arguments name
(an item, a character, a place, a state, an ending, or a free flag name), so one table serves the
parser, the checker and the documentation (see SPEC.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

# What an argument names: checked against the adventure by `storyworld.check`.
ITEM, OPENABLE, CHARACTER, PLACE, STATE, ENDING, FLAG, THING = (
    "item", "openable", "character", "place", "state", "ending", "flag", "thing",
)  # fmt: skip


@dataclass(frozen=True)
class Kind:
    """A kind of condition or effect: its arguments (what each one names) and, for effects, whether
    it keeps the world monotonic (what is done stays done)."""

    name: str
    args: tuple[str, ...]
    doc: str
    monotonic: bool = True


CONDITIONS: dict[str, Kind] = {k.name: k for k in (
    Kind("has", (ITEM,), "the item is in the hero's bag"),
    Kind("flag", (FLAG,), "the flag is set"),
    Kind("at", (PLACE,), "the hero is in the place"),
    Kind("visited", (PLACE,), "the hero has been in the place"),
    Kind("here", (CHARACTER,), "the character is where the hero is"),
    Kind("open", (OPENABLE,), "the container or door is open"),
    Kind("locked", (OPENABLE,), "the container or door is locked"),
    Kind("is", (THING, STATE), "the item or character is in that state"),
)}  # fmt: skip

EFFECTS: dict[str, Kind] = {k.name: k for k in (
    Kind("gain", (ITEM,), "the item goes into the bag"),
    Kind("lose", (ITEM,), "the item leaves the bag for good"),
    Kind("flag", (FLAG,), "set a flag"),
    Kind("reveal", (ITEM,), "the item appears here (or its holder lets it go)"),
    Kind("move", (CHARACTER, PLACE), "the character goes to the place (and stops following)"),
    Kind("follow", (CHARACTER,), "the character goes wherever the hero goes"),
    Kind("goto", (PLACE,), "the hero is taken to the place"),
    Kind("end", (ENDING,), "the adventure ends"),
    Kind("open", (OPENABLE,), "open (and unlock) a container or door"),
    Kind("unlock", (OPENABLE,), "unlock a container or door"),
    Kind("set", (THING, STATE), "put the item or character in that state"),
)}  # fmt: skip


@dataclass(frozen=True)
class Rule:
    """A parsed condition or effect. `str(rule)` gives the text back."""

    kind: str
    args: tuple[str, ...]
    negated: bool = False

    @property
    def arg(self) -> str:
        return self.args[0] if self.args else ""

    @property
    def last(self) -> str:
        return self.args[-1] if self.args else ""

    def __str__(self) -> str:
        return ("!" if self.negated else "") + ":".join((self.kind, *self.args))


class RuleError(ValueError):
    pass


@lru_cache(maxsize=4096)
def parse(text: str) -> Rule:
    """A rule from its text (any kind: `check` tells whether it is a known condition or effect)."""
    if not isinstance(text, str) or not text.strip():
        raise RuleError(f"{text!r} is not a rule")
    negated = text.startswith("!")
    kind, *args = text.lstrip("!").split(":")
    return Rule(kind.strip(), tuple(a.strip() for a in args), negated)


def parse_all(texts: list[str] | None) -> list[Rule]:
    """Rules from texts; a malformed one is skipped here (the checker reports it)."""
    out = []
    for t in texts or []:
        try:
            out.append(parse(t))
        except RuleError:
            continue
    return out


def of_kind(texts: list[str] | None, *kinds: str, negated: bool = False) -> list[Rule]:
    """The rules of these kinds (not negated, unless asked)."""
    return [r for r in parse_all(texts) if r.kind in kinds and r.negated == negated]


def args_of(texts: list[str] | None, *kinds: str, negated: bool = False) -> set[str]:
    """The first arguments of the rules of these kinds: « has:cle » → {"cle"}."""
    return {r.arg for r in of_kind(texts, *kinds, negated=negated)}


def problem(text: str, table: dict[str, Kind]) -> str:
    """Why a rule is not well formed for this table ("" when it is)."""
    try:
        r = parse(text)
    except RuleError as e:
        return str(e)
    kind = table.get(r.kind)
    if kind is None:
        return f"{r.kind!r} is not one of {sorted(table)}"
    if table is EFFECTS and r.negated:
        return "an effect cannot be negated"
    if len(r.args) != len(kind.args) or not all(r.args):
        return f"{r.kind} takes {len(kind.args)} argument(s): {':'.join((r.kind, *kind.args))}"
    return ""
