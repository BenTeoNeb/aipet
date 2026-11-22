"""Variety: what the pet suggests when nothing is going on.

Left alone, the model always offered « une histoire ou une devinette ? »: what the children liked
is in its memory, they pick it, and the memory says they like it even more. Here the code chooses:
for each child, the activity not played for the longest time (never played first, then the least
offered), next to their favourite. The pet gets it as a hint at the greeting, after an activity
ends, and on the next idle turns.

Record (family memory, `jeux/activites.json`):
    {"players": {"Zina": {"story": {"last": "2026-11-21", "count": 6, "offered": 0}, …}}}
`offered` counts the suggestions since the activity was last played: a suggestion the children
keep declining gives way to another one. Bootstrapped once from the session logs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from loguru import logger

from aipet.fileio import atomic_write_json, load_json, lock_for
from aipet.memory.cards import DIR_GAMES
from aipet.session_log import NO_LOG

FILE = "activites.json"
FAMILY = "*"  # nobody known for sure: the whole family's history


class ActivityHistory:
    def __init__(self, memory_root: Path, sessions_dir: Path | None = None, slog=None):
        self.path = Path(memory_root) / DIR_GAMES / FILE
        self.slog = slog or NO_LOG
        if not self.path.exists() and sessions_dir:
            self._bootstrap(Path(sessions_dir))

    # ---------------------------------------------------------------- record
    def _load(self) -> dict:
        data = load_json(self.path, None)
        return data if isinstance(data, dict) and isinstance(data.get("players"), dict) else {"players": {}}

    def _save(self, data: dict):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
        except OSError as e:
            logger.warning(f"Activity history not saved: {e}")

    def played(self, player: str | None, skill: str, day: date):
        """An activity starts (for `player`, or the family when unknown)."""
        with lock_for(self.path):
            data = self._load()
            rec = data["players"].setdefault(player or FAMILY, {}).setdefault(skill, {})
            rec.update(last=day.isoformat(), count=rec.get("count", 0) + 1, offered=0)
            self._save(data)

    def _offered(self, player: str | None, skill: str):
        with lock_for(self.path):
            data = self._load()
            rec = data["players"].setdefault(player or FAMILY, {}).setdefault(skill, {})
            rec["offered"] = rec.get("offered", 0) + 1
            self._save(data)

    def _merged(self, player: str | None) -> dict[str, dict]:
        """One child's record; the family's (everyone merged) when nobody is known."""
        players = self._load()["players"]
        if player and player != FAMILY:
            return players.get(player, {})
        out: dict[str, dict] = {}
        for recs in players.values():
            for skill, r in recs.items():
                m = out.setdefault(skill, {"last": "", "count": 0, "offered": 0})
                m["last"] = max(m["last"], r.get("last", ""))
                m["count"] += r.get("count", 0)
                m["offered"] = max(m["offered"], r.get("offered", 0))
        return out

    # ---------------------------------------------------------------- choice
    def suggest(self, player: str | None, skills: list[str], day: date) -> dict | None:
        """{"new": skill, "since": days or None (never), "favourite": skill or None}, or None."""
        if not skills:
            return None
        recs = self._merged(player)
        played = [s for s in skills if recs.get(s, {}).get("count")]
        favourite = max(played, key=lambda s: (recs[s]["count"], recs[s].get("last", ""))) if played else None
        others = [s for s in skills if s != favourite] or skills

        def key(s: str):
            r = recs.get(s, {})
            return (r.get("last", ""), r.get("offered", 0), skills.index(s))  # never played first

        new = min(others, key=key)
        last = recs.get(new, {}).get("last")
        since = (day - date.fromisoformat(last)).days if last else None
        return {"new": new, "since": since, "favourite": favourite}

    def offer(self, player: str | None, skills: dict[str, str], day: date, *, record: bool = True) -> str:
        """The French hint for the pet (`skills`: name → short spoken label), or "". `record`: count
        it as offered (once per idle moment, not at each turn)."""
        s = self.suggest(player, list(skills), day)
        if not s:
            return ""
        who = player if player and player != FAMILY else "les enfants"
        new = skills[s["new"]]
        why = "jamais fait" if s["since"] is None else f"pas fait depuis {s['since']} jours" if s["since"] > 1 else ""
        line = f"si tu proposes une activité, propose d'abord {new}" + (f" ({why} avec {who})" if why else "")
        if s["favourite"]:
            loves = "adorent" if who == "les enfants" else "adore"
            line += f", ou bien {skills[s['favourite']]} que {who} {loves}"
        line += " (varie : pas toujours les mêmes propositions)"
        if record:
            self._offered(player, s["new"])
            self.slog.event("offer", child=player, new=s["new"], since=s["since"], favourite=s["favourite"])
        return line

    # ---------------------------------------------------------------- bootstrap
    def _bootstrap(self, sessions: Path):
        """First run: rebuild the record from the session logs (who was talking at each start)."""
        data: dict = {"players": {}}
        for f in sorted(sessions.glob("*.jsonl")):
            speaker = None
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "turn":
                    speaker = e.get("speaker") if e.get("certainty") == "sure" else None
                elif e.get("type") == "skill_start" and e.get("name"):
                    day = str(e.get("t", ""))[:10]
                    rec = data["players"].setdefault(speaker or FAMILY, {}).setdefault(e["name"], {})
                    rec.update(last=max(rec.get("last", ""), day), count=rec.get("count", 0) + 1, offered=0)
        if data["players"]:
            logger.info(f"🎲 Activity history rebuilt from the logs: {sorted(data['players'])}")
            self._save(data)
