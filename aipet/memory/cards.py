"""The memory's on-disk format (French data names: the model reads them, real data exists):
person cards (frontmatter + bulleted sections), the pet's life file, folder and file names."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aipet import school
from aipet.memory.text import normalize_text, slug, without_article

# Section headings of a person's card (on-disk data format, French).
SECTIONS = ["Aime", "N'aime pas", "À savoir", "À l'école", "Historique"]
LIKES, DISLIKES, FACTS, SCHOOL, HISTORY = SECTIONS
MAX_SCHOOL_TOPICS = 8
SUMMARY_FILE = "RESUME.md"
LEGACY_SUMMARY_FILE = "FLOCON.md"  # name before the character packs (renamed on open)
SUMMARY_MAX = 2500  # characters of the summary


LIFE_FILE = "MA_VIE.md"
LIFE_SECTIONS = [
    "Ce que les enfants ont choisi pour moi",
    "Ma collection de mots",
    "Mes petites aventures",
    "Nos blagues à nous",
]
LIFE_CHOSEN, LIFE_WORDS, LIFE_ADVENTURES, LIFE_JOKES = LIFE_SECTIONS
LIFE_EMPTY = "Rien pour l'instant : tout reste à écrire avec les enfants."
MAX_ADVENTURES = 12
MAX_JOKES = 20
WORDS_IN_PROMPT = 25

# On-disk directories (French data names).
DIR_FAMILY, DIR_MEMORIES, DIR_STORIES, DIR_CONVERSATIONS = "famille", "souvenirs", "histoires", "conversations"


# --------------------------------------------------------------------------
# Person cards: simple frontmatter + bulleted sections
# --------------------------------------------------------------------------
def parse_card(text: str) -> tuple[dict, dict[str, list[str]]]:
    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {s: [] for s in SECTIONS}
    cur: str | None = None
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    for line in body.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections.setdefault(cur, [])
        elif line.startswith("- ") and cur:
            sections[cur].append(line[2:].strip())
    return meta, sections


HISTORY_CHARS = 200


def add_history(hist: list[str], day: str, topics: list[str], keep: int = 20) -> list[str]:
    """One « YYYY-MM-DD : topics » line per day in a card's history; same-day topics are merged."""
    hist = list(hist)
    same_day = bool(hist) and hist[-1].startswith(day) and " : " in hist[-1]
    merged = (hist[-1].split(" : ", 1)[1].split(", ") if same_day else []) + topics
    kept: list[str] = []
    for t in dict.fromkeys(merged):  # whole topics only, within HISTORY_CHARS
        if len(", ".join(kept + [t])) > HISTORY_CHARS:
            break
        kept.append(t)
    line = f"{day} : " + ", ".join(kept or [merged[0][:HISTORY_CHARS]])
    if same_day:
        hist[-1] = line
    else:
        hist.append(line)
    return hist[-keep:]


def render_card(meta: dict, sections: dict[str, list[str]]) -> str:
    fm = "\n".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
    out = [f"---\n{fm}\n---", f"# {meta.get('prenom', '?')}", ""]
    for name in SECTIONS + [s for s in sections if s not in SECTIONS]:
        items = sections.get(name) or []
        if items:
            out += [f"## {name}", *[f"- {i}" for i in items], ""]
    return "\n".join(out).rstrip() + "\n"


def parse_life(text: str) -> dict[str, list[str]]:
    _, sections = parse_card(text)
    return {name: sections.get(name, []) for name in LIFE_SECTIONS}


def life_title(pet_name: str) -> str:
    return f"Ma vie de {pet_name}" if pet_name else "Ma vie"


def render_life(sections: dict[str, list[str]], pet_name: str = "") -> str:
    out = [f"# {life_title(pet_name)}", ""]
    for name in LIFE_SECTIONS:
        if sections.get(name):
            out += [f"## {name}", *[f"- {i}" for i in sections[name]], ""]
    if len(out) == 2:
        out.append(LIFE_EMPTY)
    return "\n".join(out).rstrip() + "\n"


def item_key(item: str, sep: str) -> str:
    """Normalized part of a line before `sep` (trait name, word…), for de-duplication."""
    return without_article(normalize_text(item.split(sep, 1)[0]).strip())


def card_path(memory_root: str | Path, name: str) -> Path:
    return Path(memory_root) / DIR_FAMILY / f"{slug(name)}.md"


def _noted(meta: dict, key: str) -> date | None:
    try:
        return date.fromisoformat(meta.get(key, ""))
    except ValueError:
        return None


def school_level(meta: dict, today: date) -> str | None:
    """A child's school level from their card: the class noted (advanced at each rentrée), else
    the typical level for their age."""
    level = school.current_level(meta.get("classe"), _noted(meta, "classe_note_le"), today)
    if level:
        return level
    age = school.current_age(school.parse_age(meta.get("age")), _noted(meta, "age_note_le"), today)
    return school.typical_level(age)


def child_level(memory_root: str | Path, name: str, today: date) -> str | None:
    """School level of a known child (from their card), or None."""
    card = card_path(memory_root, name)
    if not card.exists():
        return None
    return school_level(parse_card(card.read_text(encoding="utf-8"))[0], today)
