"""Languages: the words, the narrator's notes, the messages and the outcome keys of one language
(French first, `storyworld.locale.fr`; English is the source language, `storyworld.locale.en`).

Messages work like gettext: the engine writes its English source text as the message id
(`lang.say("{name} is already open.", name=…)`), a language maps it to its own template with the same
named placeholders, and a missing translation falls back to the English."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

IDS = {"in_reach"}  # maps keyed by ids: left as they are


def localize(value: Any, keys: dict[str, str]) -> Any:
    """An outcome with its keys in a language (« place » → « lieu »), at every depth."""
    if isinstance(value, dict):
        return {keys.get(k, k): (v if k in IDS else localize(v, keys)) for k, v in value.items()}
    if isinstance(value, list):
        return [localize(v, keys) for v in value]
    return value


def cap(s: str) -> str:
    """The first letter in upper case (« le coffre » → « Le coffre »)."""
    return s[:1].upper() + s[1:]


def _same(s: str) -> str:
    return s


def _never(s: str) -> bool:
    return False


def _plain(s: str) -> str:
    """Lower case, without accents or punctuation (the default normalizer)."""
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("œ", "oe").replace("æ", "ae"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def _plain_stems(s: str) -> set[str]:
    return {w for w in _plain(s).split() if len(w) > 2}


@dataclass(frozen=True, eq=False)
class Language:
    """One language of the engine: its messages (English template → its template), the words for the
    verbs, the notes joined to answers for the narrator, the outcome keys, and a few grammar helpers
    the messages need (a name after « of », a name without its article, a plural name)."""

    code: str
    messages: dict[str, str] = field(default_factory=dict)
    verbs: dict[str, list[str]] = field(default_factory=dict)  # canonical verb → the words for it
    verb_words: dict[str, str] = field(default_factory=dict)  # canonical verb → how a hint says it
    keys: dict[str, str] = field(default_factory=dict)  # outcome keys as the narrator reads them
    # Notes for the narrator (a language model): what the engine cannot know is left to it.
    warning: str = ""
    truth_note: str = ""
    free_note: str = ""
    absent_note: str = ""
    destroy_note: str = ""
    dark_note: str = ""
    think_twice_note: str = ""
    improvise_note: str = ""
    improvise_no_note: str = ""
    # Words: how the player's words are compared with names (no article, no accent), the words that
    # name a thing (« des poissons » shows « un poisson »), the words that name a way, a verb's
    # canonical form, the hero's bag, the words for « just leave », what says « locked » in a text.
    norm: Callable[[str], str] = _plain
    norm_keep: Callable[[str], str] = _plain
    stems: Callable[[str], AbstractSet[str]] = _plain_stems  # frozen once memoised
    way: Callable[[str], AbstractSet[str]] = _plain_stems
    canonical_verb: Callable[[str], str] = _same
    bag: frozenset[str] = frozenset()
    leave_words: frozenset[str] = frozenset()
    locked_words: re.Pattern | None = None
    of: Callable[[str], str] = _same  # a name after « of » (« du jardin »)
    bare: Callable[[str], str] = _same  # a name without its article (« des cailloux » → « cailloux »)
    plural: Callable[[str], bool] = _never  # does the name speak of several things?

    def __post_init__(self) -> None:
        """The word helpers are pure: each string is normalized once (the same names are compared at
        every turn and on every edge the checker replays). Sets come back frozen: never changed."""
        for name in ("norm", "norm_keep", "canonical_verb"):
            object.__setattr__(self, name, lru_cache(maxsize=16_384)(getattr(self, name)))
        for name in ("stems", "way"):
            raw = getattr(self, name)
            object.__setattr__(self, name, lru_cache(maxsize=16_384)(lambda s, raw=raw: frozenset(raw(s))))

    def say(self, msgid: str, **kw: Any) -> str:
        """The message in this language (the English id when it has no translation), filled in."""
        return self.messages.get(msgid, msgid).format(**kw)

    def verb(self, verb: str, default: str | None = None) -> str:
        """How a hint says a canonical verb (« give » → « donner »)."""
        return self.verb_words.get(verb, verb if default is None else default)

    cap = staticmethod(cap)
