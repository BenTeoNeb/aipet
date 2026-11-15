"""The parents' view: what happened, readable, from the session logs and the pet's memory.

- `thread(events)`: a session as a readable conversation (child / pet lines, activity markers).
- `day_digest(day)`: who talked to the pet that day, how long, what they did.
- `review()`: moments worth a parent's look (sadness, new names, swear words, noise taken for a
  child, confidences, refused wins, failures), each with a stable id that can be marked as seen.
- Corrections of the memory in one click: `structured(path)` and `edit(path, change)`, each edit
  committed in the memory's git history.

Everything is computed on request from local files; nothing leaves the machine. Text shown to
parents is French (data and UI), code and keys are English.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from aipet import paths, session_log
from aipet.character import Character
from aipet.emotions import EMOTIONS
from aipet.fileio import atomic_write_json, atomic_write_text, lock_for
from aipet.memory.store import (
    DIR_FAMILY,
    LIFE_FILE,
    SUMMARY_FILE,
    Memory,
    normalize_text,
    parse_card,
    render_card,
    render_life,
)

TAG = re.compile(r"\[([^\]\n]{1,40})\]")
# Swear words only (the memory's RUDE list also has « nul », « bébé »…, too common to alert on).
SWEAR = re.compile(r"\b(merde|putain|con|connard|connasse|salaud|salope|enfoir\w*|encul\w*|bite|chier|ta gueule)\b")
REVIEW_DAYS = 14
SKILL_LABELS = {
    "guess-what-im-thinking": "Devine à quoi je pense",
    "story": "Histoire",
    "quiz": "Quiz",
    "learn": "Découverte",
    "yes-no-game": "Ni oui ni non",
}


# ---------------------------------------------------------------------------------------- thread
def clean_reply(raw: str) -> tuple[str, list[str]]:
    """The pet's reply without its control tags, and the emotions it showed."""
    tags = TAG.findall(raw or "")
    text = re.sub(r"\s{2,}", " ", TAG.sub("", raw or "")).strip()
    return text, [t for t in tags if t in EMOTIONS]


def _marker(e: dict, pet: Character) -> tuple[str, str] | None:
    """(icon, French text) for an event worth showing in the conversation, else None."""
    t, n = e["type"], pet.name
    if t == "skill_start":
        return "🎲", f"Activité : {SKILL_LABELS.get(e.get('name', ''), e.get('name'))}"
    if t == "guess_end":
        q = e.get("questions")
        if e.get("found"):
            return "🔮", f"Devinette avec {e.get('player')} : {n} a trouvé en {q} questions"
        return "🔮", f"Devinette : {e.get('player')} a gagné ({q} questions)"
    if t == "story_start":
        return "📖", f"Histoire « {e.get('title')} » pour {e.get('player')}"
    if t == "story_end":
        return "📖", f"Fin de « {e.get('title')} »" + (f" — {e['reaction']}" if e.get("reaction") else "")
    if t == "quiz_end":
        return "❓", f"Quiz « {e.get('theme')} » avec {e.get('player')} : {e.get('score')}/{e.get('total')}"
    if t == "learn_end":
        return "🎓", f"Découverte « {e.get('topic')} » avec {e.get('player')}" + (" — a aimé" if e.get("liked") else "")
    if t == "yesno_end":
        return "🙊", f"Ni oui ni non avec {e.get('player')} : {e.get('held')} questions tenues"
    if t == "life_moment":
        return "🐾", f"{n} a demandé à {e.get('child')} de l'aider à grandir (« Ma vie »)"
    if t == "goodnight":
        return "🌙", "Bonne nuit"
    if t == "energy_state" and e.get("state") in ("tired", "very_tired"):
        very = "très " if e["state"] == "very_tired" else ""
        return "😴", f"{n} est {very}{pet.g('fatigué', 'fatiguée')}"
    if t == "hang_up":
        if e.get("reason") == "goodbye":
            return "👋", f"Au revoir : {n} a raccroché"
        return "📞", f"{n} s'est endormi{pet.g('', 'e')} et a raccroché"
    if t in ("sleepy_reply", "sleep", "goodnight_sleep"):
        return "💤", f"{n} dort"
    if t == "speaker" and e.get("previous") and e.get("name") != e.get("previous"):
        return "👋", f"{e.get('name')} prend la parole"
    if t == "turn_abandoned":
        return "⚠️", f"{n} n'a pas réussi à répondre (petit souci technique)"
    return None


