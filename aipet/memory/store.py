"""The pet's memory: structured markdown files + condensation by the model.

Layout (one folder per pet, default ~/.aipet/pets/<pet>/memory, see paths.py). The on-disk names are French
on purpose: they are family data the model reads, and real data already exists.

    RESUME.md                  condensed summary, always injected into the system prompt
    MA_VIE.md                  the pet's own life: traits the children chose, words, adventures, jokes
    famille/<name>.md          one card per person (age, likes, dislikes, facts…)
    souvenirs/YYYY-MM-DD.md    one entry per conversation (time, who, topics, summary)
    histoires/<title>.md       unfinished stories and games, so they can be resumed
    a_trier.md                 pending forget requests, folded in at the next condensation
    conversations/*.jsonl      raw transcripts (trace, searchable on request)

Reading: summary + cards + latest memories are injected at the start of each session, and the
`search_memory` / `read_memory` / `forget` tools let the pet look things up when needed.
Writing: after each conversation chunk, a model call turns the transcript into structured
updates, applied here (and versioned with git when available).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from aipet import school

TZ = ZoneInfo("Europe/Paris")
# French words: dates are spoken / written in French for the model and the memory files.
WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
# Section headings of a person's card (on-disk data format, French).
SECTIONS = ["Aime", "N'aime pas", "À savoir", "À l'école", "Historique"]
LIKES, DISLIKES, FACTS, SCHOOL, HISTORY = SECTIONS
MAX_SCHOOL_TOPICS = 8
SUMMARY_FILE = "RESUME.md"
LEGACY_SUMMARY_FILE = "FLOCON.md"  # name before the character packs (renamed on open)
SUMMARY_MAX = 2500  # characters of the summary

# The pet's own life (on-disk data, French): what the children chose for it, the words they taught
# him, the little adventures he tells about himself, and the jokes he shares with each child.
# Always injected into the prompt, so the pet stays consistent and keeps growing with the family.
# Never kept in the pet's life file (words taught, inside jokes): rude words and put-downs a child might
# teach the pet to repeat to another child. French on purpose (data matched against French speech).
RUDE = re.compile(
    r"\b(merde|putain|con|conne|connard|connasse|salaud|salope|enfoir\w*|encul\w*|bite|chier|idiot\w*|"
    r"debile|cretin\w*|imbecile|stupide|nul|nulle|moche|bebe|pue|puent|gros\s+lard|tais[- ]toi)\b"
)
GENERIC_NAMES = {"un enfant", "enfant", "inconnu", "inconnue", "famille", "quelqu'un", "personne", "?"}


def is_rude(text: str) -> bool:
    return bool(RUDE.search(normalize_text(text)))


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
# French stopwords ignored by the keyword search.
STOPWORDS = set(
    """les des une est que qui quoi dans pour avec sur pas plus son ses mon mes ton tes
