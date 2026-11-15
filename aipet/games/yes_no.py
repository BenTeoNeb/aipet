"""« Ni oui ni non », refereed by code.

- `yesno_start`: tricky questions are prepared up front (model call, adapted to the child and to
  their difficulty level, never repeating past questions); returns the rules reminder, the
  child's record and the first question.
- During the game, every child message is checked by code for a forbidden word (oui, ouais,
  non, nan…): no model call, so the game stays fast and the refereeing is fair. The result is
  added to the message label (« réponse tenue, 6 d'affilée » or « PERDU, il a dit « oui » »),
  and the pet reacts in the same reply.
- Per child: best run, games, days in a row, difficulty (up after a long run, down after a
  quick loss). Stored in the family memory as `jeux/ni_oui_ni_non.json`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from aipet import engines
from aipet.games.common import (
    GAMES_DIR,
    PREPARING_NOTE,
    UNKNOWN_PLAYER,
    Activity,
    JsonStore,
    adjust_difficulty,
    days_in_a_row,
    level_change,
    mark_played,
    pet_intro,
)
from aipet.memory.cards import child_level
from aipet.memory.text import normalize_text
from aipet.timefmt import now

YES_NO_TOOLS = {"yesno_start"}
TARGET = 15  # answers to hold to win the game
QUESTIONS_PREPARED = 20
MAX_EXCLUDED = 150
START_DIFFICULTY = 1
LEVEL_UP_AT, LEVEL_DOWN_BELOW = 10, 3  # answers held

# Forbidden words, as transcribed (accents already removed by normalize_text).
FORBIDDEN = re.compile(r"\b(oui+|ouais+|ouai|ouaip|ouep|non+|nan+|nope)\b")

# Model-facing (French).
DIFFICULTIES = {
    1: "questions simples et directes, posées tranquillement",
    2: "questions variées, dont certaines où on a très envie de répondre oui ou non",
    3: "questions surprises qui s'enchaînent vite, avec des « Tu es sûr ? »",
    4: "pièges : questions doubles, « Tu as dit quoi ? », fausses affirmations à corriger",
    5: "expert : pièges en cascade, questions sur l'enfant lui-même (« Tu t'appelles bien … ? »)",
}
GENERATOR_INSTRUCTIONS = """Tu prépares des questions pour le jeu du « ni oui ni non », joué à voix haute par
{pet}, avec un enfant : l'enfant doit répondre sans jamais dire « oui » ni « non ».
Règles :
- Des questions très courtes et rigolotes qui donnent envie de répondre oui ou non, adaptées à l'âge.
- Respecte la difficulté demandée. Varie les pièges ; rien de vexant, rien de gênant.
- N'utilise AUCUNE des questions déjà posées listées, ni une simple reformulation."""
GENERATOR_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}


def forbidden_word(text: str) -> str | None:
    """The forbidden word said in `text`, if any (« ouais » counts, « bien sûr » doesn't)."""
    m = FORBIDDEN.search(normalize_text(text))
    return m.group(1) if m else None


def generate_questions(
    cfg: dict, player: str, level: str | None, difficulty: int, exclude: list[str], n: int
) -> list[str]:
    who = f"{player}, en {level}" if level else (player if player != UNKNOWN_PLAYER else "un enfant de 5 à 10 ans")
    past = "\n".join(f"- {q}" for q in exclude) or "(aucune)"
    prompt = (
        f"Pour : {who}\nDifficulté : {difficulty} sur 5, {DIFFICULTIES[difficulty]}\nNombre de questions : {n}\n\n"
        f"Questions déjà posées :\n{past}"
    )
    res = engines.complete_json(
        cfg,
        GENERATOR_INSTRUCTIONS.format(pet=pet_intro(cfg)),
        prompt,
        GENERATOR_SCHEMA,
        role="preparation",
        activity="yes_no",
        timeout=30,
    )
    seen = {normalize_text(q).strip(" ?") for q in exclude}
    return [q.strip() for q in res.get("questions", []) if q.strip() and normalize_text(q).strip(" ?") not in seen][:n]


@dataclass
class Round:
    player: str
    difficulty: int
    questions: list[str]
    next_index: int = 1  # questions[0] is asked by yesno_start
    held: int = 0


