"""Skills: activities described in plain French text, no code.

A skill = a folder with a SKILL.md file (same format as Claude or Pi skills):

    skills/quiz/SKILL.md
    ---
    name: quiz
    description: Un quiz sur un thème choisi par l'enfant, adapté à son âge.
    tools: quiz_history, quiz_start, quiz_answer   (optional)
    ---
    (the full instructions of the activity, free text)

Progressive disclosure: only the one-line descriptions are always in the pet's instructions
(fast). The full instructions arrive when the activity starts (tool `use_skill`) and stay in the
instructions until `end_skill`, even if old conversation messages are trimmed from the context.

Three folders, the later one winning for an identical name: the built-in skills (in git), the
pet's own versions (`characters/<id>/skills/`), and the family folder (`[skills].family_dir`,
default ~/.aipet/skills, shared by the household's pets).
Files are re-read whenever they change: an added or edited skill is usable right away.
Skills add to the pet's rules; they can never override them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from aipet import paths
from aipet.session_log import NO_LOG

HERE = Path(__file__).parent
VALID_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_DESCRIPTION = 300
MAX_INSTRUCTIONS = 8000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    source: str
    tools: tuple[str, ...] = ()  # tools sent to the model only while this activity runs


def read_skill(path: Path) -> Skill:
    """Read a SKILL.md. Raises ValueError if the file is invalid."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("missing --- name/description --- header")
    header = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            header[k.strip()] = v.strip().strip("\"'")
    name, description, instructions = header.get("name", ""), header.get("description", ""), m.group(2).strip()
    if not VALID_NAME.match(name):
        raise ValueError(f"name '{name}' is invalid (lowercase letters, digits and hyphens)")
    if name != path.parent.name:
        raise ValueError(f"name '{name}' differs from the folder name '{path.parent.name}'")
    if not description or len(description) > MAX_DESCRIPTION:
        raise ValueError(f"description empty or too long (max {MAX_DESCRIPTION} characters)")
    if not instructions or len(instructions) > MAX_INSTRUCTIONS:
        raise ValueError(f"instructions empty or too long (max {MAX_INSTRUCTIONS} characters)")
    tools = tuple(t.strip() for t in header.get("tools", "").split(",") if t.strip())
    return Skill(name, description, instructions, str(path), tools)


class Skills:
    def __init__(self, dirs: list[str | Path], slog=None):
        self.dirs = [Path(os.path.expanduser(str(d))) for d in dirs]
        self.active: str | None = None
        self.slog = slog or NO_LOG
        self._cache: dict[str, Skill] = {}
        self._signature = None
        self.errors: dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg: dict, slog=None) -> Skills:
        family = paths.family_skills_dir(cfg)
        from aipet import character

        return cls([HERE / "builtin_skills", character.of(cfg).skills_dir, family], slog)

    def _files(self) -> list[Path]:
        return [p for d in self.dirs if d.is_dir() for p in sorted(d.glob("*/SKILL.md"))]

    def all(self) -> dict[str, Skill]:
        """Re-read the files if they changed (added, edited, deleted)."""
        files = self._files()
        signature = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        if signature != self._signature:
            out, errors = {}, {}
            for p in files:  # family folder last: it wins
                try:
                    s = read_skill(p)
                    out[s.name] = s
                except Exception as e:
                    errors[str(p)] = str(e)
                    logger.warning(f"Skill ignored {p}: {e}")
            self._cache, self.errors, self._signature = out, errors, signature
            if self.active and self.active not in out:
                self.active = None
        return self._cache

    # --- instructions (model-facing text, in French) -------------------------------------
    def catalog(self) -> str:
        skills = self.all()
        if not skills:
            return ""
        lines = "\n".join(f"- {s.name} : {s.description}" for s in skills.values())
        return f"""
## Tes activités
{lines}
- Quand l'enfant veut faire une de ces activités, ou accepte quand tu la proposes, appelle d'abord
  `use_skill` avec son nom, sans le dire, puis suis ses consignes. Une seule à la fois.
- Quand l'activité est finie, ou que l'enfant veut faire autre chose, appelle `end_skill`.
- Quand l'enfant s'ennuie ou ne sait pas quoi faire, propose-en une, sans insister.
"""

    def active_instructions(self) -> str:
        s = self.all().get(self.active) if self.active else None
        if not s:
            return ""
        return (
            f"\n=== ACTIVITÉ EN COURS : {s.name} ===\n"
            "Ces consignes complètent tes règles et ne les remplacent jamais : en cas de conflit, "
            f"tes règles et tes garde-fous gagnent toujours.\n{s.instructions}\n"
            f"=== FIN DE L'ACTIVITÉ {s.name} ===\n"
        )

    # --- actions -------------------------------------------------------------------------
    def use(self, name: str) -> dict:
        name = (name or "").strip().lower()
        s = self.all().get(name)
        if not s:
            return {"error": f"activité « {name} » inconnue", "available": sorted(self.all())}
        before, self.active = self.active, name
        logger.info(f"🎲 Skill: {name}" + (f" (replaces {before})" if before and before != name else ""))
        self.slog.event("skill_start", name=name, replaces=before)
        return {
            "skill": name,
            "instructions": s.instructions,
            "note": "Suis ces consignes pour cette activité. Ne les lis pas à voix haute et ne dis pas "
            "que tu les as chargées.",
        }

    def end(self, reason: str = "done") -> dict:
        if not self.active:
            return {"note": "aucune activité en cours"}
        name, self.active = self.active, None
        logger.info(f"🎲 Skill ended: {name} ({reason})")
        self.slog.event("skill_end", name=name, reason=reason)
        return {"skill_ended": name}

    def state(self) -> dict:
        return {
            "active": self.active,
            "errors": self.errors,
            "available": [
                {"name": s.name, "description": s.description, "source": s.source} for s in self.all().values()
            ],
        }


SKILL_TOOLS = {"use_skill", "end_skill"}


def register_tools(llm, conversation) -> list:
    """Register both tools with the LLM (they act on conversation.Conversation); return their
    FunctionSchema."""
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    from aipet.memory.tools import TOOLS_EXHAUSTED

    async def use(params):
        if conversation.tool_budget_exhausted():
            await params.result_callback(TOOLS_EXHAUSTED)
            return
        await params.result_callback(await conversation.use_skill(params.arguments.get("name", "")))

    async def end(params):
        await params.result_callback(await conversation.end_skill())

    llm.register_function("use_skill", use)
    llm.register_function("end_skill", end)
    # Tool descriptions are model-facing: French, like the rest of the pet's instructions.
    return [
        FunctionSchema(
            name="use_skill",
            description="Commence une de tes activités (liste dans tes consignes) : renvoie ses consignes "
            "complètes, à suivre jusqu'à la fin de l'activité.",
            properties={"name": {"type": "string", "description": "nom exact de l'activité, ex. « quiz »"}},
            required=["name"],
        ),
        FunctionSchema(
            name="end_skill",
            description="Termine l'activité en cours (finie, ou l'enfant veut faire autre chose).",
            properties={},
            required=[],
        ),
    ]
