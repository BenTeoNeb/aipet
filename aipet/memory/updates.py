"""Turning the model's structured update (see condensation.SCHEMA) into file contents.

Nothing is written here: `plan_update` returns every new file content at once, so `Memory.apply`
can write them together (an error midway writes nothing, and a retried condensation does not
duplicate a memory). One function per part of the update.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from aipet import school
from aipet.memory.cards import (
    DIR_FAMILY,
    DIR_MEMORIES,
    DIR_STORIES,
    DISLIKES,
    FACTS,
    HISTORY,
    LIFE_ADVENTURES,
    LIFE_CHOSEN,
    LIFE_FILE,
    LIFE_JOKES,
    LIFE_WORDS,
    LIKES,
    MAX_ADVENTURES,
    MAX_JOKES,
    MAX_SCHOOL_TOPICS,
    SCHOOL,
    SECTIONS,
    SUMMARY_FILE,
    SUMMARY_MAX,
    add_history,
    item_key,
    parse_card,
    parse_life,
    render_card,
    render_life,
)
from aipet.memory.text import GENERIC_NAMES, is_rude, normalize_text, slug, without_article
from aipet.timefmt import spoken_date


class PendingWrites:
    """New file contents (None = delete), read back by the next steps of the same update."""

    def __init__(self, root: Path):
        self.root = root
        self.files: dict[Path, str | None] = {}
        self.labels: list[str] = []

    def current(self, p: Path) -> str | None:
        if p in self.files:
            return self.files[p]
        return p.read_text(encoding="utf-8") if p.exists() else None

    def put(self, p: Path, content: str | None, label: str | None = None):
        self.files[p] = content
        label = label or str(p.relative_to(self.root))
        if label not in self.labels:
            self.labels.append(label)


def plan_update(
    root: Path,
    pet_name: str,
    summary_title: str,
    update: dict,
    when: datetime,
    allowed_new_names: set[str] | None = None,
) -> PendingWrites:
    w = PendingWrites(root)
    _memory_entry(w, update.get("memory") or {}, when)
    for person in update.get("people") or []:
        _person(w, person, when, allowed_new_names)
    _history(w, update.get("memory") or {}, when)
    for story in update.get("stories") or []:
        _story(w, story, when)
    _pet_life(w, update.get("pet_life") or {}, pet_name, when)
    if update.get("pet_summary"):
        text = update["pet_summary"].strip()[:SUMMARY_MAX]
        w.put(root / SUMMARY_FILE, f"# {summary_title}\n\n{text}\n", SUMMARY_FILE)
    return w


def _memory_entry(w: PendingWrites, mem: dict, when: datetime):
    """One dated entry per conversation chunk in souvenirs/<day>.md."""
    if not mem.get("summary"):
        return
    p = w.root / DIR_MEMORIES / f"{when:%Y-%m-%d}.md"
    old = w.current(p) or f"# {spoken_date(when).rsplit(',', 1)[0].capitalize()}\n\n"
    who = ", ".join(mem.get("participants") or []) or "?"
    topics = ", ".join(mem.get("topics") or [])
    w.put(
        p,
        old + f"## {when:%H}h{when:%M} — avec {who}\n"
        f"{f'Sujets : {topics}' + chr(10) if topics else ''}{mem['summary'].strip()}\n\n",
    )


def _person(w: PendingWrites, person: dict, when: datetime, allowed_new_names: set[str] | None):
    """What was learned about someone, on their card."""
    name = (person.get("name") or "").strip()
    if not name or normalize_text(name) in GENERIC_NAMES:
        return
    p = w.root / DIR_FAMILY / f"{slug(name)}.md"
    txt = w.current(p)
    if (
        txt is None
        and allowed_new_names is not None
        and normalize_text(name) not in {normalize_text(n) for n in allowed_new_names}
    ):
        # A new card only for a confirmed name: « Lila » misheard for « Mila » must not become a
        # known person (which would skip the confirmation next time).
        logger.info(f"Card not created for an unconfirmed name: {name}")
        return
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
    known_topics = {normalize_text(t.split(" : ", 1)[-1]) for t in topics}
    for topic in person.get("school_topics") or []:
        if topic.strip() and normalize_text(topic.strip()) not in known_topics:
            topics.append(f"{when:%Y-%m} : {topic.strip()}")
            known_topics.add(normalize_text(topic.strip()))
    sec[SCHOOL] = topics[-MAX_SCHOOL_TOPICS:]
    for key, section in [("likes", LIKES), ("dislikes", DISLIKES), ("facts", FACTS)]:
        lst = sec.setdefault(section, [])
        for item in person.get(key) or []:
            if item.strip() and without_article(normalize_text(item)) not in {
                without_article(normalize_text(c)) for c in lst
            }:
                lst.append(item.strip())
    for r in person.get("remove") or []:
        r = normalize_text(r).strip()
        if len(r) < 4:  # an empty or too short string would erase everything
            continue
        for section in sec:
            if section != HISTORY:  # exact, ignoring articles: « dinosaures » == « les dinosaures »
                sec[section] = [c for c in sec[section] if without_article(normalize_text(c)) != without_article(r)]
    w.put(p, render_card(meta, sec))


def _history(w: PendingWrites, mem: dict, when: datetime):
    """Every participant who has a card gets a history line, even when nothing new was learned
    about them (the model lists in "people" only new information)."""
    if not mem.get("summary"):
        return
    topics = [t.strip() for t in mem.get("topics") or [] if t.strip()] or ["discussion"]
    for name in dict.fromkeys((x or "").strip() for x in mem.get("participants") or []):
        p = w.root / DIR_FAMILY / f"{slug(name)}.md"
        txt = w.current(p) if name else None
        if txt is None:  # no card: never created here (unconfirmed or generic names)
            continue
        meta, sec = parse_card(txt)
        sec[HISTORY] = add_history(sec.get(HISTORY, []), f"{when:%Y-%m-%d}", topics)
        w.put(p, render_card(meta, sec))


def _story(w: PendingWrites, story: dict, when: datetime):
    """An unfinished story or game, kept so it can be resumed; removed once finished."""
    if not story.get("title") or not story.get("state"):
        return
    p = w.root / DIR_STORIES / f"{slug(story['title'])}.md"
    if story.get("finished"):
        if w.current(p) is not None:
            w.put(p, None, str(p.relative_to(w.root)) + " (finished)")
        return
    w.put(p, f"# {story['title']}\n\nMis à jour : {when:%Y-%m-%d %H:%M}\n\n{story['state'].strip()}\n")


def _pet_life(w: PendingWrites, life_update: dict, pet_name: str, when: datetime):
    """The pet's own life: traits the children chose, words, adventures, inside jokes."""
    if not any(life_update.get(k) for k in ("chosen_traits", "words", "adventures", "inside_jokes")):
        return
    p = w.root / LIFE_FILE
    life = parse_life(w.current(p) or "")
    day = f"{when:%Y-%m-%d}"
    for t in life_update.get("chosen_traits") or []:
        trait, value = (t.get("trait") or "").strip(), (t.get("value") or "").strip()
        if trait and value:  # a new choice for the same trait replaces the old one
            by = (t.get("chosen_by") or "").strip()
            line = f"{trait} : {value}" + (f" (choisi par {by}, le {day})" if by else f" (le {day})")
            rest = [c for c in life[LIFE_CHOSEN] if item_key(c, " : ") != item_key(trait, " : ")]
            life[LIFE_CHOSEN] = [*rest, line]
    for wd in life_update.get("words") or []:
        word = (wd.get("word") or "").strip()
        if word and is_rude(word):
            logger.info("Rude word not added to the pet's word collection")
            continue
        if word and item_key(word, " (") not in {item_key(c, " (") for c in life[LIFE_WORDS]}:
            by = (wd.get("taught_by") or "").strip()
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
    w.put(p, render_life(life, pet_name), LIFE_FILE)
