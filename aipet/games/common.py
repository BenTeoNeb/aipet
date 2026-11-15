"""Helpers shared by the pet's games: who is playing, their school level, streaks of days."""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from pathlib import Path

from aipet import character
from aipet.fileio import atomic_write_json, load_json, lock_for
from aipet.memory.text import normalize_text, without_article
from aipet.memory.tools import TOOLS_EXHAUSTED
from aipet.session_log import NO_LOG

UNKNOWN_PLAYER = "famille"  # games played while the pet doesn't know (for sure) who is talking
GAMES_DIR = "jeux"  # in the family memory (data)


class Host:
    """What a game may use of the conversation it runs in (conversation.Conversation implements it). These
    defaults are a game played on its own (tests, tools): nobody known for sure, no tool budget,
    no system prompt to refresh."""

    who = None  # memory.speakers.Speakers
    rhythm = None  # rhythm.Rhythm
    pet_name = "Toi"

    def player(self) -> str:
        """The child talking, if known for sure; else the family."""
        who = self.who
        if who and who.current and who.certainty == "sure":
            return who.current
        return UNKNOWN_PLAYER

    def present(self) -> list[str]:
        return list(self.who.present) if self.who else []

    def real_names(self) -> set[str]:
        """Children met in this conversation (beyond the family cards)."""
        return {n for n in [self.who.current, *self.who.present] if n} if self.who else set()

    def last_pet_line(self) -> str:
        """What the pet said just before the child's last message."""
        return ""

    def tool_budget_exhausted(self) -> bool:
        return False

    def refresh_prompt_soon(self):
        """The game's prompt section changed: send the model the new system prompt."""


class Activity:
    """A game whose state is kept by code, run during an activity (see its SKILL.md). The pipeline
    builds them all (`aipet.games.make_games`) and the conversation drives them through this
    interface only."""

    tools: frozenset[str] = frozenset()  # the tool names it registers

    def __init__(self, host: Host | None = None, slog=None):
        self.host = host or Host()
        self.slog = slog or NO_LOG

    def register_tools(self, llm) -> list:
        """Register its tools with the LLM; return their FunctionSchema."""
        raise NotImplementedError

    def on_user_message(self, text: str) -> str | None:
        """Each new child message: a short verdict for the pet (refereed by code), or None."""
        return None

    def prompt_section(self) -> str:
        """Its part of the system prompt while it runs (French), or ""."""
        return ""

    def stop(self):
        """The activity ends (finished, replaced, good night…): forget the running round."""

    def budget_refused(self) -> dict | None:
        """For its tools: the answer to give when this turn already used too many tools."""
        return TOOLS_EXHAUSTED if self.host.tool_budget_exhausted() else None


def theme_key(theme: str) -> str:
    """Same theme whatever the wording: « Les animaux » = « animaux », « l'espace » = « espace »."""
    t = normalize_text(theme).strip()
    t = re.sub(r"^(les |le |la |l'|l’|un |une |des |du |de la |de l'|d'|d’)", "", t)
    return without_article(t).strip()


def days_in_a_row(play_days: list[str], today: date) -> int:
    """Consecutive days played, ending today or yesterday."""
    days = {date.fromisoformat(d) for d in play_days}
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


class JsonStore:
    """A small JSON file in the family memory, written atomically."""

    def __init__(self, path: Path, empty: dict):
        self.path, self._empty = Path(path), empty
        self.lock = lock_for(self.path)  # shared with the parents' corrections

    def load(self) -> dict:
        data = load_json(self.path, self._empty)
        return data if isinstance(data, dict) else copy.deepcopy(self._empty)

    def save(self, data: dict):
        atomic_write_json(self.path, data)


def pet_intro(cfg: dict) -> str:
    """How preparation prompts name the pet (French): « Flocon, un petit chat »."""
    pet = character.of(cfg)
    return f"{pet.name}, {pet.intro}"


# ---- background preparations ------------------------------------------------------------------
# Preparing a story, a quiz, a lesson or a yes/no game takes a model call (seconds). Those tools are
# asynchronous (registered with cancel_on_interruption=False): a child speaking meanwhile no longer
# cancels the preparation, the pet answers them, and the result arrives afterwards. A preparation
# whose activity was stopped or restarted in the meantime must not start anything.
PREPARING_NOTE = (
    " La préparation prend quelques secondes : si l'enfant parle pendant ce temps, réponds-lui en une "
    "phrase (tu peux retenir son idée) sans commencer l'activité ; le résultat arrivera ensuite."
)
STALE_PREPARATION = {"cancelled": True, "note": "Cette activité a été arrêtée ou remplacée pendant sa "
                     "préparation : ne la commence pas, continue la conversation."}  # fmt: skip


def new_preparation(game) -> int:
    """Start a preparation: any earlier one of this game becomes stale. Returns its number."""
    game._preparation = getattr(game, "_preparation", 0) + 1
    return game._preparation


def cancel_preparation(game):
    """The activity stops: a preparation still running must not start it."""
    game._preparation = getattr(game, "_preparation", 0) + 1


def is_stale(game, number: int) -> bool:
    return getattr(game, "_preparation", 0) != number
