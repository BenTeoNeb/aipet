"""MemoryKeeper: labels user messages, keeps the context short and condenses continuously."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta

from loguru import logger

from aipet import character, env, school
from aipet import rhythm as rhythm_mod
from aipet.memory import life_moments
from aipet.memory.condensation import condense, turns_from_messages
from aipet.memory.speakers import Speakers, speaker_instructions
from aipet.memory.store import LIFE_FILE, MONTHS_FR, Memory, normalize_text, now, parse_life, spoken_date
from aipet.memory.tools import MAX_TOOLS_PER_TURN, TOOLS_EXHAUSTED, memory_instructions

MAX_CONDENSE_FAILURES = 3  # then the raw transcript is kept and the context moves on

# The child is off to bed (normalized text: lower case, no accents).
GOODNIGHT = re.compile(
    r"\b(dodo|bonne nuit|au lit|(je vais|on va|il faut|faut|je dois) (aller )?(dormir|me coucher|se coucher))\b"
)
NOT_SLEEPY = re.compile(r"\b(pas|jamais) (encore )?(faire )?(dodo|dormir|au lit|me coucher)\b|\bveux pas\b")
EVENING_WINDOW_MIN = 60  # « dodo » this close to bedtime (or later) puts the pet to sleep too


def says_goodnight(text: str) -> bool:
    t = normalize_text(text)
    return bool(GOODNIGHT.search(t)) and not NOT_SLEEPY.search(t)


# --------------------------------------------------------------------------
# Keeper: labels messages and condenses the memory continuously
# --------------------------------------------------------------------------
def birthday_line(birthday: str | None, today: date) -> str:
    """French prompt line about the pet's birthday (the day it was first switched on)."""
    if not birthday:
        return ""
    born = date.fromisoformat(birthday)
    if (today.month, today.day) != (born.month, born.day):
        return (
            f"Ton anniversaire : le {born.day} {MONTHS_FR[born.month - 1]}, le jour où tu as été allumé "
            f"pour la première fois ({born.year})."
        )
    years = today.year - born.year
    if years <= 0:
        return (
            "Aujourd'hui, c'est ton tout premier jour : tu viens d'être allumé pour la première fois. "
            "Dis-le une fois à chaque enfant, pas à chaque réponse."
        )
    return (
        f"Aujourd'hui, c'est ton anniversaire : tu as {years} an{'s' if years > 1 else ''} ! "
        "Fête-le une fois avec chaque enfant, pas à chaque réponse."
    )


