"""Pet characters: who the pet is, loaded from a pack in `characters/<id>/`.

The core (guardrails, speaking rules, games, memory, rhythm) is the same for every pet. A pack only
fills in what makes this pet itself:

    characters/<id>/
        character.toml   identity, grammar, voice, wake word, emotion wording, spoken lines
        persona.md       the persona (French, goes into the system prompt)
        face/face.svg    the face drawn on the web page (one pose per emotion, via data-emo)
        face/face.css
        skills/          optional: the pet's own versions of built-in skills (same name wins)

A pack is data, not code: it cannot remove the guardrails (`prompts.check_safety` refuses a system
prompt without them), and every required spoken line must be present.
"""

import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from aipet import env

CHARACTERS_DIR = Path(__file__).parent.parent / "characters"
DEFAULT = "flocon"

# Spoken lines a pack must provide (French content). Lists: one is picked at random.
REQUIRED_LINES = {
    "nap": list,  # asleep (nap), no model call
    "night": list,  # asleep (night), no model call
    "nudges": list,  # an activity answer was not heard
    "fillers": list,  # the reply is slow
    "still_working": str,  # the reply is still not there
    "apology": str,  # the turn failed
    "model_timeout": str,  # the model does not answer at all
}
REQUIRED_TEXT = ("name", "intro", "sleep_habit", "evening_mood")


class CharacterError(ValueError):
    pass


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    feminine: bool  # French agreement (« le petit compagnon » / « la petite compagne »)
    intro: str  # how preparation prompts introduce the pet, after its name: « un petit chat »
    sleep_habit: str  # first sentence of the rhythm rules: why and how this pet sleeps
    evening_mood: str  # « un peu ensommeillé le soir (…) »
    persona: str
    root: Path
    birthday: str | None = None
    lines: dict = field(default_factory=dict)
    emotions: dict = field(default_factory=dict)  # wording per emotion tag (the tag set is the core's)
    voice: dict = field(default_factory=dict)  # merged into [engines.tts]
    wake: dict = field(default_factory=dict)  # overrides [wake]
    undecided: tuple[str, ...] = ()  # [life].undecided: what a child may choose for the pet
    collection_ask: str = ""  # [life].collection_ask: « de t'apprendre un mot pour ta collection »

    def g(self, masculine: str, feminine: str) -> str:
        """French gender agreement for this pet."""
        return feminine if self.feminine else masculine

    def line(self, key: str) -> str:
        v = self.lines[key]
        return random.choice(v) if isinstance(v, list) else v

    @property
    def face_dir(self) -> Path:
        return self.root / "face"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"


def available(base: Path = CHARACTERS_DIR) -> list[str]:
    return sorted(p.name for p in base.iterdir() if (p / "character.toml").is_file())


def load(char_id: str, base: Path = CHARACTERS_DIR) -> Character:
    from aipet.emotions import EMOTIONS

    root = base / char_id
    path = root / "character.toml"
    if not path.is_file():
        raise CharacterError(f"Unknown character {char_id!r} (available: {', '.join(available(base))})")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    missing = [k for k in REQUIRED_TEXT if not str(data.get(k, "")).strip()]
    if missing:
        raise CharacterError(f"{path}: missing {missing}")
    gender = data.get("gender", "m")
    if gender not in ("m", "f"):
        raise CharacterError(f"{path}: gender must be 'm' or 'f'")
    lines = data.get("lines", {})
    for key, kind in REQUIRED_LINES.items():
        v = lines.get(key)
        if not isinstance(v, kind) or not v or (kind is list and not all(isinstance(x, str) and x for x in v)):
            raise CharacterError(f"{path}: [lines].{key} must be a non-empty {kind.__name__}")
    emotions = data.get("emotions", {})
    unknown = set(emotions) - set(EMOTIONS)
    if unknown:
        raise CharacterError(f"{path}: unknown emotion tags {sorted(unknown)} (the tag set is fixed: {list(EMOTIONS)})")
    life = data.get("life", {})
    undecided = life.get("undecided", [])
    if not isinstance(undecided, list) or not all(isinstance(x, str) and x.strip() for x in undecided):
        raise CharacterError(f"{path}: [life].undecided must be a list of non-empty strings")
    if not isinstance(life.get("collection_ask", ""), str):
        raise CharacterError(f"{path}: [life].collection_ask must be a string")
    persona_file = root / "persona.md"
    persona = persona_file.read_text() if persona_file.is_file() else ""
    if not persona.strip():
        raise CharacterError(f"{persona_file}: missing or empty")
    return Character(
        id=char_id,
        name=data["name"].strip(),
        feminine=gender == "f",
        intro=data["intro"].strip(),
        sleep_habit=data["sleep_habit"].strip(),
        evening_mood=data["evening_mood"].strip(),
        persona=persona,
        root=root,
        birthday=data.get("birthday"),
        lines=lines,
        emotions=emotions,
        voice=data.get("voice", {}),
        wake=data.get("wake", {}),
        undecided=tuple(x.strip() for x in undecided),
        collection_ask=life.get("collection_ask", "").strip(),
    )


def selected_id(cfg: dict) -> str:
    return env.get("CHARACTER") or cfg.get("character") or DEFAULT


def of(cfg: dict) -> Character:
    """The pet of this configuration (loaded once and kept in cfg["pet"])."""
    pet = cfg.get("pet")
    if pet is None:
        pet = cfg["pet"] = load(selected_id(cfg))
    return pet
