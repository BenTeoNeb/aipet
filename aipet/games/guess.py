"""« Devine à quoi je pense »: the pet asks yes/no questions; the question count is kept by code.

- `guess_start`: starts a round and returns the child's history (who won how often, what they
  thought of last time).
- During the round, each child message counts as the answer to one of the pet's questions. The
  count goes into the message label (« question 12 sur 20 »), with a warning near the end; at the
  limit the pet must stop, give up (« je donne ma langue au chat ») and ask for the answer.
- `guess_end(found, answer)`: records the result. Stored in `jeux/devine.json` per child. A win
  (`found=true`) is only accepted when the pet really proposed that answer just before and the child
  said yes: a misheard reply must not become a fake victory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from aipet.games.common import GAMES_DIR, Activity, JsonStore
from aipet.memory.text import normalize_text
from aipet.timefmt import now

GUESS_TOOLS = {"guess_start", "guess_end"}
MAX_QUESTIONS = 20
WARN_BEFORE = 5  # warn the pet when this many questions are left

# The child confirms the pet's guess (normalized text: lower case, no accents).
YES = re.compile(
    r"^\W*(oui|ouais|ouiii+|yes|bravo|exact|exactement|gagne|c'?est (ca|bien ca|juste|vrai)|"
    r"(tu (l'?)?as |t'as )?trouve)\b"
)
VAGUE_WORDS = {"petit", "petite", "grand", "grande", "gros", "grosse", "animal", "chose", "truc", "bebe"}


def answer_words(answer: str) -> set[str]:
    words = re.findall(r"[a-z]+", normalize_text(answer))
    return {w for w in words if len(w) >= 3 and w not in VAGUE_WORDS}


def confirms_guess(answer: str, pet_said: str, child_said: str) -> bool:
    """True if the pet's last line proposed `answer` and the child's reply is a yes."""
    said = set(re.findall(r"[a-z]+", normalize_text(pet_said)))
    words = answer_words(answer)
    return bool(words & said) and bool(YES.search(normalize_text(child_said)))


@dataclass
class GuessRound:
    player: str
    max_questions: int
    count: int = 0  # child answers so far = questions asked
    over: bool = False  # limit reached: the pet must give up and ask
    revealed: str | None = None  # what the child said right after the pet gave up
    pending_end: bool = False  # guess_end(found=False) came before the child revealed the answer
    last_reply: str = ""  # the child's latest message


class GuessGame(Activity):
    tools = frozenset(GUESS_TOOLS)

    def register_tools(self, llm) -> list:
        return register_tools(llm, self)

    def __init__(self, memory_root, host=None, slog=None, max_questions: int = MAX_QUESTIONS):
        super().__init__(host, slog)
        self.max_questions = max_questions
        self.store = JsonStore(memory_root / GAMES_DIR / "devine.json", {"players": {}})
        self.round: GuessRound | None = None

    def history(self, player: str) -> dict:
        p = self.store.load()["players"].get(player)
        if not p:
            return {"games_played": 0}
        games = p["games"]
        found = [g for g in games if g["found"]]
        out = {
            "games_played": len(games),
            "pet_found": len(found),
            "child_won": len(games) - len(found),
            "recent_answers": [g["answer"] for g in games[-5:] if g.get("answer")],
        }
        if found:
            out["fewest_questions_to_find"] = min(g["questions"] for g in found)
        return out

    def start(self) -> dict:
        player = self.host.player()
        self.round = GuessRound(player, self.max_questions)
        logger.info(f"🔮 Guess game for {player} (max {self.max_questions} questions)")
        self.slog.event("guess_start", player=player)
        return {
            "player": player,
            "max_questions": self.max_questions,
            "history": self.history(player),
            "note": (
                f"Tu as {self.max_questions} questions au plus. Le système les compte : l'étiquette de chaque "
                "message te dira où tu en es. Si tu trouves, ou à la fin, appelle guess_end. Évite de deviner "
                "la même chose que les dernières fois (recent_answers) sans de bons indices."
            ),
        }

    def on_user_message(self, text: str) -> str | None:
        r = self.round
        if not r:
            return None
        r.last_reply = text.strip()
        if r.over:
            if r.revealed is not None:
                return None
            r.revealed = text.strip()
            if r.pending_end:  # the pet already ended the round: record the real answer now
                self._record(found=False, answer=r.revealed)
                return "devine : l'enfant vient de te donner la réponse ; la partie est enregistrée. Réagis avec joie"
            return (
                "devine : l'enfant vient de répondre. S'il dit oui à ta proposition, tu as trouvé : appelle "
                "guess_end avec found=true. Sinon c'est la réponse : réagis avec joie, puis appelle guess_end "
                "avec found=false et answer = ce qu'il vient de dire"
            )
        r.count += 1
        left = r.max_questions - r.count
        if left <= 0:
            r.over = True
            return (
                f"devine : c'était ta question {r.count} sur {r.max_questions}, la DERNIÈRE. Ne pose plus de "
                "question : si sa réponse te permet de deviner, propose UNE seule réponse ; sinon, dis « je donne "
                "ma langue au chat », déclare-le gagnant et demande-lui à quoi il pensait. N'appelle PAS encore "
                "guess_end : attends sa réponse"
            )
        if left <= WARN_BEFORE:
            return f"devine : question {r.count} sur {r.max_questions}, plus que {left} : resserre et tente ta chance"
        return f"devine : question {r.count} sur {r.max_questions}"

    def end(self, found: bool, answer: str = "") -> dict:
        r = self.round
        if not r:
            return {"error": "Aucune partie en cours."}
        if not found and r.revealed is None and (r.over or not self._child_said(answer)):
            # Called while giving up, before the child said what it was (the pet may even make up an
            # answer): record the child's real answer later.
            r.over = r.pending_end = True
            return {"note": "Attends que l'enfant te dise à quoi il pensait ; ce sera enregistré tout seul."}
        if not found and r.revealed and not answer:
            answer = r.revealed
        if found and not self._win_confirmed(answer):
            logger.info(f"🔮 Win refused: « {answer} » was not proposed then confirmed (child: {r.last_reply!r})")
            self.slog.event("guess_win_refused", answer=answer, child=r.last_reply)
            return {
                "error": f"Victoire pas confirmée : tu n'as pas proposé « {answer} » juste avant, ou l'enfant n'a "
                "pas dit oui.",
                "note": f"Demande d'abord « Est-ce que c'est {answer} ? » et attends son oui. Si tu as mal "
                "entendu, demande-lui gentiment de répéter. Si c'est lui qui t'a donné la réponse, il a gagné : "
                "found=false.",
            }
        return self._record(found, answer)

    def _child_said(self, answer: str) -> bool:
        """True if the child's last message contains the answer (it just revealed it)."""
        words = answer_words(answer)
        return bool(words) and bool(words & answer_words(self.round.last_reply))

    def _win_confirmed(self, answer: str) -> bool:
        pet_said = self.host.last_pet_line()
        return confirms_guess(answer, pet_said, self.round.last_reply)

    def _record(self, found: bool, answer: str) -> dict:
        r = self.round
        with self.store.lock:
            data = self.store.load()
            p = data["players"].setdefault(r.player, {"games": []})
            p["games"].append({"date": now().isoformat(timespec="minutes"), "found": bool(found),
                               "answer": (answer or "").strip(), "questions": r.count})  # fmt: skip
            self.store.save(data)
        self.slog.event("guess_end", player=r.player, found=bool(found), questions=r.count)
        self.round = None
        h = self.history(r.player)
        return {
            "found": bool(found),
            "questions": r.count,
            "score": f"{self.host.pet_name} {h['pet_found']} – {r.player} {h['child_won']}",
            "note": "Félicite l'enfant (surtout s'il t'a battu), dis le score de vos parties, propose une revanche.",
        }

    def stop(self):
        """Activity ended mid-round: nothing to record (no answer known)."""
        self.round = None


def register_tools(llm, game: GuessGame) -> list:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    async def start(params):
        if refused := game.budget_refused():
            await params.result_callback(refused)
            return
        await params.result_callback(game.start())

    async def end(params):
        a = params.arguments
        await params.result_callback(game.end(bool(a.get("found")), a.get("answer", "")))

    llm.register_function("guess_start", start)
    llm.register_function("guess_end", end)
    return [
        FunctionSchema(
            name="guess_start",
            description="Commence une partie de « devine à quoi je pense », quand l'enfant est prêt : renvoie le "
            "nombre maximum de questions et l'historique de vos parties.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="guess_end",
            description="Fin de la partie de devinette, quand tu connais la VRAIE réponse : après l'avoir trouvée, "
            "ou après que l'enfant te l'a dite. As-tu trouvé, et à quoi pensait l'enfant.",
            properties={
                "found": {"type": "boolean", "description": "true si tu as trouvé"},
                "answer": {"type": "string", "description": "ce à quoi l'enfant pensait, ex. « un pingouin »"},
            },
            required=["found", "answer"],
        ),
    ]
