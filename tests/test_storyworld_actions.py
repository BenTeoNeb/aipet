"""The precedence table of `storyworld.actions`: verb tiers and things ranks, on a tiny adventure."""

import copy

from aipet.games.adventure_patterns import PATTERNS
from storyworld import World
from storyworld.actions import (
    THINGS_PARTIAL,
    THINGS_PLUS_TOOL,
    THINGS_SAME,
    VERB_EXACT,
    VERB_NEAR,
)


def _trade() -> tuple[World, dict]:
    w = World(copy.deepcopy(PATTERNS["trade_with_a_holder"][0]))
    return w, next(r for r in w.rules if r["id"] == "troc")


def test_exact_verb_and_same_things():
    w, troc = _trade()
    st = w.start()
    m = w.scripts.match(troc, "give", "pomme", "ane", st)
    assert m and (m.verb, m.things) == (VERB_EXACT, THINGS_SAME)


def test_a_verb_of_the_same_class_is_near():
    w, troc = _trade()
    m = w.scripts.match(troc, "throw", "ane", "pomme", w.start())  # any order
    assert m and (m.verb, m.things) == (VERB_NEAR, THINGS_SAME)


def test_the_things_remap_another_verb_but_never_a_blow():
    w, troc = _trade()
    st = w.start()
    m = w.scripts.match(troc, "show", "pomme", "ane", st)  # TADS' remap: the things say it
    assert m and (m.verb, m.things) == (VERB_NEAR, THINGS_SAME)
    assert w.scripts.match(troc, "hit", "pomme", "ane", st) is None  # NO_REMAP


def test_a_held_tool_more_is_plus_tool():
    w, _ = _trade()
    fin = next(r for r in w.rules if r["id"] == "fin")
    st = w.start()
    st.inventory += ["cle", "pomme"]
    m = w.scripts.match(fin, "use", "cle", "pomme", st)
    assert m and (m.verb, m.things) == (VERB_EXACT, THINGS_PLUS_TOOL)


def test_half_the_things_and_another_verb_is_too_far():
    w, troc = _trade()
    st = w.start()
    assert w.scripts.match(troc, "throw", "pomme", None, st) is None
    m = w.scripts.match(troc, "give", "pomme", None, st)
    assert m and m.things == THINGS_PARTIAL
