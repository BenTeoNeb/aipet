"""« Ma vie » moments: the pet's life only grows when a child chooses something for it or feeds its
collection, and left to itself the model never finds the moment (none in the first three days).
Once per conversation, at a calm moment, the code suggests one such question in the message label.
"""

from __future__ import annotations

import re

from aipet.memory.store import LIFE_CHOSEN, LIFE_WORDS, normalize_text

MIN_CHILD_MESSAGES = 3  # not right away: first the child says what it came for
# Words that don't tell two undecided things apart (« ta couleur préférée » / « ton jeu préféré »).
GENERIC = {"ton", "ta", "tes", "le", "la", "les", "de", "du", "nom", "prefer", "ce", "que", "tu", "fera",
           "en", "dans", "sous", "premier", "pour", "toi"}  # fmt: skip


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", normalize_text(text))
    return {w.rstrip("es") or w for w in words} - GENERIC


def still_undecided(undecided: tuple[str, ...] | list[str], chosen: list[str]) -> list[str]:
    """The undecided items not yet in « Ce que les enfants ont choisi pour moi » (lines « trait : value »)."""
    decided = [_stems(line.split(" : ", 1)[0]) for line in chosen]
    out = []
    for item in undecided:
        own = _stems(item)
        if not any(own and 2 * len(own & d) >= len(own) for d in decided):
            out.append(item)
    return out


def life_question(child: str, undecided: list[str], collection_ask: str, life: dict[str, list[str]]) -> str:
    """The French hint, or "" when there is nothing to ask. Choices and collection take turns."""
    chosen, words = life.get(LIFE_CHOSEN, []), life.get(LIFE_WORDS, [])
    left = still_undecided(undecided, chosen)
    intro = (
        "moment « ma vie » (seulement si l'enfant est de bonne humeur et que ça vient naturellement ; sinon, ignore)"
    )
    if left and (len(chosen) <= len(words) or not collection_ask):
        return f"{intro} : demande à {child} de choisir pour toi {left[0]}, ce n'est pas encore décidé"
    if collection_ask:
        return f"{intro} : demande à {child} {collection_ask}"
    return ""