notre votre leur leurs elle ils elles nous vous tout tous toute cette ces comme mais donc car
etait avait fait faire dit aux par ont sont quand comment pourquoi rappelle souviens souvenir
toi moi lui deja encore aussi tres bien chez""".split()
)

# On-disk directories (French data names).
DIR_FAMILY, DIR_MEMORIES, DIR_STORIES, DIR_CONVERSATIONS = "famille", "souvenirs", "histoires", "conversations"
# `kind` values of the search tool → directory searched (None = all except raw transcripts).
SEARCH_KINDS = {
    "all": None,
    "family": DIR_FAMILY,
    "memories": DIR_MEMORIES,
    "stories": DIR_STORIES,
    "conversations": DIR_CONVERSATIONS,
}


def now() -> datetime:
    return datetime.now(TZ)


def spoken_date(d: datetime) -> str:
    """Date and time as said in French, e.g. « mercredi 11 novembre 2026, 18h02 »."""
    return f"{WEEKDAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]} {d.year}, {d.hour}h{d.minute:02d}"


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _without_article(s: str) -> str:
    return re.sub(r"^(les|le|la|l'|un|une|des|du|de la|de) ", "", s.strip())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_text(s)).strip("-") or "untitled"


# --------------------------------------------------------------------------
# Person cards: simple frontmatter + bulleted sections
# --------------------------------------------------------------------------
def parse_card(text: str) -> tuple[dict, dict[str, list[str]]]:
    meta, sections, cur = {}, {s: [] for s in SECTIONS}, None
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


def _key(item: str, sep: str) -> str:
    """Normalized part of a line before `sep` (trait name, word…), for de-duplication."""
    return _without_article(normalize_text(item.split(sep, 1)[0]).strip())


class Memory:
    def __init__(self, root: str | Path, pet_name: str = ""):
        self.root = Path(os.path.expanduser(str(root)))
        self.pet_name = pet_name
        for d in [DIR_FAMILY, DIR_MEMORIES, DIR_STORIES, DIR_CONVERSATIONS]:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        legacy = self.root / LEGACY_SUMMARY_FILE
        if legacy.exists() and not (self.root / SUMMARY_FILE).exists():
            legacy.rename(self.root / SUMMARY_FILE)
            logger.info(f"Memory: {LEGACY_SUMMARY_FILE} renamed {SUMMARY_FILE}")
            if (self.root / ".git").exists():
                self._commit(f"{LEGACY_SUMMARY_FILE} renamed {SUMMARY_FILE} (character packs)")
        if not (self.root / SUMMARY_FILE).exists():
            (self.root / SUMMARY_FILE).write_text(
                f"# {self._summary_title()}\n\nRien pour l'instant : c'est le début de l'aventure.\n"
            )
        if not (self.root / LIFE_FILE).exists():
            (self.root / LIFE_FILE).write_text(render_life({}, pet_name))
        # Raw transcripts and pending notes stay out of git: they must be truly erasable
        # (limited retention).
        gi = self.root / ".gitignore"
        if not gi.exists():
            gi.write_text("conversations/\na_trier*.md\n*.tmp\npresence.json\n")
        for pattern in ("*.tmp", "presence.json"):
            if pattern not in gi.read_text().split():
                gi.write_text(gi.read_text() + pattern + "\n")
        if not (self.root / ".git").exists():
            try:
                subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
                self._git_identity()
            except Exception as e:
                logger.warning(f"git unavailable for memory history: {e}")
        self._lock = asyncio.Lock()  # one condensation at a time (slow: model call)
        self._files_lock = threading.Lock()  # read-modify-write of the files (short)

    def _summary_title(self) -> str:
        return f"Ce que {self.pet_name} sait" if self.pet_name else "Ce que je sais"

    def _git_identity(self):
        subprocess.run(["git", "config", "user.name", self.pet_name or "pet"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "pet@localhost"], cwd=self.root, check=True)

    # ---------------------------------------------------------------- reading
    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def files(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts and p.suffix in (".md", ".jsonl")
        )

    def read(self, path: str) -> str | None:
        p = (self.root / path).resolve()
        if self.root.resolve() not in p.parents or not p.is_file():
            return None
        return p.read_text()

    def context_block(self, max_memories_chars: int = 2500, full_stories: bool = False) -> str:
        """Block injected into the system prompt (French headings: the model reads them)."""
        parts = [(self.root / SUMMARY_FILE).read_text().strip()[:SUMMARY_MAX]]
        life = parse_life((self.root / LIFE_FILE).read_text()) if (self.root / LIFE_FILE).exists() else {}
        if life.get(LIFE_WORDS):
            life[LIFE_WORDS] = life[LIFE_WORDS][-WORDS_IN_PROMPT:]
        parts.append("#" + render_life(life, self.pet_name).strip())
        cards = sorted((self.root / DIR_FAMILY).glob("*.md"))
        if cards:
            today = now().date()
            texts = []
            for f in cards:
                txt = f.read_text().strip()
                line = school.child_summary(parse_card(txt)[0], today)
                texts.append(f"{txt}\n{line}" if line else txt)
            parts.append("## Fiches de la famille\n" + "\n\n".join(texts))
        days = sorted((self.root / DIR_MEMORIES).glob("*.md"), reverse=True)[:3]
        recent = "\n".join(d.read_text().strip() for d in reversed(days))
        if recent:
            parts.append("## Derniers souvenirs\n" + recent[-max_memories_chars:])
        stories = sorted((self.root / DIR_STORIES).glob("*.md"))
        if stories and full_stories:
            parts.append(
                "## Histoires et jeux en cours (état complet)\n" + "\n\n".join(s.read_text().strip() for s in stories)
            )
        elif stories:
            parts.append(
                "## Histoires et jeux en cours\n"
                + "\n".join(
                    f"- « {s.read_text().splitlines()[0].lstrip('# ').strip()} » (fichier {DIR_STORIES}/{s.name})"
                    for s in stories
                )
            )
        return "\n\n".join(parts)

    def school_levels(self, today) -> list[str]:
        """Current (or typical) school level of each known child, for the landmarks section."""
        levels = []
        for f in (self.root / DIR_FAMILY).glob("*.md"):
            meta = parse_card(f.read_text())[0]
            if meta.get("role") not in (None, "", "enfant"):
                continue
            try:
                noted = date.fromisoformat(meta.get("classe_note_le", ""))
            except ValueError:
                noted = None
            level = school.current_level(meta.get("classe"), noted, today)
            if not level:
                try:
                    age_noted = date.fromisoformat(meta.get("age_note_le", ""))
                except ValueError:
                    age_noted = None
                level = school.typical_level(school.current_age(school.parse_age(meta.get("age")), age_noted, today))
            if level:
                levels.append(level)
        return levels

    def search(self, query: str, kind: str = "all", limit: int = 5) -> list[dict]:
        """Keyword search: accent-insensitive, stopwords ignored; short words match whole
        (« chat » does not find « château »), long words by their prefix; YYYY-MM-DD dates;
        recent memories ranked higher. Returns [{"file", "excerpt"}]."""
        folder = SEARCH_KINDS[kind] if kind in SEARCH_KINDS else kind  # tolerate a raw directory name
        q = normalize_text(query)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", q)
        q = re.sub(r"\d{4}-\d{2}-\d{2}", " ", q)
        terms = [t for t in re.findall(r"\w+", q) if len(t) > 2 and t not in STOPWORDS and not t.isdigit()]
        if not terms and not dates:
            return []

        def pattern(t: str):
            if len(t) > 5:  # long words: word prefix (dinosaure → dinosaures)
                return re.compile(r"\b" + re.escape(t[:6]))
            base = t[:-1] if len(t) > 4 and t.endswith("s") else t
            return re.compile(r"\b" + re.escape(base) + r"(?:s|e|es|x)?\b")

        pats = [pattern(t) for t in terms]
        hits = []
        for p in self.files():
            rel = self._rel(p)
            if folder and not rel.startswith(folder):
                continue
            if not folder and rel.startswith(DIR_CONVERSATIONS):
                continue  # raw transcripts: only on explicit request
            m = re.search(r"(\d{4}-\d{2}-\d{2})", rel)
            if dates and not (m and m.group(1) in dates):
                continue  # a date was asked: only that day
            age_days = (now().date() - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days if m else 0
            text = p.read_text()
            chunks = re.split(r"\n(?=## )", text) if p.suffix == ".md" else text.splitlines()
            owner = ""
            if rel.startswith(DIR_FAMILY + "/"):  # every chunk of a card keeps the person's name
                owner = normalize_text(parse_card(text)[0].get("prenom", p.stem)) + " "
            for ch in chunks:
                n = owner + normalize_text(ch)  # the path is not scored (otherwise « histoire » finds everything)
                found = [len(pt.findall(n)) for pt in pats]
                matched = sum(1 for f in found if f)
                if matched or (dates and not terms):
                    score = 10 * matched + min(sum(found), 10) - min(age_days, 365) / 60
                    hits.append({"file": rel, "excerpt": ch.strip()[:600], "score": score})
        hits.sort(key=lambda h: -h["score"])
        return [{k: v for k, v in h.items() if k != "score"} for h in hits[:limit]]

    # ------------------------------------------------------------- writing
    def note(self, info: str, who: str | None = None) -> None:
        line = f"- {now():%Y-%m-%d %H:%M}{f' ({who})' if who else ''} : {info.strip()}\n"
        with open(self.root / "a_trier.md", "a") as f:
            f.write(line)

    def forget(self, what: str, who: str | None = None) -> dict:
        """Forget request said out loud, deliberately cautious:
        - with `who`: only in that person's card, remove items containing all the important
          words of the request (« le foot » removes « aime le foot »);
        - without `who`: at least two important words, and the exact phrase must appear;
        - memories: only the sentences containing all important words (2 minimum);
        - never a whole card, a story or a whole day.
        The request is also passed to the condensation to clean the summary.
        Returns {"removed_items", "files"} and "refused" (reason) when too vague."""
        with self._files_lock:
            return self._forget(what, who)

    def _forget(self, what: str, who: str | None) -> dict:
        target = normalize_text(what).strip()
        words = [w for w in re.findall(r"\w+", target) if w not in STOPWORDS and len(w) > 2]
        res = {"removed_items": 0, "files": []}
        if not words or (not who and len(words) < 2):  # « oublie Emma » alone: touch nothing
            # French: this reason is passed back to the model.
            res["refused"] = "trop vague : il faut quelques mots précis" + ("" if who else ", ou dire de qui il s'agit")
            return res
        all_words = [re.compile(r"\b" + re.escape(w)) for w in words]

        def contains_all(t: str) -> bool:
            n = normalize_text(t)
            return all(pt.search(n) for pt in all_words)

        phrase = re.compile(r"\b" + re.escape(target) + r"\b")

        cards = sorted((self.root / DIR_FAMILY).glob("*.md"))
        if who:
            cards = [p for p in cards if p.stem == _slug(who)]
            if not cards:
                res["refused"] = f"je n'ai pas de fiche pour {who}"
                return res
        for p in cards:
            meta, sec = parse_card(p.read_text())
            match = contains_all if who else (lambda c: bool(phrase.search(normalize_text(c))))
            new = {k: (v if k == HISTORY else [c for c in v if not match(c)]) for k, v in sec.items()}
            n = sum(len(v) for v in sec.values()) - sum(len(v) for v in new.values())
            if n:
                p.write_text(render_card(meta, new))
                res["removed_items"] += n
                res["files"].append(self._rel(p))
        if len(words) >= 2:
            for p in sorted((self.root / DIR_MEMORIES).glob("*.md")):
                lines, n = [], 0
                for line in p.read_text().splitlines():
                    if line.startswith("#") or not contains_all(line):
                        lines.append(line)
                        continue
                    sentences = re.split(r"(?<=[.!?])\s+", line)
                    kept = [s for s in sentences if not contains_all(s)]
                    n += len(sentences) - len(kept)
                    lines.append(" ".join(kept) if kept else "(effacé à la demande)")
                if n:
                    p.write_text("\n".join(lines).rstrip() + "\n")
                    res["removed_items"] += n
                    res["files"].append(self._rel(p))
        with open(self.root / "a_trier.md", "a") as f:  # the condensation also removes it from the summary
            f.write(
                f"- {now():%Y-%m-%d %H:%M} : DEMANDE D'OUBLI{f' (concerne {who})' if who else ''} "
                f"→ effacer du résumé : {what.strip()}\n"
            )
        res["removed_items"] += self._scrub_transcripts(contains_all if len(words) >= 2 else phrase.search)
        summary = self.root / SUMMARY_FILE
        if summary.exists() and len(words) >= 2:  # immediately, not only at the next condensation
            text = summary.read_text()
            kept = [s for s in re.split(r"(?<=[.!?])\s+", text) if not contains_all(s)]
            if len(kept) < len(re.split(r"(?<=[.!?])\s+", text)):
                summary.write_text(" ".join(kept).rstrip() + "\n")
                res["removed_items"] += 1
                res["files"].append(SUMMARY_FILE)
        if res["removed_items"]:
            self.wipe_history()  # git would otherwise keep the forgotten text in older versions
        return res

    def _scrub_transcripts(self, match) -> int:
        """Remove matching lines from the raw transcripts (conversations/*.jsonl)."""
        removed = 0
        for p in (self.root / DIR_CONVERSATIONS).glob("*.jsonl"):
            lines = p.read_text().splitlines()
            kept = [line for line in lines if not match(normalize_text(line))]
            if len(kept) != len(lines):
                p.write_text("\n".join(kept) + ("\n" if kept else ""))
                removed += len(lines) - len(kept)
        return removed

    def wipe_history(self):
        """Forget for real: start the memory's git history again from the current files."""
        git = self.root / ".git"
        if not git.exists():
            return
        try:
            import shutil

            shutil.rmtree(git)
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
            self._git_identity()
            self._commit("Memory (history cleared after a forget request)")
        except Exception as e:
            logger.warning(f"Could not clear the memory history: {e}")

    def known_names(self) -> list[str]:
        names = []
        for p in sorted((self.root / DIR_FAMILY).glob("*.md")):
            names.append(parse_card(p.read_text())[0].get("prenom", p.stem.capitalize()))
        return names

    def purge_transcripts(self, days: int):
        limit = now().timestamp() - days * 86400
        for p in (self.root / DIR_CONVERSATIONS).glob("*.jsonl"):
            if p.stat().st_mtime < limit:
                p.unlink()

    def save_transcript(self, started: datetime, turns: list[dict]) -> Path:
        p = self.root / DIR_CONVERSATIONS / f"{started:%Y-%m-%d_%H%M%S}.jsonl"
        with open(p, "a") as f:
            for t in turns:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        return p

    def apply(self, update: dict, when: datetime, allowed_new_names: set[str] | None = None) -> list[str]:
        """Apply the structured updates produced by the model (see SCHEMA).
        Everything is computed first, then written at once: an error midway writes nothing
        (no duplicated memory when the condensation is retried)."""
        with self._files_lock:
            return self._apply(update, when, allowed_new_names)

    def _apply(self, update: dict, when: datetime, allowed_new_names: set[str] | None = None) -> list[str]:
        writes: dict[Path, str | None] = {}  # None = delete
        labels: list[str] = []

        def cur(p: Path) -> str | None:
            if p in writes:
                return writes[p]
            return p.read_text() if p.exists() else None

        mem = update.get("memory") or {}
        if mem.get("summary"):
            p = self.root / DIR_MEMORIES / f"{when:%Y-%m-%d}.md"
            old = cur(p) or f"# {spoken_date(when).rsplit(',', 1)[0].capitalize()}\n\n"
            who = ", ".join(mem.get("participants") or []) or "?"
            topics = ", ".join(mem.get("topics") or [])
            writes[p] = (
                old + f"## {when:%H}h{when:%M} — avec {who}\n"
                f"{f'Sujets : {topics}' + chr(10) if topics else ''}{mem['summary'].strip()}\n\n"
            )
            labels.append(self._rel(p))

        for person in update.get("people") or []:
            name = (person.get("name") or "").strip()
            if not name or normalize_text(name) in GENERIC_NAMES:
                continue
            p = self.root / DIR_FAMILY / f"{_slug(name)}.md"
            txt = cur(p)
            if (
                txt is None
                and allowed_new_names is not None
                and normalize_text(name) not in {normalize_text(n) for n in allowed_new_names}
            ):
                # A new card only for a confirmed name: « Lila » misheard for « Mila » must not become a
                # known person (which would skip the confirmation next time).
                logger.info(f"Card not created for an unconfirmed name: {name}")
                continue
            meta, sec = parse_card(txt) if txt else ({"prenom": name}, {k: [] for k in SECTIONS})
            for k in ("age", "role"):
                if person.get(k) and person[k] != "inconnu":
                    meta[k] = str(person[k])
            if person.get("age"):
                meta["age_note_le"] = f"{when:%Y-%m-%d}"  # to know whether the age is still current
            level = school.normalize_level(person.get("school_class"))
            if level:  # advanced automatically at each rentrée (see aipet.school)
                meta["classe"], meta["classe_note_le"] = level, f"{when:%Y-%m-%d}"
            topics = sec.setdefault(SCHOOL, [])
            for topic in person.get("school_topics") or []:
                if topic.strip() and normalize_text(topic.strip()) not in {
                    normalize_text(t.split(" : ", 1)[-1]) for t in topics
                }:
                    topics.append(f"{when:%Y-%m} : {topic.strip()}")
            sec[SCHOOL] = topics[-MAX_SCHOOL_TOPICS:]
            for key, section in [("likes", LIKES), ("dislikes", DISLIKES), ("facts", FACTS)]:
                lst = sec.setdefault(section, [])
                for item in person.get(key) or []:
                    if item.strip() and _without_article(normalize_text(item)) not in {
                        _without_article(normalize_text(c)) for c in lst
                    }:
                        lst.append(item.strip())
            for r in person.get("remove") or []:
                r = normalize_text(r).strip()
                if len(r) < 4:  # an empty or too short string would erase everything
                    continue
                for section in sec:
                    if section != HISTORY:  # exact, ignoring articles: « dinosaures » == « les dinosaures »
                        sec[section] = [
                            c for c in sec[section] if _without_article(normalize_text(c)) != _without_article(r)
                        ]
            writes[p] = render_card(meta, sec)
            labels.append(self._rel(p))

        # History: every participant who has a card, even when nothing new was learned about them
        # (the model lists in "people" only new information).
        if mem.get("summary"):
            topics = [t.strip() for t in mem.get("topics") or [] if t.strip()] or ["discussion"]
            for name in dict.fromkeys((x or "").strip() for x in mem.get("participants") or []):
                p = self.root / DIR_FAMILY / f"{_slug(name)}.md"
                txt = cur(p) if name else None
                if txt is None:  # no card: never created here (unconfirmed or generic names)
                    continue
                meta, sec = parse_card(txt)
                sec[HISTORY] = add_history(sec.get(HISTORY, []), f"{when:%Y-%m-%d}", topics)
                writes[p] = render_card(meta, sec)
                if self._rel(p) not in labels:
                    labels.append(self._rel(p))

        for story in update.get("stories") or []:
            if not story.get("title") or not story.get("state"):
                continue
            p = self.root / DIR_STORIES / f"{_slug(story['title'])}.md"
            if story.get("finished"):
                if cur(p) is not None:
                    writes[p] = None
                    labels.append(self._rel(p) + " (finished)")
                continue
            writes[p] = f"# {story['title']}\n\nMis à jour : {when:%Y-%m-%d %H:%M}\n\n{story['state'].strip()}\n"
            labels.append(self._rel(p))

        life_update = update.get("pet_life") or {}
        if any(life_update.get(k) for k in ("chosen_traits", "words", "adventures", "inside_jokes")):
            p = self.root / LIFE_FILE
            life = parse_life(cur(p) or "")
            day = f"{when:%Y-%m-%d}"
            for t in life_update.get("chosen_traits") or []:
                trait, value = (t.get("trait") or "").strip(), (t.get("value") or "").strip()
                if trait and value:  # a new choice for the same trait replaces the old one
                    by = (t.get("chosen_by") or "").strip()
                    line = f"{trait} : {value}" + (f" (choisi par {by}, le {day})" if by else f" (le {day})")
                    rest = [c for c in life[LIFE_CHOSEN] if _key(c, " : ") != _key(trait, " : ")]
                    life[LIFE_CHOSEN] = [*rest, line]
            for w in life_update.get("words") or []:
                word = (w.get("word") or "").strip()
                if word and is_rude(word):
                    logger.info("Rude word not added to the pet's word collection")
                    continue
                if word and _key(word, " (") not in {_key(c, " (") for c in life[LIFE_WORDS]}:
                    by = (w.get("taught_by") or "").strip()
                    life[LIFE_WORDS].append(f"{word} (appris de {by}, le {day})" if by else f"{word} (le {day})")
            for a in life_update.get("adventures") or []:
                if a.strip():
                    life[LIFE_ADVENTURES].append(f"{day} : {a.strip()}")
            life[LIFE_ADVENTURES] = life[LIFE_ADVENTURES][-MAX_ADVENTURES:]
            for j in life_update.get("inside_jokes") or []:
                who, joke = (j.get("with") or "").strip(), (j.get("joke") or "").strip()
                if is_rude(joke):
                    logger.info("Mocking or rude inside joke not kept")
                    continue
                line = f"avec {who} : {joke}" if who else joke
                if joke and normalize_text(line) not in {normalize_text(c) for c in life[LIFE_JOKES]}:
                    life[LIFE_JOKES].append(line)
            life[LIFE_JOKES] = life[LIFE_JOKES][-MAX_JOKES:]
            writes[p] = render_life(life, self.pet_name)
            labels.append(LIFE_FILE)

        if update.get("pet_summary"):
            writes[self.root / SUMMARY_FILE] = (
                f"# {self._summary_title()}\n\n" + update["pet_summary"].strip()[:SUMMARY_MAX] + "\n"
            )
            labels.append(SUMMARY_FILE)

        for p, content in writes.items():  # atomic write, file by file
            if content is None:
                p.unlink(missing_ok=True)
            else:
                tmp = p.with_suffix(p.suffix + ".tmp")
                tmp.write_text(content)
                tmp.replace(p)
        self._commit(f"Condensation {when:%Y-%m-%d %H:%M}")
        return labels

    def _commit(self, msg: str):
        try:
            subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-qm", msg], cwd=self.root, capture_output=True)
        except Exception:
            pass
