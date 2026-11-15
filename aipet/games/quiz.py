"""Quiz game, with its state kept by code (not by the model's memory).

- `quiz_history`: what this child already played (themes, best scores, streaks), so the pet can
  suggest a new theme or a record to beat.
- `quiz_start(theme)`: questions are really prepared up front by a model call (adapted to the
  child's school level, never repeating questions already asked on that theme), each with its
  answer and a short explanation.
- `quiz_answer(correct)`: the model judges the child's answer; code keeps the score and the
  streak, returns the explanation and the next question, and saves the game at the end.

Stored in the family memory as `jeux/quiz.json` (data; texts are French).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from loguru import logger

from aipet import engines
from aipet.games.common import (
    GAMES_DIR,
    PREPARING_NOTE,
    STALE_PREPARATION,
    UNKNOWN_PLAYER,
    Activity,
    JsonStore,
    cancel_preparation,
    child_level,
    days_in_a_row,
    is_stale,
    new_preparation,
    pet_intro,
    theme_key,
)
from aipet.memory.store import normalize_text, now

QUIZ_TOOLS = {"quiz_history", "quiz_start", "quiz_answer"}
DEFAULT_QUESTIONS = 5
# Difficulty per child and per theme, relative to the child's school level (model-facing, French).
DIFFICULTIES = {
    1: "très facile pour sa classe : des questions pour prendre confiance",
    2: "facile pour sa classe",
    3: "de son niveau de classe",
    4: "un peu plus difficile que sa classe : un petit défi",
    5: "expert : des questions de passionné, un vrai défi de grand",
}
START_DIFFICULTY = 2
LEVEL_UP_RATIO, LEVEL_DOWN_RATIO = 0.8, 0.4  # score/total after a game
MAX_EXCLUDED = 60  # past questions sent to the generator, per theme

# Model-facing (French): the question generator.
GENERATOR_INSTRUCTIONS = """Tu prépares un quiz pour un enfant, joué à voix haute avec {pet}.
Règles :
- Uniquement des faits sûrs et vérifiables, adaptés à son niveau scolaire. Aucune question piège.
- Questions courtes, faciles à comprendre à l'oral, variées (pas toutes sur le même aspect du thème).
- Pour les petits (jusqu'au CE1, ou âge inconnu), trois choix courts dans "choices" ; pour les grands,
  "choices" peut être vide (question ouverte avec une réponse courte).
- "answer" : la bonne réponse, courte. "explanation" : une ou deux phrases simples qui expliquent la bonne
  réponse, avec un détail intéressant, dites à un enfant.
- Respecte la difficulté demandée, et range les questions de la plus facile à la plus difficile : la dernière
  est un petit défi.
- N'utilise AUCUNE des questions déjà posées listées, ni une simple reformulation."""

GENERATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["question", "choices", "answer", "explanation"],
            },
        }
    },
    "required": ["questions"],
}


@dataclass
class Game:
    player: str
    theme: str
    level: str | None
    questions: list[dict]
    difficulty: int = START_DIFFICULTY
    index: int = 0
    score: int = 0
    streak: int = 0
    best_streak: int = 0
    answers: list[bool] = field(default_factory=list)

    def current(self) -> dict:
        q = self.questions[self.index]
        return {"number": self.index + 1, "question": q["question"], "choices": q.get("choices") or []}