def thread(events: list[dict], pet: Character) -> list[dict]:
    """A session as a conversation: {"kind": "child"|"pet"|"marker", "t", "text", …}."""
    items: list[dict] = []
    pending: list[dict] = []

    def flush(who=None, certainty=None):
        if pending:
            items.append({"kind": "child", "t": pending[0]["t"], "who": who, "certainty": certainty,
                          "text": " ".join(p["text"] for p in pending)})  # fmt: skip
            pending.clear()

    for e in events:
        t = e["type"]
        if t == "transcription" and (e.get("text") or "").strip():
            pending.append(e)
        elif t == "transcript_dropped":
            pending[:] = [p for p in pending if p["text"][:200] != e.get("text")]
        elif t == "turn":
            flush(e.get("speaker"), e.get("certainty"))
        elif t == "llm_response":
            raw = e.get("raw_text") or ""
            if not raw.strip():
                continue  # a tool call
            text, emotions = clean_reply(raw)
            if not text:
                if "silence" in TAG.findall(raw):
                    items.append({"kind": "pet", "t": e["t"], "text": "", "silent": True, "emotions": []})
                continue
            items.append({"kind": "pet", "t": e["t"], "text": text, "emotions": emotions})
        elif t == "sleepy_reply" and e.get("text"):
            items.append({"kind": "pet", "t": e["t"], "text": e["text"], "emotions": [], "asleep": True})
        elif t == "filler_spoken":
            items.append({"kind": "pet", "t": e["t"], "text": e.get("text", ""), "filler": True, "emotions": []})
        elif m := _marker(e, pet):
            items.append({"kind": "marker", "t": e["t"], "icon": m[0], "text": m[1]})
    flush()
    return items


# ---------------------------------------------------------------------------------------- sessions
def _sessions_dir() -> Path:
    return paths.logs_dir() / "sessions"


def sessions_of(day: date) -> list[tuple[str, list[dict]]]:
    files = sorted(_sessions_dir().glob(f"{day:%Y-%m-%d}_*.jsonl"))
    return [(p.name, session_log.read_session(p)) for p in files]


def _duration(events: list[dict]) -> float:
    end = next((e for e in reversed(events) if e["type"] == "session_end"), None)
    return float(end.get("duration_s") or 0) if end else float(events[-1]["s"] if events else 0)


def _hm(ts: str) -> str:
    return ts[11:16] if ts else ""


def day_digest(day: date, pet: Character) -> dict:
    """Who talked to the pet that day, how much, what they did (French texts)."""
    sessions = sessions_of(day)
    children: dict[str, dict] = {}
    activities, convs = [], []
    unknown = 0
    tired = False
    for name, events in sessions:
        items = thread(events, pet)
        lines = [i for i in items if i["kind"] == "child"]
        if not lines:
            continue  # opened and closed without a word
        who = sorted({i["who"] for i in lines if i.get("who")})
        convs.append({"name": name, "start": _hm(events[0]["t"]), "minutes": round(_duration(events) / 60),
                      "children": who, "messages": len(lines)})  # fmt: skip
        for i in lines:
            if not i.get("who"):
                unknown += 1
                continue
            c = children.setdefault(i["who"], {"name": i["who"], "messages": 0, "first": i["t"], "last": i["t"],
                                               "activities": []})  # fmt: skip
            c["messages"] += 1
            c["first"], c["last"] = min(c["first"], i["t"]), max(c["last"], i["t"])
        for e in events:
            m = _marker(e, pet)
            if m and e["type"] in ("guess_end", "story_end", "quiz_end", "learn_end", "yesno_end", "story_start"):
                if e["type"] == "story_start" and any(
                    x["type"] == "story_end" and x.get("title") == e.get("title") for x in events
                ):
                    continue  # the end says it better
                activities.append({"t": _hm(e["t"]), "icon": m[0], "text": m[1], "session": name})
                player = e.get("player")
                if player in children:
                    children[player]["activities"].append(m[1])
            if e["type"] == "energy_state" and e.get("state") in ("tired", "very_tired"):
                tired = True
    for c in children.values():
        c["first"], c["last"] = _hm(c["first"]), _hm(c["last"])
    return {
        "day": day.isoformat(),
        "conversations": convs,
        "minutes": sum(c["minutes"] for c in convs),
        "children": sorted(children.values(), key=lambda c: c["first"]),
        "unknown_messages": unknown,
        "activities": activities,
        "pet_got_tired": tired,
    }