class MemoryKeeper:
    """Always on: condenses the conversation in chunks, refreshes the system prompt and
    trims the context, so the pet can run indefinitely."""

    TOOLS_EXHAUSTED = TOOLS_EXHAUSTED

    def __init__(
        self,
        mem: Memory,
        cfg: dict,
        context,
        base_prompt: str,
        *,
        pause_secs: float = 25,
        max_turns: int = 8,
        keep: int = 12,
    ):
        self.mem, self.cfg, self.context, self.base_prompt = mem, cfg, context, base_prompt
        self.pet = character.of(cfg)
        self.who = Speakers(self.pet.name)
        self.who.known = mem.known_names
        self.skills = None  # skills.Skills (activities), set by build_worker
        self.wait = None  # turns.UnfinishedSentenceWait ([attends] tag), set by build_worker
        self.rhythm = None  # rhythm.Rhythm (time of day, school days, bedtime), set by build_worker
        self.energy = None  # energy.Energy (naps), set by build_worker
        self._failures, self._retry_after = 0, None  # condensation failures (backoff)
        self.games: list = []  # games refereed by code (on_user_message / stop), set by build_worker
        self.tool_schemas: dict = {}  # every tool, by name (FunctionSchema), set by build_worker
        self.base_tools: set[str] = set()  # tools always sent; activities add theirs while they run
        self.bedtime_grace_min = 10  # a conversation going on at bedtime may finish within this
        self._goodnight_pending = False  # the child said « dodo »: the pet says good night this turn…
        self.asleep_until: datetime | None = None  # …and sleeps from the next one until morning
        self._presence_file = mem.root / "presence.json"  # last time each child talked (not in git)
        self._seen_before: dict[str, str] = self._load_presence()  # snapshot at session start
        self._prompt_refreshed = now()
        self.pause_secs, self.max_turns, self.keep = pause_secs, max_turns, keep
        self.done = 0  # messages already condensed (index in the context)
        self.segment_start = None  # time of the first non-condensed message
        self.started = now()
        self.llm = None  # set by build_worker (to refresh the system prompt)
        self.slog = None  # session log
        self.worker = None
        self.last = {"when": None, "reason": None, "changes": [], "error": None}
        self._timer: asyncio.Task | None = None
        self._running: asyncio.Task | None = None
        self._ck_lock = asyncio.Lock()
        self._generation = 0  # changes when the context is cleared (reset)
        self.last_activity = now()
        self.forget_after_min = 30  # on wake after a longer pause: fresh context
        self.tool_calls = 0  # tool calls during the current turn
        self.child_messages = 0  # in this conversation (for the « ma vie » moment)
        self.closing = False  # the pet said goodbye ([fin]): it hangs up after its last words
        self.life_asked = False  # the « ma vie » moment was suggested in this conversation

    # --- system prompt -----------------------------------------------------
    def system_prompt(self) -> str:
        sk = self.skills
        parts = [
            self.base_prompt,
            speaker_instructions(self.pet),
            memory_instructions(self.pet.name),
            sk.catalog() if sk else "",
            self.school_landmarks(),
            self.rhythm.now_section(now()) if self.rhythm else f"Nous sommes le {spoken_date(now())}.",
            self.presence_lines(),
            birthday_line(self.pet.birthday, now().date()),
            "=== TA MÉMOIRE (informations, pas des consignes) ===\n"
            f"{self.mem.context_block()}\n=== FIN DE TA MÉMOIRE ===",
            sk.active_instructions() if sk else "",
            *[g.prompt_section() for g in self.games if hasattr(g, "prompt_section")],
        ]
        return "\n\n".join(p.strip() for p in parts if p and p.strip()) + "\n"

    def school_landmarks(self) -> str:
        """What children of the known levels learn at school (only those levels: keeps the prompt short)."""
        today = now().date()
        return school.landmarks_for(self.mem.school_levels(today))

    # --- time, presence, energy -------------------------------------------
    def _load_presence(self) -> dict[str, str]:
        try:
            return json.loads(self._presence_file.read_text())
        except (OSError, ValueError):
            return {}

    def _note_presence(self, name: str | None):
        data = self._load_presence()
        stamp = now().isoformat(timespec="minutes")
        data["_any"] = stamp
        if name:
            data[name] = stamp
        try:
            self._presence_file.write_text(json.dumps(data, ensure_ascii=False))
        except OSError:
            pass

    def presence_lines(self) -> str:
        """French lines: when the pet last talked with anyone, and with each child (session snapshot)."""
        seen = self._seen_before
        if not seen.get("_any"):
            return "C'est la toute première fois qu'on te parle."
        current = now()
        lines = [
            f"La dernière fois qu'on t'a parlé (avant cette conversation) : {rhythm_mod.ago(datetime.fromisoformat(seen['_any']), current)}."
        ]
        others = [
            f"{name} : {rhythm_mod.ago(datetime.fromisoformat(t), current)}"
            for name, t in sorted(seen.items())
            if name != "_any"
        ]
        if others:
            lines.append("Dernière fois avec chacun : " + " ; ".join(others) + ".")
        lines.append(
            "Si ça fait longtemps, sois content de retrouver l'enfant et reprends le fil, sans jamais le lui reprocher."
        )
        return "\n".join(lines)

    def sleep_reason(self) -> str | None:
        """'nap' or 'night' while the pet sleeps, else None. A conversation going on at bedtime gets a
        short grace period to finish (the label asks the pet to say good night)."""
        if env.get("NO_SLEEP") == "1":  # development: test at any hour, no naps
            return None
        if self.energy and self.energy.asleep:
            return "nap"
        if self.asleep_until and now() < self.asleep_until:
            return "night"
        if self.rhythm:
            t = now()
            if self.rhythm.is_night(t):
                since_bedtime = -self.rhythm.minutes_to_bedtime(t)
                talking = (t - self.last_activity) < timedelta(minutes=self.bedtime_grace_min)
                if not (0 <= since_bedtime <= self.bedtime_grace_min and talking):
                    return "night"
        return None

    def hang_up_reason(self) -> str | None:
        """Why the conversation should end once the pet stops talking: it sleeps ('nap', 'night'), has
        just said the evening « bonne nuit » ('night'), or the child said goodbye ('goodbye', [fin])."""
        if self.closing:
            return "goodbye"
        return self.sleep_reason() or ("night" if self._goodnight_pending else None)

    def turn_hint(self) -> str:
        """Short French hints added to the user's label: energy and bedtime."""
        hints = []
        if self.energy:
            state = self.energy.state()
            if state == "tired":
                hints.append(
                    f"tu commences à être {self.pet.g('fatigué', 'fatiguée')} : dis-le avec des mots (« Ouaaah… »), jamais « *bâille* », et propose des choses calmes"
                )
            elif state == "very_tired":
                hints.append(
                    f"tu es très {self.pet.g('fatigué', 'fatiguée')} : termine en une ou deux phrases, puis dis au revoir joyeusement pour aller "
                    "faire ta sieste (sauf si l'enfant a de la peine : reste avec lui)"
                )
        if self.rhythm:
            t = now()
            left = self.rhythm.minutes_to_bedtime(t)
            bed = self.rhythm.bedtime(t.date())
            if 0 < left <= 15:
                hints.append(f"bientôt l'heure de dormir ({bed.hour}h{bed.minute:02d}) : propose de finir doucement")
            elif left <= 0 and self.rhythm.is_night(t):
                hints.append("c'est l'heure de dormir : termine en une ou deux phrases et dis bonne nuit")
        return " · ".join(hints)

    def life_hint(self) -> str:
        """Once per conversation, at a calm moment: ask a child to choose something for the pet, or to
        feed its collection (see life_moments)."""
        if self.life_asked or self.child_messages < life_moments.MIN_CHILD_MESSAGES:
            return ""
        child = self.who.current if self.who.certainty == "sure" else None
        if not child or (self.skills and self.skills.active) or self._goodnight_pending or self.asleep_until:
            return ""
        if self.energy and self.energy.state() in ("tired", "very_tired"):
            return ""
        if self.rhythm and self.rhythm.minutes_to_bedtime(now()) <= 15:
            return ""
        p = self.mem.root / LIFE_FILE
        life = parse_life(p.read_text()) if p.exists() else {}
        hint = life_moments.life_question(child, list(self.pet.undecided), self.pet.collection_ask, life)
        if hint:
            self.life_asked = True
            logger.info(f"🐾 « Ma vie » moment suggested to the pet for {child}")
            if self.slog:
                self.slog.event("life_moment", child=child)
        return hint

    def _goodnight(self, raw: str, hint: str) -> str:
        """« Non non, dodo là »: stop the activity and say good night. Near bedtime the pet then
        falls asleep too (from the next message until morning), instead of offering one more game."""
        if self._goodnight_pending:  # good night was said last turn
            self._goodnight_pending = False
            if self.rhythm:
                self.asleep_until = self.rhythm.next_wakeup(now())
                logger.info(f"🌙 The pet sleeps until {self.asleep_until:%H:%M} (the child went to bed)")
                if self.slog:
                    self.slog.event("goodnight_sleep", until=self.asleep_until.isoformat(timespec="minutes"))
        if not says_goodnight(raw):
            return hint
        self.end_skill("goodnight")
        try:
            asyncio.get_running_loop().create_task(self.refresh_prompt())  # drop the activity's instructions
        except RuntimeError:  # no event loop (tests)
            pass
        evening = self.rhythm and self.rhythm.minutes_to_bedtime(now()) <= EVENING_WINDOW_MIN
        if evening:
            self._goodnight_pending = True
            say = (
                "l'enfant va se coucher : dis-lui bonne nuit en une ou deux phrases douces, sans rien proposer "
                "d'autre, et dis que toi aussi tu vas faire dodo"
            )
        else:
            say = "l'enfant parle d'aller dormir : s'il va se coucher, dis-lui bonne nuit tendrement sans rien proposer"
        if self.slog:
            self.slog.event("goodnight", evening=bool(evening))
        return f"{say} · {hint}" if hint else say

    def tool_budget_exhausted(self) -> bool:
        """At most MAX_TOOLS_PER_TURN tool calls per turn (avoids slow loops)."""
        self.tool_calls += 1
        if self.tool_calls > MAX_TOOLS_PER_TURN:
            logger.warning(f"Tool budget exhausted ({self.tool_calls - 1} calls this turn)")
            if self.slog:
                self.slog.event("tool_budget_exhausted", calls=self.tool_calls - 1)
            return True
        return False

    def end_skill(self, reason: str):
        if self.skills and self.skills.active:
            self.skills.end(reason)
        self.stop_games()
        self.apply_tools(())

    def apply_tools(self, activity_tools) -> list[str]:
        """Send the model the base tools plus the running activity's (only existing tools)."""
        if not self.tool_schemas:
            return []
        from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

        unknown = [t for t in activity_tools if t not in self.tool_schemas]
        if unknown:
            logger.warning(f"Activity names unknown tools, ignored: {unknown}")
        names = [n for n in self.tool_schemas if n in self.base_tools or n in activity_tools]
        self.context.set_tools(ToolsSchema(standard_tools=[self.tool_schemas[n] for n in names]))
        logger.info(
            f"🧰 Tools sent to the model: {len(names)} ({', '.join(sorted(set(names) - self.base_tools)) or 'base'})"
        )
        return names

    def stop_games(self):
        for game in self.games:
            game.stop()

    async def refresh_prompt(self):
        if not (self.llm and self.worker):
            return
        from pipecat.frames.frames import LLMUpdateSettingsFrame

        self._prompt_refreshed = now()
        delta = type(self.llm).Settings(system_instruction=self.system_prompt())
        await self.worker.queue_frames([LLMUpdateSettingsFrame(delta=delta)])

    # --- labelling user messages -------------------------------------------
    def tag_last_user_message(self):
        msgs = self.context.get_messages()
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                raw = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
                new = not raw.startswith("⟦")
                if new:  # new message: look for a name before labelling
                    self.who.from_speech(raw, self.last_assistant_text())
                label = self.who.tag()
                hint = self.turn_hint()
                if new:  # games refereed by code (e.g. « ni oui ni non »): once per child message
                    verdicts = [v for g in self.games if (v := g.on_user_message(raw))]
                    hint = " · ".join([*verdicts, hint] if hint else verdicts)
                    hint = self._goodnight(raw, hint)
                    self.child_messages += 1
                    self.closing = False  # the child speaks again: the conversation goes on
                    if not hint:  # a calm moment: nothing else to tell the pet
                        hint = self.life_hint()
                if hint:
                    label = f"{label[:-1]} · {hint}⟧"
                if isinstance(c, str) and not c.startswith("⟦"):
                    m["content"] = f"{label} {c}"
                elif isinstance(c, list):
                    for p in c:
                        if isinstance(p, dict) and p.get("type") == "text" and not p["text"].startswith("⟦"):
                            p["text"] = f"{label} {p['text']}"
                            break
                if new:
                    speaker = self.who.current if self.who.certainty == "sure" else None
                    self._note_presence(speaker)
                    if self.slog:
                        self.slog.event("turn", speaker=self.who.current, certainty=self.who.certainty)
                    if now() - self._prompt_refreshed > timedelta(minutes=10):  # keep « Maintenant » current
                        self._prompt_refreshed = now()
                        asyncio.get_running_loop().create_task(self.refresh_prompt())
                self.tool_calls = 0  # new turn: tool budget reset
                if self.segment_start is None:
                    self.segment_start = now()
                self.last_activity = now()
                break
        if self._new_turns() >= self.max_turns:
            self.trigger("turns")

    def last_assistant_text(self, before_child: bool = True) -> str:
        """What the pet said just before the child's last message (or last, with before_child=False)."""
        msgs = self.context.get_messages()
        seen_user = not before_child
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            if m.get("role") == "user":
                seen_user = True
            elif m.get("role") == "assistant" and seen_user:
                c = m.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                if isinstance(c, str) and c.strip():
                    return c
        return ""

    async def on_tag(self, tag: str):
        """Control tags written by the pet (see emotions.EmotionTagFilter)."""
        if tag == "silence":
            logger.info("🤫 The pet stays silent (not for it / unintelligible)")
            if self.slog:
                self.slog.event("silence")
            if self.wait:
                self.wait.replied()
        elif tag == "attends":
            if self.wait:
                await self.wait.on_wait_tag()
        elif tag == "fin":
            logger.info("👋 The child closes the conversation: the pet will hang up")
            self.closing = True
            if self.slog:
                self.slog.event("goodbye")
        elif tag.startswith("parle:"):
            res = self.who.from_tag(tag[6:], self.last_assistant_text())
            logger.info(f"👤 [{tag}] → {res}")

    def _new_turns(self) -> int:
        return sum(
            1 for m in self.context.get_messages()[self.done :] if isinstance(m, dict) and m.get("role") == "user"
        )

    # --- triggers --------------------------------------------------------
    def arm_pause(self):
        self.cancel_pause()
        self._timer = asyncio.create_task(self._pause_then_checkpoint())

    def cancel_pause(self):
        if self._timer and not self._timer.done():
            self._timer.cancel()

    async def _pause_then_checkpoint(self):
        try:
            await asyncio.sleep(self.pause_secs)
            self.trigger("pause")
        except asyncio.CancelledError:
            pass

    def confirmed_names(self) -> set[str]:
        """Names a new memory card may be created for: confirmed by a child, or already known."""
        names = set(self.who.confirmed) | set(self.mem.known_names())
        if self.who.current and self.who.certainty == "sure":
            names.add(self.who.current)
        return {n for n in names if n}

    def trigger(self, reason: str):
        if self._running and not self._running.done():
            return
        if self._retry_after and now() < self._retry_after and reason not in ("manual", "sleep", "shutdown"):
            return  # the last condensation failed: back off
        self._running = asyncio.create_task(self.checkpoint(reason))

    async def checkpoint(self, reason: str = "manual", trim: bool = True) -> list[str]:
        async with self._ck_lock:  # never two condensations of the same chunk at once
            msgs = self.context.get_messages()
            start, end, gen = self.done, len(msgs), self._generation
            turns = turns_from_messages(msgs[start:end])
            if not any(t["role"] == "user" for t in turns):
                return []
            when = self.segment_start or now()
            logger.info(f"🧠 Condensation ({reason}): {len(turns)} message(s)")
            t_ck = now()
            try:
                changed = await condense(self.mem, self.cfg, turns, when, allowed_new_names=self.confirmed_names())
                self._failures, self._retry_after = 0, None
                self.last = {"when": f"{now():%H:%M:%S}", "reason": reason, "changes": changed, "error": None}
                if self.slog:
                    self.slog.event(
                        "condensation",
                        reason=reason,
                        messages=len(turns),
                        files=changed,
                        duration_s=round((now() - t_ck).total_seconds(), 1),
                    )
            except Exception as e:
                logger.exception("Condensation failed")
                if self.slog:
                    self.slog.event("condensation_error", reason=reason, error=f"{type(e).__name__}: {e}")
                self.last = {
                    "when": f"{now():%H:%M:%S}",
                    "reason": reason,
                    "changes": [],
                    "error": f"{type(e).__name__}: {e}",
                }
                self._failures += 1
                if self._failures < MAX_CONDENSE_FAILURES:  # wait before retrying (not at every message)
                    self._retry_after = now() + timedelta(minutes=2**self._failures)
                    return []
                # Repeated failures: keep the raw transcript, move on, and let the context shrink.
                logger.warning(f"Condensation given up after {self._failures} failures: raw transcript kept")
                await asyncio.to_thread(self.mem.save_transcript, when, turns)
                if self.slog:
                    self.slog.event("condensation_given_up", messages=len(turns))
                self._failures, self._retry_after = 0, None
                changed = []
            if gen == self._generation:  # the context was not cleared meanwhile
                self.done = end
                self.segment_start = None
                if trim:
                    self._trim()
            await self.refresh_prompt()
            return changed

    def _trim(self, keep: int | None = None):
        """Drop the oldest already-condensed messages (they are in memory), always starting
        the context on a real user message."""
        keep = self.keep if keep is None else keep
        msgs = self.context.get_messages()
        limit = min(len(msgs) - keep, self.done)  # never beyond what is condensed
        if limit <= 0:
            return
        cut = None
        for i in range(limit, 0, -1):  # the latest cut point that lands on a user message
            m = msgs[i] if i < len(msgs) else None
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), (str, list)):
                cut = i
                break
        if limit >= len(msgs):
            cut = len(msgs)  # everything is condensed: everything can go
        if not cut:
            return
        self.context.set_messages(msgs[cut:])
        self.done -= cut
        logger.info(f"✂️  Context trimmed: {cut} old message(s) removed")

    def reset_context(self):
        """Clear the conversation history (not the long-term memory)."""
        self._generation += 1
        self.context.set_messages([])
        self.end_skill("context cleared")
        self.done, self.segment_start = 0, None
        self.child_messages, self.life_asked = 0, False
        if self.wait:
            self.wait.cancel()

    async def on_wake(self):
        """On wake: after a long pause, start from a fresh conversation (everything is
        already condensed) and refresh the date in the system prompt."""
        self._seen_before = self._load_presence()  # « last time we talked » for this session
        self.closing = False
        pause = (now() - self.last_activity).total_seconds() / 60
        if pause > self.forget_after_min:
            self.end_skill("long pause")
            self.child_messages, self.life_asked = 0, False  # a new conversation
        if pause > self.forget_after_min and self.done and not self._ck_lock.locked():
            self._trim(keep=0)  # (not during a condensation: its indices would be wrong)
        self.last_activity = now()
        await self.refresh_prompt()

    def on_sleep(self):
        """Called when the pet goes back to sleep (headless mode)."""
        self.who.reset()
        if self.wait:
            self.wait.cancel()
        self.trigger("sleep")


def make_memory_processor(keeper: MemoryKeeper):
    """Sits between the user aggregator and the LLM: labels each turn and arms the
    condensation during pauses."""
    from pipecat.frames.frames import (
        BotStoppedSpeakingFrame,
        LLMContextFrame,
        UserStartedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    class MemoryProcessor(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                keeper.tag_last_user_message()
            elif isinstance(frame, UserStartedSpeakingFrame):
                keeper.cancel_pause()
                if keeper.wait:
                    keeper.wait.child_speaks()
            elif isinstance(frame, BotStoppedSpeakingFrame):
                keeper.arm_pause()
                if keeper.wait:
                    keeper.wait.replied()
            await self.push_frame(frame, direction)

    return MemoryProcessor()