class QuizStore(JsonStore):
    """`jeux/quiz.json`: games per player, best score per theme, best streak, days played."""

    def __init__(self, memory_root: Path):
        super().__init__(Path(memory_root) / GAMES_DIR / "quiz.json", {"players": {}})

    def save_game(self, game: Game, when) -> dict:
        """Record a finished game; return the player's records after it."""
        with self.lock:
            data = self.load()
            p = data["players"].setdefault(game.player, {"games": [], "best": {}, "best_streak": 0, "play_days": []})
            p["games"].append(
                {
                    "date": when.isoformat(timespec="minutes"),
                    "theme": game.theme,
                    "level": game.level,
                    "score": game.score,
                    "total": len(game.questions),
                    "questions": [
                        {"question": q["question"], "answer": q["answer"], "correct": ok}
                        for q, ok in zip(game.questions, game.answers, strict=False)
                    ],
                }
            )
            key = theme_key(game.theme)
            ratio = game.score / max(len(game.questions), 1)
            diffs = p.setdefault("difficulty", {})
            before = diffs.get(key, game.difficulty)
            after = (
                min(5, before + 1)
                if ratio >= LEVEL_UP_RATIO
                else max(1, before - 1)
                if ratio <= LEVEL_DOWN_RATIO
                else before
            )
            diffs[key] = after
            previous = p["best"].get(key)
            new_record = previous is None or game.score > previous["score"]
            if new_record:
                p["best"][key] = {"theme": game.theme, "score": game.score, "total": len(game.questions)}
            p["best_streak"] = max(p.get("best_streak", 0), game.best_streak)
            day = when.date().isoformat()
            if day not in p["play_days"]:
                p["play_days"].append(day)
            self.save(data)
            return {
                "new_record": new_record and previous is not None,
                "previous_best": previous,
                "best_streak": p["best_streak"],
                "days_in_a_row": days_in_a_row(p["play_days"], when.date()),
                "difficulty": after,
                "level_change": "up" if after > before else "down" if after < before else None,
            }

    def difficulty(self, player: str, theme: str) -> int:
        p = self.load()["players"].get(player, {})
        return p.get("difficulty", {}).get(theme_key(theme), START_DIFFICULTY)

    def asked(self, theme: str) -> list[str]:
        """Questions already asked on this theme (to anyone in the family)."""
        key = theme_key(theme)
        out = []
        for p in self.load()["players"].values():
            for g in p["games"]:
                if theme_key(g["theme"]) == key:
                    out += [q["question"] for q in g["questions"]]
        return out[-MAX_EXCLUDED:]

    def history(self, player: str, today: date) -> dict:
        p = self.load()["players"].get(player)
        if not p:
            return {"player": player, "games_played": 0, "note": "première partie de quiz"}
        themes = sorted(p["best"].values(), key=lambda b: -b["score"])
        last = p["games"][-1]
        return {
            "player": player,
            "games_played": len(p["games"]),
            "themes": [
                f"{b['theme']} : record {b['score']}/{b['total']}, niveau "
                f"{p.get('difficulty', {}).get(theme_key(b['theme']), START_DIFFICULTY)} sur 5"
                for b in themes
            ],
            "last_game": f"{last['theme']}, {last['score']}/{last['total']}, le {last['date'][:10]}",
            "best_streak": p.get("best_streak", 0),
            "days_in_a_row": days_in_a_row(p["play_days"], today),
        }


def generate_questions(
    cfg: dict, theme: str, level: str | None, exclude: list[str], n: int, difficulty: int = START_DIFFICULTY
) -> list[dict]:
    """Prepare n questions with the conversation model (synchronous: run it in a thread)."""
    who = f"un enfant en {level}" if level else "un enfant d'environ 5 à 10 ans (niveau inconnu)"
    past = "\n".join(f"- {q}" for q in exclude) or "(aucune)"
    prompt = (
        f"Thème : {theme}\nPour : {who}\nDifficulté : {difficulty} sur 5, {DIFFICULTIES[difficulty]}\n"
        f"Nombre de questions : {n}\n\nQuestions déjà posées sur ce thème :\n{past}"
    )
    res = engines.complete_json(
        cfg,
        GENERATOR_INSTRUCTIONS.format(pet=pet_intro(cfg)),
        prompt,
        GENERATOR_SCHEMA,
        role="preparation",
        activity="quiz",
        timeout=30,
    )
    excluded = {normalize_text(q).strip(" ?") for q in exclude}
    questions = [
        q
        for q in res.get("questions", [])
        if q.get("question") and q.get("answer") and normalize_text(q["question"]).strip(" ?") not in excluded
    ]
    return questions[:n]