def days_with_sessions(limit: int = 30) -> list[str]:
    days = sorted({p.name[:10] for p in _sessions_dir().glob("*.jsonl")}, reverse=True)
    return days[:limit]


# ---------------------------------------------------------------------------------------- review
def _id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _seen_file() -> Path:
    return paths.state_dir() / "parents_seen.json"


def seen() -> set[str]:
    try:
        return set(json.loads(_seen_file().read_text()))
    except (OSError, ValueError):
        return set()


def mark_seen(ids: list[str]):
    s = seen() | set(ids)
    _seen_file().parent.mkdir(parents=True, exist_ok=True)
    _seen_file().write_text(json.dumps(sorted(s)))


def review(memory_root: Path, pet: Character, today: date | None = None, days: int = REVIEW_DAYS) -> list[dict]:
    """Moments worth a parent's look, newest first: {"id", "kind", "t", "title", "detail", "session"}."""
    today = today or datetime.now().date()
    known = {normalize_text(p.stem) for p in (memory_root / DIR_FAMILY).glob("*.md")}
    flags: list[dict] = []

    def flag(kind, t, title, detail="", session="", key=""):
        flags.append({"id": _id(kind, session, key or t, title), "kind": kind, "t": t, "title": title,
                      "detail": detail, "session": session})  # fmt: skip

    for n in range(days):
        for name, events in sessions_of(today - timedelta(days=n)):
            items = thread(events, pet)
            last_child = ""
            silences = []
            for i in items:
                if i["kind"] == "child":
                    last_child = i["text"]
                    if SWEAR.search(normalize_text(i["text"])):
                        flag("swear", i["t"], f"Gros mot entendu ({i.get('who') or 'qui ?'})", f"« {i['text']} »",
                             name)  # fmt: skip
                elif i["kind"] == "pet":
                    if "triste" in i.get("emotions", []):
                        flag("sad", i["t"], "Moment triste", f"Enfant : « {last_child} »\n{pet.name} : « {i['text']} »",
                             name)  # fmt: skip
                    if i.get("silent") and last_child:
                        silences.append(last_child)
            if silences:
                flag("noise", events[0]["t"], f"{pet.name} n'a pas répondu {len(silences)} fois (bruit, télé, "
                     "autre conversation ?)", "\n".join(f"« {s} »" for s in silences[:5]), name)  # fmt: skip
            for e in events:
                t = e["type"]
                if t == "speaker" and e.get("name") and normalize_text(e["name"]) not in known:
                    flag("name", e["t"], f"Prénom entendu sans fiche : {e['name']}",
                         "Mal entendu, ou un nouvel enfant ?", name, e["name"])  # fmt: skip
                elif t == "speaker_to_confirm":
                    like = f" (ressemble à {e['resembles']})" if e.get("resembles") else ""
                    flag("name", e["t"], f"Prénom à confirmer : {e.get('name')}{like}", "", name, e.get("name"))
                elif t == "guess_win_refused":
                    flag("game", e["t"], f"Victoire de {pet.name} refusée à la devinette",
                         f"Il disait « {e.get('answer')} », l'enfant : « {e.get('child')} »", name)  # fmt: skip
                elif t == "guess_end" and not e.get("found") and (e.get("questions") or 0) < 5:
                    flag("game", e["t"], f"Devinette abandonnée tôt ({e.get('questions')} questions)",
                         "Vérifie la réponse enregistrée dans Mémoire → jeux/devine.json.", name)  # fmt: skip
                elif t == "turn_abandoned":
                    flag(
                        "failure", e["t"], f"{pet.name} n'a pas réussi à répondre", "Panne ou lenteur du modèle.", name
                    )
    # Confidences live in the cards, not in the logs.
    for p in sorted((memory_root / DIR_FAMILY).glob("*.md")):
        meta, sec = parse_card(p.read_text())
        for card_items in sec.values():
            for item in card_items:
                if normalize_text(item).startswith("confidence"):
                    flag("confidence", "", f"Confidence de {meta.get('prenom', p.stem)}", item,
                         key=f"{p.name}:{item}")  # fmt: skip
    done = seen()
    for f in flags:
        f["seen"] = f["id"] in done
    flags.sort(key=lambda f: f["t"], reverse=True)  # newest first…
    return sorted(flags, key=lambda f: f["seen"])  # …not seen yet before seen (stable)


