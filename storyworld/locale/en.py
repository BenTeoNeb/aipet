"""English: the engine's source language. Its messages are the message ids themselves (an identity
catalog); it only provides the verbs' words, the narrator's notes and a few grammar helpers."""

from __future__ import annotations

import re

from storyworld.locale import Language

MESSAGES: dict[str, str] = {}  # the English templates are the ids: nothing to translate

# Spoken verbs for the strongest hint (« Try: give the seaweed, with Pinces. »).
VERB_WORDS = {"talk": "talk to", "ask": "ask", "climb": "climb on", "swim": "swim in", "sit": "sit on",
              "sing": "sing for", "dance": "dance with", "touch": "stroke", "search": "search",
              "examine": "look at", "wear": "put on"}  # fmt: skip

WARNING = "Careful, this could end very badly for your mission… Are you sure you want to do it?"
TRUTH_NOTE = (
    "“to_know” is true in the adventure: slip it into your story, in your own words (“Looks like it's locked… "
    "we'd have to find a way to open it!”), without giving the solution away."
)
FREE_NOTE = (
    "Not planned in the adventure, and it changes nothing in the game. If it is possible in this world, play along: "
    "tell it in one or two fun sentences, then come back to the situation (“Now that's done, what do you do?”). "
    "If it is impossible here, say so with a reason from the world. If you don't understand what they want to do, "
    "ask them to say more."
)
ABSENT_NOTE = (
    "Not in the adventure. If it is plausible here (pebbles among rocks, a stick in a wood, their cuddly toy), "
    "play along: they can find some and have fun with it, but it won't help solve the adventure (nothing is "
    "gained or opened). If it isn't plausible here, say so kindly; if it's odd, ask what they mean. The place's "
    "“decor” gives ideas, you can imagine others."
)
DESTROY_NOTE = (
    "It's done, for good: tell it. If it stops the mission from being finished, the child will find out later "
    "(don't say so now)."
)

DARK_NOTE = (
    "It is dark here: the hero sees nothing but can feel the ways out. Describe only what they hear, smell or "
    "feel (“senses”), never a thing or a person (none is in reach): they need a light to see."
)


def of(name: str) -> str:
    """A name after « of »: English needs no contraction (« near the lighthouse »)."""
    return name


def bare(name: str) -> str:
    """The name without its article: « some pebbles » → « pebbles »."""
    return re.sub(r"^(the|some|a|an)\s+", "", name, flags=re.I)


def plural(name: str) -> bool:
    """« some fireflies », « the pebbles »: several things."""
    words = name.lower().split()
    return bool(words) and (words[0] == "some" or (len(words) > 1 and words[-1].endswith("s")))


ENGLISH = Language(
    code="en",
    messages=MESSAGES,
    verb_words=VERB_WORDS,
    keys={},  # the outcome keys are English already
    warning=WARNING,
    truth_note=TRUTH_NOTE,
    free_note=FREE_NOTE,
    absent_note=ABSENT_NOTE,
    destroy_note=DESTROY_NOTE,
    dark_note=DARK_NOTE,
    of=of,
    bare=bare,
    plural=plural,
)