class Quiz(Activity):
    tools = frozenset(QUIZ_TOOLS)

    def register_tools(self, llm) -> list:
        return register_tools(llm, self)

    """The quiz in progress (one at a time) and its tools."""

    def __init__(self, cfg: dict, memory_root: Path, host=None, slog=None):
        super().__init__(host, slog)
        self.cfg, self.root = cfg, Path(memory_root)
        self.store = QuizStore(self.root)
        self.game: Game | None = None

    def player(self) -> str:
        return self.host.player()

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    def history(self) -> dict:
        return self.store.history(self.player(), now().date())

    async def start(self, theme: str, count: int = DEFAULT_QUESTIONS) -> dict:
        theme = (theme or "").strip()
        if not theme:
            return {"error": "Demande d'abord le thème à l'enfant."}
        count = max(3, min(int(count or DEFAULT_QUESTIONS), 10))
        player = self.player()
        level = child_level(self.root, player, now().date()) if player != UNKNOWN_PLAYER else None
        difficulty = self.store.difficulty(player, theme)
        prep = new_preparation(self)
        try:
            questions = await asyncio.to_thread(
                generate_questions, self.cfg, theme, level, self.store.asked(theme), count, difficulty
            )
        except Exception as e:
            logger.warning(f"Quiz preparation failed: {e}")
            return {"error": "Le quiz n'a pas pu être préparé. Propose un autre jeu ou réessaie plus tard."}
        if is_stale(self, prep):
            return STALE_PREPARATION
        if len(questions) < 3:
            return {"error": "Pas assez de nouvelles questions sur ce thème : propose un autre thème."}
        self.game = Game(player, theme, level, questions, difficulty=difficulty)
        logger.info(f"🧩 Quiz « {theme} » for {player} ({level or 'level unknown'}): {len(questions)} questions")
        self._ev("quiz_start", theme=theme, player=player, level=level, questions=len(questions))
        best = self.store.load()["players"].get(player, {}).get("best", {}).get(theme_key(theme))
        return {
            "theme": theme,
            "total": len(questions),
            "record_on_this_theme": f"{best['score']}/{best['total']}" if best else None,
            "difficulty": f"niveau {difficulty} sur 5 ({DIFFICULTIES[difficulty]})",
            "question": self.game.current(),
            "note": "Annonce le quiz en une phrase, puis pose la question 1 (avec les choix s'il y en a).",
        }

    def on_user_message(self, text: str) -> str | None:
        return None  # the model judges quiz answers (quiz_answer)

    def stop(self):
        """Activity ended, sleep, reset: an unfinished quiz is dropped."""
        cancel_preparation(self)
        self.game = None

    def answer(self, correct: bool, question_number: int | None = None) -> dict:
        g = self.game
        if not g:
            return {"error": "Aucun quiz en cours : appelle d'abord quiz_start."}
        if question_number is not None and int(question_number) != g.index + 1:
            return {"error": f"La question {question_number} est déjà comptée : on en est à la question {g.index + 1}.",
                    "current_question": g.current()}  # fmt: skip
        q = g.questions[g.index]
        g.answers.append(bool(correct))
        if correct:
            g.score += 1
            g.streak += 1
            g.best_streak = max(g.best_streak, g.streak)
        else:
            g.streak = 0
        g.index += 1
        res = {
            "correct": bool(correct),
            "right_answer": q["answer"],
            "explanation": q.get("explanation", ""),
            "score": f"{g.score}/{g.index}",
            "streak": g.streak,
        }
        if g.index < len(g.questions):
            res["next_question"] = g.current()
            res["note"] = (
                "Bonne réponse : félicite avec un petit détail de l'explication. Mauvaise réponse : donne gentiment "
                "la bonne réponse et l'explication en une ou deux phrases. Puis pose la question suivante."
            )
        else:
            records = self.store.save_game(g, now())
            self._ev("quiz_end", theme=g.theme, player=g.player, score=g.score, total=len(g.questions))
            res["finished"] = {"score": g.score, "total": len(g.questions), **records}
            res["note"] = (
                "Réagis à la dernière réponse (avec l'explication si elle était fausse), annonce le score final et "
                'félicite. Mentionne le record battu ou la série de jours s\'il y en a. Si level_change vaut "up", '
                'annonce fièrement qu\'il passe au niveau supérieur sur ce thème ; si "down", dis gentiment que la '
                "prochaine fois tu prépareras des questions un peu plus faciles. Propose de rejouer sur un "
                "nouveau thème ; sinon appelle end_skill."
            )
            self.game = None
        return res


def register_tools(llm, quiz: Quiz) -> list:
    """Register the quiz tools with the LLM; return their FunctionSchema."""
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    async def budget_ok(params) -> bool:
        if refused := quiz.budget_refused():
            await params.result_callback(refused)
            return False
        return True

    async def history(params):
        if await budget_ok(params):
            await params.result_callback(quiz.history())

    async def start(params):
        if await budget_ok(params):
            a = params.arguments
            await params.result_callback(await quiz.start(a.get("theme", ""), a.get("count", DEFAULT_QUESTIONS)))

    async def answer(params):
        a = params.arguments
        await params.result_callback(quiz.answer(bool(a.get("correct")), a.get("question_number")))

    llm.register_function("quiz_history", history)
    llm.register_function("quiz_start", start, cancel_on_interruption=False)  # see PREPARING_NOTE
    llm.register_function("quiz_answer", answer)
    # Model-facing descriptions (French).
    return [
        FunctionSchema(
            name="quiz_history",
            description="Ce que l'enfant qui parle a déjà joué au quiz : thèmes, records, meilleure série, "
            "jours de jeu d'affilée. À appeler au début du quiz, avant de proposer un thème.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="quiz_start",
            description="Prépare un vrai quiz sur le thème choisi (questions nouvelles, adaptées à l'enfant) et "
            "renvoie la première question." + PREPARING_NOTE,
            properties={
                "theme": {"type": "string", "description": "le thème choisi, ex. « les animaux de la mer »"},
                "count": {"type": "integer", "description": "nombre de questions (5 par défaut)"},
            },
            required=["theme"],
        ),
        FunctionSchema(
            name="quiz_answer",
            description="Après chaque réponse de l'enfant au quiz : dis si elle est juste (sois souple sur la "
            "formulation). Renvoie la bonne réponse, son explication, le score et la question suivante.",
            properties={
                "correct": {"type": "boolean", "description": "true si la réponse de l'enfant est juste"},
                "question_number": {"type": "integer", "description": "le numéro de la question à laquelle il répond"},
            },
            required=["correct", "question_number"],
        ),
    ]
