"""French text helpers shared by the memory and the games: accent-insensitive matching, slugs,
stopwords, rude words (French on purpose: data matched against French speech)."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def without_article(s: str) -> str:
    return re.sub(r"^(les|le|la|l'|un|une|des|du|de la|de) ", "", s.strip())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_text(s)).strip("-") or "untitled"


# French stopwords ignored by the keyword search.
STOPWORDS = set(
    """les des une est que qui quoi dans pour avec sur pas plus son ses mon mes ton tes
notre votre leur leurs elle ils elles nous vous tout tous toute cette ces comme mais donc car
etait avait fait faire dit aux par ont sont quand comment pourquoi rappelle souviens souvenir
toi moi lui deja encore aussi tres bien chez""".split()
)

# The pet's own life (on-disk data, French): what the children chose for it, the words they taught
# him, the little adventures he tells about himself, and the jokes he shares with each child.
# Always injected into the prompt, so the pet stays consistent and keeps growing with the family.
# Never kept in the pet's life file (words taught, inside jokes): rude words and put-downs a child might
# teach the pet to repeat to another child. French on purpose (data matched against French speech).
RUDE = re.compile(
    r"\b(merde|putain|con|conne|connard|connasse|salaud|salope|enfoir\w*|encul\w*|bite|chier|idiot\w*|"
    r"debile|cretin\w*|imbecile|stupide|nul|nulle|moche|bebe|pue|puent|gros\s+lard|tais[- ]toi)\b"
)
GENERIC_NAMES = {"un enfant", "enfant", "inconnu", "inconnue", "famille", "quelqu'un", "personne", "?"}


def is_rude(text: str) -> bool:
    return bool(RUDE.search(normalize_text(text)))
