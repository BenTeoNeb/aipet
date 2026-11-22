"""Difficulty profiles: an application's policy (sizes, hints, retries), not part of the format.
The checker applies one when asked (`check(graph)` uses the graph's `difficulty`)."""

from __future__ import annotations

# ---- difficulty (inherent to each adventure, chosen when it is created) ------------------------
DIFFICULTIES: dict[str, dict] = {
    # places, steps of the shortest win (moves and pickups count), lose endings, tries that lead nowhere
    # before a reminder of what can be done (then hints), retries from just before a loss
    # (« sauvegardes »; None = unlimited)
    "facile": {
        "places": (3, 6),
        "wins": (1, 3),
        "steps": (4, 14),
        "puzzles": (2, 6),
        "loses": (0, 1),
        "hint_after": 3,
        "checkpoints": None,
    },
    "moyen": {
        "places": (5, 8),
        "wins": (1, 3),
        "steps": (8, 24),
        "puzzles": (4, 9),
        "loses": (1, 2),
        "hint_after": 4,
        "checkpoints": 3,
    },
    "difficile": {
        "places": (7, 11),
        "wins": (1, 3),
        "steps": (12, 40),
        "puzzles": (6, 14),
        "loses": (1, 3),
        "hint_after": 6,
        "checkpoints": 1,
    },
}
WINS = (1, 3)  # ways to win an adventure