# ---------------------------------------------------------------------------------------- memory edits
EDITABLE_JSON = {
    "jeux/devine.json": "games",
    "histoires/univers.json": "universe",
    "jeux/quiz.json": "quiz",
    "jeux/ni_oui_ni_non.json": "yesno",
    "jeux/apprendre.json": "learn",
}


def safe_path(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents or not p.is_file() or ".git" in p.parts:
        raise ValueError("fichier inconnu")
    return p


def structured(root: Path, rel: str) -> dict:
    """A memory file as data the page can edit item by item."""
    p = safe_path(root, rel)
    rel = p.relative_to(root.resolve()).as_posix()  # « famille/../MA_VIE.md » is MA_VIE.md
    if rel in EDITABLE_JSON:
        return {"kind": EDITABLE_JSON[rel], "path": rel, "data": json.loads(p.read_text())}
    if p.suffix == ".md" and (rel.startswith(DIR_FAMILY + "/") or rel == LIFE_FILE):
        meta, sections = parse_card(p.read_text())
        return {"kind": "card" if rel != LIFE_FILE else "life", "path": rel, "meta": meta,
                "sections": {k: v for k, v in sections.items() if v}}  # fmt: skip
    return {"kind": "text", "path": rel, "text": p.read_text()}


def edit(mem: Memory, rel: str, change: dict) -> dict:
    """Apply one parent correction and commit it. `change`:
    - cards and « Ma vie »: {"section", "index", "expected", "text"} (text None or "" = delete)
    - jeux/devine.json: {"player", "index", "answer"?, "found"?, "delete"?}
    - histoires/univers.json: {"character", "delete": true} or {"story", "delete": true}
    - jeux/quiz.json, jeux/ni_oui_ni_non.json: {"player", "index", "delete": true} (records recomputed)
    - jeux/apprendre.json: {"player", "index", "delete": true} (a lesson) or {"player", "question", "delete": true}
    """
    root = mem.root
    p = safe_path(root, rel)
    rel = p.relative_to(root.resolve()).as_posix()  # checks on the real path, not on « a/../b »
    gone: str | None = None  # text a parent removed or rewrote: out of the running conversation too
    with mem._files_lock, lock_for(p):  # the games lock their JSON file the same way
        if rel in EDITABLE_JSON:
            data = json.loads(p.read_text(encoding="utf-8"))
            what = _edit_json(EDITABLE_JSON[rel], data, change)
            atomic_write_json(p, data)
        elif p.suffix == ".md" and (rel.startswith(DIR_FAMILY + "/") or rel == LIFE_FILE):
            meta, sections = parse_card(p.read_text())
            items = sections.get(str(change.get("section", "")), [])
            i = int(change.get("index", -1))
            if not (0 <= i < len(items)) or items[i] != change.get("expected"):
                raise ValueError("la fiche a changé entre-temps : recharge la page")
            new = (change.get("text") or "").strip()
            what = f"« {items[i]} » → « {new} »" if new else f"« {items[i]} » retiré"
            gone = items[i]
            # A condensation running now must not write it back.
            mem.forget_pending(gone, None if rel == LIFE_FILE else meta.get("prenom", p.stem))
            if new:
                items[i] = new
            else:
                items.pop(i)
            if rel == LIFE_FILE:
                atomic_write_text(p, render_life(sections, mem.pet_name))
            else:
                atomic_write_text(p, render_card(meta, sections))
        elif rel == SUMMARY_FILE and isinstance(change.get("text"), str):
            atomic_write_text(p, change["text"].rstrip() + "\n")
            what = "résumé réécrit"
        else:
            raise ValueError("ce fichier ne se corrige pas ici")
        # Removed or rewritten, the old text must not stay readable: not in older versions, not in
        # the commit message.
        mem.wipe_history(f"Correction par un parent ({rel}), anciennes versions effacées")
    res: dict = {"ok": True, "change": what}
    if gone:
        res["gone"] = gone
    return res


def _edit_json(kind: str, data: dict, c: dict) -> str:
    if kind == "games":
        games = data.get("players", {}).get(c.get("player"), {}).get("games", [])
        i = int(c.get("index", -1))
        if not 0 <= i < len(games):
            raise ValueError("partie introuvable")
        g = games[i]
        if c.get("delete"):
            games.pop(i)
            return f"partie du {g.get('date', '')[:10]} de {c['player']} retirée"
        if "answer" in c:
            g["answer"] = str(c["answer"]).strip()
        if "found" in c:
            g["found"] = bool(c["found"])
        return f"partie du {g.get('date', '')[:10]} de {c['player']} : {g['answer']}, trouvé={g['found']}"
    if kind == "universe":
        if c.get("character") and c.get("delete"):
            removed = data.get("characters", {}).pop(c["character"], None)
            if not removed:
                raise ValueError("personnage introuvable")
            return f"personnage {removed.get('name')} retiré"
        if "story" in c and c.get("delete"):
            stories = data.get("stories", [])
            i = int(c["story"])
            if not 0 <= i < len(stories):
                raise ValueError("histoire introuvable")
            return f"histoire « {stories.pop(i).get('title')} » retirée"
    if kind in ("quiz", "yesno", "learn") and c.get("delete"):
        p = data.get("players", {}).get(c.get("player"))
        if p is None:
            raise ValueError("enfant introuvable")
        if kind == "learn" and "question" in c:
            qs = p.get("open_questions", [])
            i = int(c["question"])
            if not 0 <= i < len(qs):
                raise ValueError("question introuvable")
            return f"question « {qs.pop(i)} » de {c['player']} retirée"
        items = p.get("topics" if kind == "learn" else "games", [])
        i = int(c.get("index", -1))
        if not 0 <= i < len(items):
            raise ValueError("élément introuvable")
        gone = items.pop(i)
        _recompute(kind, p, gone)
        label = gone.get("title") or gone.get("topic") or gone.get("theme") or "partie"
        return f"{label} du {gone.get('date', '')[:10]} de {c['player']} retiré(e)"
    raise ValueError("modification inconnue")


def _recompute(kind: str, p: dict, gone: dict):
    """Keep a child's records consistent after a parent removed a game or a lesson."""
    from aipet.games.common import theme_key

    if kind == "quiz":
        best: dict[str, dict] = {}
        for g in p.get("games", []):
            key = theme_key(g.get("theme", ""))
            if key not in best or g.get("score", 0) > best[key]["score"]:
                best[key] = {"theme": g.get("theme"), "score": g.get("score", 0), "total": g.get("total", 0)}
        p["best"] = best
    elif kind == "yesno":
        p["best"] = max((g.get("held", 0) for g in p.get("games", [])), default=0)
    elif kind == "learn":
        key = theme_key(gone.get("domain", ""))
        dom = p.get("domains", {}).get(key)
        if dom:
            dom["lessons"] = max(0, dom.get("lessons", 1) - 1)
        name = gone.get("domain")
        if name in p.get("interests", {}):
            p["interests"][name] = max(0, p["interests"][name] - 1)
            if not p["interests"][name]:
                del p["interests"][name]
