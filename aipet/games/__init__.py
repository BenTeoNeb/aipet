"""The pet's games whose state is kept by code (quiz, ni oui ni non, devinettes, stories, lessons).

Each one is an `Activity` (see common.py); adding a game means adding it to `make_games`.
"""

from __future__ import annotations

from pathlib import Path

from aipet.games.common import Activity, Host


def make_games(cfg: dict, memory_root: Path, host: Host, slog=None) -> list[Activity]:
    from aipet.games import guess, learn, quiz, story, yes_no

    return [
        quiz.Quiz(cfg, memory_root, host, slog=slog),
        yes_no.YesNoGame(cfg, memory_root, host, slog=slog),
        guess.GuessGame(
            memory_root, host, slog=slog, max_questions=cfg.get("games", {}).get("guess_max_questions", 20)
        ),
        story.StoryGame(cfg, memory_root, host, slog=slog),
        learn.LearnGame(cfg, memory_root, host, slog=slog),
    ]
