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
import threading
import time
from datetime import date, datetime
from pathlib import Path

from loguru import logger

from aipet import school
from aipet.fileio import atomic_write_json, atomic_write_text, load_json, lock_for
from aipet.memory.cards import (  # noqa: F401 (re-exported: the memory's public format)
    DIR_CONVERSATIONS,
    DIR_FAMILY,
    DIR_GAMES,
    DIR_MEMORIES,
    DIR_STORIES,
    DISLIKES,
    FACTS,
    HISTORY,
    LEGACY_SUMMARY_FILE,
    LIFE_ADVENTURES,
    LIFE_CHOSEN,
    LIFE_EMPTY,
    LIFE_FILE,
    LIFE_JOKES,
    LIFE_SECTIONS,
    LIFE_WORDS,
    LIKES,
    MAX_ADVENTURES,
    MAX_JOKES,
    MAX_SCHOOL_TOPICS,
    SCHOOL,
    SECTIONS,
    SUMMARY_FILE,
    SUMMARY_MAX,
    WORDS_IN_PROMPT,
    add_history,
    life_title,
    parse_card,
    parse_life,
    render_card,
    render_life,
    school_level,
)
from aipet.memory.forget import ForgetRequest, scrub_sentences, scrub_value
from aipet.memory.repo import MemoryRepo
from aipet.memory.text import STOPWORDS, normalize_text, slug, without_article  # noqa: F401
from aipet.memory.updates import plan_update
from aipet.timefmt import MONTHS_FR, TZ, WEEKDAYS_FR, now, spoken_date  # noqa: F401

NOTES_FILE, NOTES_IN_PROGRESS = "a_trier.md", "a_trier.en-cours.md"
DELETED_CARDS_FILE = "fiches_supprimees.json"  # names whose card a parent deleted
FORGET_NOTE = "DEMANDE D'OUBLI"
# A forget request also cleans what is not on disk yet (the conversation waiting to be condensed);
# conversations are condensed well within this delay.
FORGET_PENDING_SECS = 6 * 3600
# `kind` values of the search tool → directory searched (None = all except raw transcripts).
SEARCH_KINDS = {
    "all": None,
    "family": DIR_FAMILY,
    "memories": DIR_MEMORIES,
    "stories": DIR_STORIES,
    "conversations": DIR_CONVERSATIONS,
}