class YesNoGame(Activity):
    tools = frozenset(YES_NO_TOOLS)

    def register_tools(self, llm) -> list:
        return register_tools(llm, self)

    def __init__(self, cfg: dict, memory_root, host=None, slog=None):
        super().__init__(host, slog)
        self.cfg = cfg
        self.store = JsonStore(memory_root / GAMES_DIR / "ni_oui_ni_non.json", {"players": {}, "asked": []})
        self.root = memory_root
        self.round: Round | None = None

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    def _player_data(self, data: dict, player: str) -> dict:
        return data["players"].setdefault(
            player, {"games": [], "best": 0, "play_days": [], "difficulty": START_DIFFICULTY}
        )

    def history(self, player: str) -> dict:
        p = self.store.load()["players"].get(player)
        if not p:
            return {"games_played": 0, "best": 0, "days_in_a_row": 0, "difficulty": START_DIFFICULTY}
        return {
            "games_played": len(p["games"]),
            "best": p["best"],
            "days_in_a_row": days_in_a_row(p["play_days"], now().date()),
            "difficulty": p.get("difficulty", START_DIFFICULTY),
        }

    async def start(self) -> dict:
        if self.round:
            self.stop()  # a new game replaces an unfinished one
        player = self.host.player()
        hist = self.history(player)
        level = child_level(self.root, player, now().date()) if player != UNKNOWN_PLAYER else None
        asked = self.store.load().get("asked", [])[-MAX_EXCLUDED:]
        questions, refused = await self.prepare(
            generate_questions, self.cfg, player, level, hist["difficulty"], asked, QUESTIONS_PREPARED,
            failed="Le jeu n'a pas pu être préparé. Propose un autre jeu ou réessaie plus tard.",
        )  # fmt: skip
        if refused:
            return refused
        if len(questions) < 5:
            return {"error": "Pas assez de nouvelles questions : propose un autre jeu."}
        self.round = Round(player, hist["difficulty"], questions)
        logger.info(f"🙊 Ni oui ni non for {player} (difficulty {hist['difficulty']}): {len(questions)} questions")
        self._ev("yesno_start", player=player, difficulty=hist["difficulty"])
        return {
            "player": player,
            "record": hist["best"],
            "games_played": hist["games_played"],
            "days_in_a_row": hist["days_in_a_row"],
            "difficulty": f"niveau {hist['difficulty']} sur 5 ({DIFFICULTIES[hist['difficulty']]})",
            "target": TARGET,
            "first_question": questions[0],
            "note": (
                f"Rappelle la règle en une phrase (et son record s'il en a un), puis pose la première question. "
                f"Ensuite, c'est le système qui arbitre : l'étiquette de chaque message te dira si l'enfant a tenu "
                f"ou perdu, et te proposera la question suivante. S'il tient {TARGET} réponses, il a gagné."
            ),
        }

    def on_user_message(self, text: str) -> str | None:
        """Referee one child answer; return the French hint for the message label (or None)."""
        r = self.round
        if not r:
            return None
        word = forbidden_word(text)
        if word:
            held = r.held
            rec = self._finish(lost_on=word)
            plural = "s" if held != 1 else ""
            record = "NOUVEAU RECORD !" if rec["new_record"] else f"record : {rec['best']}"
            level = f", il monte au niveau {rec['difficulty']}" if rec["level_change"] == "up" else ""
            return (
                f"ni oui ni non : PERDU, l'enfant a dit « {word} » après {held} réponse{plural} tenue{plural} "
                f"({record}{level}). Annonce-le en riant, félicite-le, puis propose une revanche "
                "(appelle yesno_start s'il veut)"
            )
        r.held += 1
        if r.held >= TARGET:
            rec = self._finish(lost_on=None)
            return (
                f"ni oui ni non : GAGNÉ ! L'enfant a tenu {r.held} réponses sans dire oui ni non "
                f"({'NOUVEAU RECORD !' if rec['new_record'] else 'record égalé ou déjà plus haut'}"
                f"{', il monte au niveau ' + str(rec['difficulty']) if rec['level_change'] == 'up' else ''}). "
                "Fête-le avec enthousiasme et propose une revanche ou un autre jeu"
            )
        if r.next_index >= len(r.questions):
            nxt = "invente une nouvelle question piège"
        else:
            nxt = f"question suivante proposée : « {r.questions[r.next_index]} »"
            r.next_index += 1
        best = self.history(r.player)["best"]
        return f"ni oui ni non : réponse tenue ({r.held} d'affilée, record {best}) ; {nxt}"

    def stop(self):
        """The game ends without a winner or loser (activity ended, sleep…): keep its result."""
        self.cancel_preparation()
        if self.round and self.round.held:
            self._finish(lost_on=None, stopped=True)
        self.round = None

    def _finish(self, lost_on: str | None, stopped: bool = False) -> dict:
        r, when = self.round, now()
        assert r is not None  # only called during a round
        with self.store.lock:
            data = self.store.load()
            p = self._player_data(data, r.player)
            previous_best = p["best"]
            p["games"].append({"date": when.isoformat(timespec="minutes"), "held": r.held, "lost_on": lost_on,
                               "stopped": stopped, "difficulty": r.difficulty})  # fmt: skip
            p["best"] = max(previous_best, r.held)
            before = p.get("difficulty", START_DIFFICULTY)
            p["difficulty"] = adjust_difficulty(
                before, up=r.held >= LEVEL_UP_AT, down=bool(lost_on) and r.held < LEVEL_DOWN_BELOW
            )
            mark_played(p, when.date())
            data["asked"] = (data.get("asked", []) + r.questions[: r.next_index])[-MAX_EXCLUDED:]
            self.store.save(data)
        self.round = None
        self._ev("yesno_end", player=r.player, held=r.held, lost_on=lost_on, stopped=stopped)
        after = p["difficulty"]
        return {
            "new_record": r.held > previous_best and previous_best > 0,
            "best": p["best"],
            "difficulty": after,
            "level_change": level_change(before, after),
        }


def register_tools(llm, game: YesNoGame) -> list:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    start = game.tool(lambda a: game.start(), budget=True)
    llm.register_function("yesno_start", start, cancel_on_interruption=False)  # see PREPARING_NOTE
    return [
        FunctionSchema(
            name="yesno_start",
            description="Prépare une partie de « ni oui ni non » (questions pièges adaptées à l'enfant) et renvoie "
            "son record et la première question. Ensuite, l'arbitrage se fait tout seul." + PREPARING_NOTE,
            properties={},
            required=[],
        )
    ]
