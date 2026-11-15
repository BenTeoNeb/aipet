"""Helpers shared by the pet's games: who is playing, their school level, streaks of days."""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from aipet import character
from aipet.fileio import atomic_write_json, load_json, lock_for
from aipet.memory.cards import DIR_GAMES
from aipet.memory.text import normalize_text, without_article
from aipet.session_log import NO_LOG
from aipet.tool_gate import tool_handler

if TYPE_CHECKING:
    from aipet.memory.speakers import Speakers
    from aipet.rhythm import Rhythm

UNKNOWN_PLAYER = "famille"  # games played while the pet doesn't know (for sure) who is talking
GAMES_DIR = DIR_GAMES  # in the family memory (data)


class Host:
    """What a game may use of the conversation it runs in (conversation.Conversation implements it). These
    defaults are a game played on its own (tests, tools): nobody known for sure, no tool budget,
    no system prompt to refresh."""

    who: Speakers | None = None

    @property
    def pet_name(self) -> str:
        return "Toi"

    @property
    def rhythm(self) -> Rhythm | None:
        return None

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
        self._preparation = 0  # see prepare()

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

    def tool(self, call: Callable[[dict], Any], *, budget: bool = False) -> Callable:
        """A tool handler calling `call(arguments)`; with `budget`, the call counts against the
        per-turn tool budget (for tools that start something or search)."""
        return tool_handler(call, self.host.tool_budget_exhausted if budget else None)

    async def prepare(self, make: Callable[..., Any], *args, failed: str) -> tuple[Any, dict | None]:
        """Run a slow preparation (a model call) off the event loop. Returns (result, None), or
        (None, answer for the model) when it failed — `failed` is that answer, French — or when the
        activity was stopped or restarted meanwhile (see PREPARING_NOTE)."""
        self._preparation += 1
        number = self._preparation
        try:
            result = await asyncio.to_thread(make, *args)
        except Exception:
            logger.exception(f"{type(self).__name__}: preparation failed")
            return None, {"error": failed}
        if self._preparation != number:
            return None, STALE_PREPARATION
        return result, None

    def cancel_preparation(self):
        """The activity stops: a preparation still running must not start it."""
        self._preparation += 1


def theme_key(theme: str) -> str:
    """Same theme whatever the wording: « Les animaux » = « animaux », « l'espace » = « espace »."""
    t = normalize_text(theme).strip()
    t = re.sub(r"^(les |le |la |l'|l’|un |une |des |du |de la |de l'|d'|d’)", "", t)
    return without_article(t).strip()


MIN_DIFFICULTY, MAX_DIFFICULTY = 1, 5


def adjust_difficulty(before: int, up: bool, down: bool) -> int:
    """One step up after a game well played, one step down after a hard one, within 1..5."""
    if up:
        return min(MAX_DIFFICULTY, before + 1)
    if down:
        return max(MIN_DIFFICULTY, before - 1)
    return before


def level_change(before: int, after: int) -> str | None:
    return "up" if after > before else "down" if after < before else None


def mark_played(player_data: dict, day: date) -> None:
    """Remember the day a child played (for « days in a row »)."""
    days = player_data.setdefault("play_days", [])
    if day.isoformat() not in days:
        days.append(day.isoformat())


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