class Memory:
    """One pet's memory folder. Use `Memory.shared(root)` in the app, so the voice pipeline and the
    web page share one object; the file lock is per folder in any case."""

    _shared: dict[Path, Memory] = {}
    _shared_guard = threading.Lock()

    @classmethod
    def shared(cls, root: str | Path, pet_name: str = "") -> Memory:
        key = Path(os.path.expanduser(str(root))).resolve()
        with cls._shared_guard:
            if key not in cls._shared:
                cls._shared[key] = cls(key, pet_name)
            return cls._shared[key]

    def __init__(self, root: str | Path, pet_name: str = ""):
        self.root = Path(os.path.expanduser(str(root)))
        self.pet_name = pet_name
        self._files_lock = lock_for(self.root)
        self.repo = MemoryRepo(self.root, pet_name)
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
        if not self.repo.exists:
            self.repo.init()
        self._lock = asyncio.Lock()  # one condensation at a time (slow: model call)
        # Read-modify-write of the files and git (short). Process-wide per folder: the web page's
        # corrections and the pipeline's condensations never interleave.
        self._forgotten: list[ForgetRequest] = []  # recent requests, applied to what is still pending

    def _summary_title(self) -> str:
        return f"Ce que {self.pet_name} sait" if self.pet_name else "Ce que je sais"

    # ---------------------------------------------------------------- reading
    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def files(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts and p.suffix in (".md", ".jsonl")
        )

    def readable(self) -> list[Path]:
        """What the model may read with `read_memory`: the memory files, never git internals, state
        files or the pending notes (they quote forget requests)."""
        return [p for p in self.files() if not p.name.startswith("a_trier")]

    def read(self, path: str) -> str | None:
        p = (self.root / path).resolve()
        if p not in {f.resolve() for f in self.readable()}:
            return None
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

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

    def school_levels(self, today: date) -> list[str]:
        """Current (or typical) school level of each known child, for the landmarks section."""
        levels = []
        for f in (self.root / DIR_FAMILY).glob("*.md"):
            meta = parse_card(f.read_text(encoding="utf-8"))[0]
            if meta.get("role") in (None, "", "enfant"):
                level = school_level(meta, today)
                if level:
                    levels.append(level)
        return levels

    def search(self, query: str, kind: str = "all", limit: int = 5) -> list[dict]:
        """Keyword search: accent-insensitive, stopwords ignored; short words match whole
        (« chat » does not find « château »), long words by their prefix; YYYY-MM-DD dates;
        recent memories ranked higher. Returns [{"file", "excerpt"}]."""
        folder = SEARCH_KINDS.get(kind)  # an unknown kind searches everything allowed
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
        for p in self.readable():  # never the pending notes: they quote forget requests
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
        hits.sort(key=lambda h: -float(h["score"]))
        return [{k: v for k, v in h.items() if k != "score"} for h in hits[:limit]]

    # ------------------------------------------------------------- writing
    def note(self, info: str, who: str | None = None) -> None:
        line = f"- {now():%Y-%m-%d %H:%M}{f' ({who})' if who else ''} : {info.strip()}\n"
        with self._files_lock, open(self.root / NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(line)

    def take_notes(self) -> str | None:
        """Notes to fold into the next condensation. They stay in a_trier.en-cours.md until
        `notes_done()`, so a failed condensation keeps them; notes taken meanwhile go to a new file."""
        notes, in_progress = self.root / NOTES_FILE, self.root / NOTES_IN_PROGRESS
        with self._files_lock:
            if notes.exists():
                with open(in_progress, "a", encoding="utf-8") as f:
                    f.write(notes.read_text(encoding="utf-8"))
                notes.unlink()
            return in_progress.read_text(encoding="utf-8") if in_progress.exists() else None

    def notes_done(self) -> None:
        with self._files_lock:
            (self.root / NOTES_IN_PROGRESS).unlink(missing_ok=True)

    def forget(self, what: str, who: str | None = None) -> dict:
        """Forget request said out loud, deliberately cautious (see ForgetRequest):
        - with `who`: in that person's card, remove the items containing all the important words;
        - without `who`: at least two important words, and the exact phrase must appear in the item;
        - everywhere else (memories, stories, the pet's life, summary, transcripts): only sentences
          containing all the important words (two minimum);
        - never a whole card, story or day. What is still pending (the conversation not condensed
          yet, a condensation running) is cleaned when it is written.
        Returns {"removed_items", "files"} and "refused" (reason) when too vague."""
        req = ForgetRequest.parse(what, who)
        res: dict = {"removed_items": 0, "files": []}
        if req.too_vague:
            # French: this reason is passed back to the model.
            res["refused"] = "trop vague : il faut quelques mots précis" + ("" if who else ", ou dire de qui il s'agit")
            return res
        with self._files_lock:
            cards = sorted((self.root / DIR_FAMILY).glob("*.md"))
            if req.who:
                cards = [p for p in cards if p.stem == slug(req.who)]
                if not cards:
                    res["refused"] = f"je n'ai pas de fiche pour {req.who}"
                    return res
            self._forgotten = [r for r in self._forgotten if time.monotonic() - r.at < FORGET_PENDING_SECS]
            self._forgotten.append(req)

            def changed(p: Path, text: str, n: int):
                atomic_write_text(p, text)
                res["removed_items"] += n
                res["files"].append(self._rel(p))

            card_match = req.contains_all if req.who else req.in_phrase
            for p in cards:
                meta, sec = parse_card(p.read_text(encoding="utf-8"))
                new = {k: [c for c in v if not card_match(c)] for k, v in sec.items()}
                n = sum(len(v) for v in sec.values()) - sum(len(v) for v in new.values())
                if n:
                    changed(p, render_card(meta, new), n)
            everywhere = req.everywhere
            if req.broad or req.who:
                life = self.root / LIFE_FILE
                if life.exists():
                    sections = parse_life(life.read_text(encoding="utf-8"))
                    new = {k: [c for c in v if not everywhere(c)] for k, v in sections.items()}
                    n = sum(map(len, sections.values())) - sum(map(len, new.values()))
                    if n:
                        changed(life, render_life(new, self.pet_name), n)
                texts = [
                    *sorted((self.root / DIR_MEMORIES).glob("*.md")),
                    *sorted((self.root / DIR_STORIES).glob("*.md")),
                ]
                summary = self.root / SUMMARY_FILE
                if summary.exists():
                    texts.append(summary)
                for p in texts:
                    text, n = scrub_sentences(p.read_text(encoding="utf-8"), everywhere)
                    if n:
                        changed(p, text, n)
                # Game records and the story universe (guessed answers, quiz questions, characters).
                for p in [
                    *sorted((self.root / DIR_GAMES).glob("*.json")),
                    *sorted((self.root / DIR_STORIES).glob("*.json")),
                ]:
                    with lock_for(p):  # the games lock their file the same way
                        data = load_json(p, None)
                        if data is None:
                            continue
                        cleaned = scrub_value(data, everywhere)
                        if cleaned != data:
                            atomic_write_json(p, cleaned)
                            res["removed_items"] += 1
                            res["files"].append(self._rel(p))
            with open(self.root / NOTES_FILE, "a", encoding="utf-8") as f:  # the condensation cleans the summary too
                f.write(
                    f"- {now():%Y-%m-%d %H:%M} : {FORGET_NOTE}{f' (concerne {req.who})' if req.who else ''} "
                    f"→ effacer du résumé : {req.what}\n"
                )
            res["removed_items"] += self._scrub_transcripts(lambda t: req.matches(t) or everywhere(t))
            if res["removed_items"]:
                self.wipe_history()  # git would otherwise keep the forgotten text in older versions
            return res

    def recently_forgotten(self):
        """Matcher for what recent forget requests removed (None if there are none)."""
        self._forgotten = [r for r in self._forgotten if time.monotonic() - r.at < FORGET_PENDING_SECS]
        return self._pending_match() if self._forgotten else None

    def _pending_match(self, owner: str = ""):
        """Matches text that a recent forget request removed (for what was not on disk yet)."""
        reqs = list(self._forgotten)
        return lambda t: any(r.matches(t) or (r.about(owner) and r.contains_all(t)) for r in reqs)

    def _scrub_update(self, update: dict) -> dict:
        if not self._forgotten:
            return update
        update = scrub_value(update, self._pending_match())
        update["people"] = [
            scrub_value(person, self._pending_match(str(person.get("name") or "")))
            for person in update.get("people") or []
            if isinstance(person, dict)
        ]
        return update

    def _scrub_transcripts(self, match) -> int:
        """Remove matching lines from the raw transcripts (conversations/*.jsonl)."""
        removed = 0
        for p in (self.root / DIR_CONVERSATIONS).glob("*.jsonl"):
            lines = p.read_text(encoding="utf-8").splitlines()
            kept = [line for line in lines if not match(line)]
            if len(kept) != len(lines):
                atomic_write_text(p, "\n".join(kept) + ("\n" if kept else ""))
                removed += len(lines) - len(kept)
        return removed

    def delete(self, rel: str) -> bool:
        """A parent deletes a memory file, for real (older versions too). Never the summary, the
        git folder or its settings."""
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if root not in p.parents or not p.is_file():
            return False
        if ".git" in p.relative_to(root).parts or p.name in (SUMMARY_FILE, ".gitignore"):
            return False
        with self._files_lock:
            if p.parent == root / DIR_FAMILY:  # not re-created by the next condensation
                name = parse_card(p.read_text(encoding="utf-8"))[0].get("prenom", p.stem)
                self._block_card(name)
            p.unlink()
            self.wipe_history("Memory (history cleared after a parent deleted a file)")
        return True

    def blocked_cards(self) -> set[str]:
        """Names whose card a parent deleted: the condensation never re-creates them by itself."""
        return set(load_json(self.root / DELETED_CARDS_FILE, []))

    def _block_card(self, name: str) -> None:
        names = self.blocked_cards() | {normalize_text(name)}
        atomic_write_json(self.root / DELETED_CARDS_FILE, sorted(names))

    def forget_pending(self, text: str, who: str | None = None) -> None:
        """A parent removed `text`: also keep it out of what is still pending (a condensation
        running, the conversation not condensed yet), like a forget request said out loud."""
        req = ForgetRequest.parse(text, who)
        if req.words:
            with self._files_lock:
                self._forgotten.append(req)

    def summary_text(self) -> str:
        p = self.root / SUMMARY_FILE
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def wipe_history(self, message: str = "Memory (history cleared after a forget request)"):
        """Forget for real: start the memory's git history again from the current files."""
        self.repo.wipe(message)

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
        with self._files_lock:
            match = self._pending_match()
            with open(p, "a", encoding="utf-8") as f:
                for t in turns:
                    if not self._forgotten or not match(t.get("text", "")):
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
        return p

    def apply(
        self,
        update: dict,
        when: datetime,
        allowed_new_names: set[str] | None = None,
        summary_before: str | None = None,
    ) -> list[str]:
        """Apply the structured updates produced by the model (see SCHEMA).
        Everything is computed first, then written at once: an error midway writes nothing
        (no duplicated memory when the condensation is retried). `summary_before`: the summary the
        model was shown; if a parent rewrote it meanwhile, the parent's version wins."""
        with self._files_lock:  # cleaned of anything forgotten while the model was writing it
            if summary_before is not None and self.summary_text() != summary_before:
                logger.info("Summary changed during the condensation (a parent's correction): kept")
                update = {k: v for k, v in update.items() if k != "pet_summary"}
            return self._apply(self._scrub_update(update), when, allowed_new_names)

    def _apply(self, update: dict, when: datetime, allowed_new_names: set[str] | None = None) -> list[str]:
        planned = plan_update(
            self.root, self.pet_name, self._summary_title(), update, when, allowed_new_names, self.blocked_cards()
        )
        for p, content in planned.files.items():  # atomic write, file by file
            if content is None:
                p.unlink(missing_ok=True)
            else:
                atomic_write_text(p, content)
        self._commit(f"Condensation {when:%Y-%m-%d %H:%M}")
        return planned.labels

    def _commit(self, msg: str):
        self.repo.commit(msg)
