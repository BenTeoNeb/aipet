"""Forget requests said out loud (« oublie … »): what they match, and how text is cleaned."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from aipet.memory.text import STOPWORDS, normalize_text, slug

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ForgetRequest:
    """« Oublie … », said out loud. Deliberately cautious about what it matches:
    - `contains_all`: every important word of the request (« le foot » matches « aime le foot »);
    - `matches` (anywhere in the memory): every important word when there are at least two,
      otherwise only the exact phrase."""

    what: str
    who: str | None
    words: tuple[str, ...]
    at: float

    @classmethod
    def parse(cls, what: str, who: str | None = None) -> ForgetRequest:
        target = normalize_text(what).strip()
        words = tuple(w for w in re.findall(r"\w+", target) if w not in STOPWORDS and len(w) > 2)
        return cls(what.strip(), (who or "").strip() or None, words, time.monotonic())

    @property
    def too_vague(self) -> bool:  # « oublie Emma » alone: touch nothing
        return not self.words or (not self.who and len(self.words) < 2)

    @property
    def broad(self) -> bool:
        """Precise enough to remove sentences anywhere (two important words or more)."""
        return len(self.words) >= 2

    def contains_all(self, text: str) -> bool:
        n = normalize_text(text)
        return all(re.search(r"\b" + re.escape(w), n) for w in self.words)

    def in_phrase(self, text: str) -> bool:
        return bool(re.search(r"\b" + re.escape(normalize_text(self.what)) + r"\b", normalize_text(text)))

    def matches(self, text: str) -> bool:
        return self.contains_all(text) if self.broad else self.in_phrase(text)

    def everywhere(self, text: str) -> bool:
        """Sentences removed outside the person's card: every important word (two or more), or,
        for a request about someone, their word(s) together with that person's name."""
        if self.broad:
            return self.contains_all(text)
        if not self.who:
            return False
        name = normalize_text(self.who)
        return self.contains_all(text) and bool(re.search(r"\b" + re.escape(name) + r"\b", normalize_text(text)))

    @property
    def log_words(self) -> list[str]:
        """Words a log line must all contain to be removed (the name too for a one-word request)."""
        if self.broad or not self.who:
            return list(self.words)
        return [*self.words, *re.findall(r"\w+", normalize_text(self.who))]

    def about(self, name: str) -> bool:
        return self.who is not None and slug(self.who) == slug(name)


def scrub_sentences(text: str, match) -> tuple[str, int]:
    """Remove the matching sentences of a text (headings kept); returns (text, sentences removed)."""
    lines, removed = [], 0
    for line in text.splitlines():
        if line.startswith("#") or not match(line):
            lines.append(line)
            continue
        sentences = SENTENCE_END.split(line)
        kept = [x for x in sentences if not match(x)]
        removed += len(sentences) - len(kept)
        lines.append(" ".join(kept) if kept else "(effacé à la demande)")
    return "\n".join(lines).rstrip() + "\n", removed


def scrub_value(value, match):
    """Remove matching sentences / list items from model output (strings, lists, dicts)."""
    if isinstance(value, str):
        if not match(value):
            return value
        return " ".join(x for x in SENTENCE_END.split(value) if not match(x))
    if isinstance(value, list):
        return [scrub_value(v, match) for v in value if not (isinstance(v, str) and match(v))]
    if isinstance(value, dict):
        return {k: scrub_value(v, match) for k, v in value.items()}
    return value
