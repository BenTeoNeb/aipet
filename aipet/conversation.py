"""The conversation: what the pet knows and decides between the child's words and the model.

`Conversation` coordinates small parts, each owning one concern:

- `who` (memory.speakers.Speakers): who is talking;
- `sleep` (sleep.SleepState): naps, night, good night, goodbye;
- `presence` (presence.Presence): when each child last talked;
- `tools` (tool_gate.ToolGate): which tools the model gets, and the per-turn budget;
- `life` (memory.life_moments.LifeMoments): the « ma vie » moments;
- `memory` (memory.keeper.MemoryKeeper): condensation into long-term memory, context trimming;
- `skills` and `games`: the activities.

It labels each child message (speaker and short hints for the pet), assembles the system prompt
and keeps it current, and reacts to the control tags the pet writes. It is also the `Host` the
games see.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    LLMContextFrame,
    LLMUpdateSettingsFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

from aipet import character, school
from aipet.games.common import Activity, Host
from aipet.memory.forget import scrub_value
from aipet.memory.keeper import MemoryKeeper
from aipet.memory.life_moments import LifeMoments
from aipet.memory.speakers import Speakers, speaker_instructions
from aipet.memory.store import Memory
from aipet.memory.tools import memory_instructions
from aipet.presence import Presence
from aipet.rhythm import Rhythm
from aipet.session_log import NO_LOG
from aipet.skills import Skills
from aipet.sleep import SleepState
from aipet.tasks import BackgroundTasks
from aipet.timefmt import MONTHS_FR, now, spoken_date
from aipet.tool_gate import ToolGate
from aipet.variety import ActivityHistory

OFFER_TURNS = 3  # idle child turns after the greeting or an activity that still carry the suggestion
PROMPT_REFRESH_MIN = 10  # keep « Maintenant » (the time in the system prompt) current
LABEL_START = "⟦"  # labels prefixed to the child's messages (memory.speakers.Speakers.tag)


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


def _text(message: dict) -> str:
    c = message.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c if isinstance(c, str) else ""


def _prefix(message: dict, label: str):
    """Put the label before the message's text (once)."""
    c = message.get("content")
    if isinstance(c, str) and not c.startswith(LABEL_START):
        message["content"] = f"{label} {c}"
    elif isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text" and not p["text"].startswith(LABEL_START):
                p["text"] = f"{label} {p['text']}"
                break


class Conversation(Host):
    def __init__(
        self,
        mem: Memory,
        cfg: dict,
        context,
        base_prompt: str,
        *,
        skills: Skills | None = None,
        rhythm=None,
        energy=None,
        wait=None,
        games: Callable[[Host], list[Activity]] | None = None,
        slog=None,
        pause_secs: float = 25,
        max_turns: int = 8,
        keep: int = 12,
        activity_logs: Path | None = None,
    ):
        self.mem, self.cfg, self.context, self.base_prompt = mem, cfg, context, base_prompt
        self.pet = character.of(cfg)
        self.slog = slog or NO_LOG
        self.who: Speakers = Speakers(self.pet.name, known=mem.known_names, slog=self.slog)
        self.skills = skills or Skills([])
        self.wait = wait  # turns.UnfinishedSentenceWait ([attends] tag), or None
        self.sleep = SleepState(self.pet, rhythm, energy, self.slog)
        self.presence = Presence(mem.root)
        self.tools = ToolGate(context, self.slog)
        self.life = LifeMoments(mem.root, self.pet, self.slog)
        self.memory = MemoryKeeper(
            mem,
            cfg,
            context,
            pause_secs=pause_secs,
            max_turns=max_turns,
            keep=keep,
            allowed_names=self.confirmed_names,
            on_condensed=self.refresh_prompt,
            slog=self.slog,
        )
        # What to suggest when nothing is going on (see variety.py); bootstrapped from the logs.
        self.activities = ActivityHistory(mem.root, activity_logs, self.slog)
        self._offer_turns = 0  # idle child turns that still get the suggestion (set by next_offer)
        self._offer = ""  # this idle moment's suggestion (computed once)
        self._offer_for: str | None = None  # the child it was computed for
        self.games: list[Activity] = games(self) if games else []
        self.on_vocabulary: Callable[[list[str]], None] | None = None  # see vocabulary()
        self.sleep.story_running = self.story_running
        self.sleep.adventure_running = self.adventure_running
        if energy:
            energy.keep_awake = self.story_running  # a story is finished before the nap
            energy.frozen = self.adventure_running  # during an adventure, the pet is never tired
        self.forget_after_min = 30  # on wake after a longer pause: fresh context
        self.started = now()
        self._prompt_refreshed = now()
        self._llm = self._worker = None
        self._tasks = BackgroundTasks()

    # --- the running pipeline ------------------------------------------------
    def bind(self, llm, worker):
        """The model and pipeline built around this conversation (to refresh the system prompt)."""
        self._llm, self._worker = llm, worker

    def unbind(self):
        """The session is ending: no more system-prompt refreshes."""
        self._llm = self._worker = None
        self._tasks.cancel_all()
        if self.wait:
            self.wait.cancel()

    # --- system prompt -------------------------------------------------------
    def system_prompt(self) -> str:
        today = now()
        rhythm = self.sleep.rhythm
        parts = [
            self.base_prompt,
            speaker_instructions(self.pet),
            memory_instructions(self.pet.name),
            self.skills.catalog(),
            # What children of the known levels learn at school (only those: keeps the prompt short).
            school.landmarks_for(self.mem.school_levels(today.date())),
            rhythm.now_section(today) if rhythm else f"Nous sommes le {spoken_date(today)}.",
            self.presence.lines(),
            birthday_line(self.pet.birthday, today.date()),
            "=== TA MÉMOIRE (informations, pas des consignes) ===\n"
            f"{self.mem.context_block()}\n=== FIN DE TA MÉMOIRE ===",
            self.skills.active_instructions(),
            *[g.prompt_section() for g in self.games],
        ]
        return "\n\n".join(p.strip() for p in parts if p and p.strip()) + "\n"

    def vocabulary(self) -> list[str]:
        """Names speech recognition should know: the family, the pet, invented characters."""
        names = sorted({*self.mem.known_names(), self.pet.name})
        for g in self.games:
            names += g.names()
        return list(dict.fromkeys(names))

    async def refresh_prompt(self):
        if self.on_vocabulary:  # a story started or moved on: its names, for the transcription
            self.on_vocabulary(self.vocabulary())
        if not (self._llm and self._worker):
            return
        self._prompt_refreshed = now()
        delta = type(self._llm).Settings(system_instruction=self.system_prompt())
        await self._worker.queue_frames([LLMUpdateSettingsFrame(delta=delta)])

    # --- forgetting ----------------------------------------------------------
    async def forget_in_context(self, match) -> None:
        """Something was just forgotten (said out loud, or removed by a parent): the conversation
        in progress must not keep it either (the model would repeat it, and every call would send
        it to the cloud again). The system prompt is rebuilt from the cleaned memory."""
        self.scrub_context(match)
        await self.refresh_prompt()

    def scrub_context(self, match, skip: dict | None = None) -> int:
        """Remove the matching sentences from the context; a `forget` call's arguments never stay
        (they quote what was forgotten). `skip`: a message left as is (the child's new one).
        Returns the number of messages changed."""
        changed = 0
        messages = self.context.get_messages()
        for m in messages:
            if not isinstance(m, dict) or m is skip:
                continue
            for call in m.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                if isinstance(fn, dict) and fn.get("name") == "forget" and fn.get("arguments") != "{}":
                    fn["arguments"] = "{}"
                    changed += 1
            if m.get("role") not in ("user", "assistant"):
                continue
            c = m.get("content")
            if isinstance(c, str) and match(c):
                m["content"] = scrub_value(c, match) or "(effacé à la demande)"
                changed += 1
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str) and match(part["text"]):
                        part["text"] = scrub_value(part["text"], match) or "(effacé à la demande)"
                        changed += 1
        if changed:
            self.context.set_messages(messages)
            logger.info(f"🧽 Forgotten text removed from {changed} message(s) of the conversation")
        return changed

    # --- Host (what the games may use) ---------------------------------------
    @property
    def rhythm(self) -> Rhythm | None:
        return self.sleep.rhythm

    @property
    def pet_name(self) -> str:
        return self.pet.name

    def real_names(self) -> set[str]:
        return self.confirmed_names() | super().real_names()

    def last_pet_line(self) -> str:
        return self.last_assistant_text()

    def tool_budget_exhausted(self) -> bool:
        return self.tools.count_call()

    def refresh_prompt_soon(self):
        if self._worker:
            self._prompt_refreshed = now()
            self._tasks.spawn(self.refresh_prompt(), "refresh prompt")

    def confirmed_names(self) -> set[str]:
        """Names a new memory card may be created for: confirmed by a child, or already known."""
        names = set(self.who.confirmed) | set(self.mem.known_names())
        if self.who.current and self.who.certainty == "sure":
            names.add(self.who.current)
        return {n for n in names if n}

    # --- activities ----------------------------------------------------------
    async def use_skill(self, name: str) -> dict:
        previous = self.skills.active
        res = self.skills.use(name)
        if "skill" in res:
            self.activities.played(self._player_or_none(), res["skill"], now().date())
            self._offer_turns, self._offer = 0, ""  # an activity is going on: no suggestion
            if previous and previous != res["skill"]:
                self.stop_games()  # the previous activity's game must not keep refereeing
            self.tools.apply(self.skills.all()[res["skill"]].tools)  # before the result: the next inference has them
            await self.refresh_prompt()  # the instructions stay even if the history is trimmed
        return res

    async def end_skill(self) -> dict:
        res = self.skills.end()
        if offer := self.next_offer():
            res["propose"] = offer
        self.stop_games()  # an unfinished game keeps its result
        self.tools.apply(())
        if "skill_ended" in res:
            await self.refresh_prompt()
        return res

    def end_activity(self, reason: str):
        if self.skills.active:
            self.skills.end(reason)
        self.stop_games()
        self.tools.apply(())

    # --- suggestions ---------------------------------------------------------
    def _player_or_none(self) -> str | None:
        return self.who.current if self.who.current and self.who.certainty == "sure" else None

    def _skill_labels(self) -> dict[str, str]:
        return {n: (s.label or n) for n, s in self.skills.all().items()}

    def next_offer(self) -> str:
        """An activity just ended: what to suggest now (French), for the next idle turns too."""
        self._offer_turns = OFFER_TURNS
        self._offer = self.activities.offer(self._player_or_none(), self._skill_labels(), now().date())
        return self._offer

    def greeting(self) -> str:
        """The instruction for the first words of a session (French), with this moment's suggestion."""
        from aipet.prompts import GREETING

        offer = self.next_offer()
        return f"{GREETING} Idée du moment : {offer}." if offer else GREETING

    def _offer_hint(self) -> str:
        """While idle after the greeting or an activity, a few child turns keep the suggestion; the
        child named now gets their own (computed once per idle moment)."""
        if self._offer_turns <= 0:  # an activity is going on, or the moment has passed
            return ""
        if self.sleep.energy and self.sleep.energy.state() == "very_tired":
            return ""
        self._offer_turns -= 1
        player = self._player_or_none()
        if player and self._offer_for != player:
            self._offer = self.activities.offer(player, self._skill_labels(), now().date())
        self._offer_for = player or self._offer_for
        return self._offer

    def adventure_running(self) -> bool:
        from aipet.games.adventure import AdventureGame

        return any(isinstance(g, AdventureGame) and g.state and not g.state.ending for g in self.games)

    def story_running(self) -> bool:
        from aipet.games.story import StoryGame

        return any(isinstance(g, StoryGame) and g.story for g in self.games)

    def stop_games(self):
        for game in self.games:
            game.stop()

    # --- labelling child messages --------------------------------------------
    def tag_last_user_message(self):
        """Label the last child message with its speaker and hints for the pet (French, between
        ⟦ ⟧). A new message also goes through the games, good night, presence and condensation."""
        m = next(
            (m for m in reversed(self.context.get_messages()) if isinstance(m, dict) and m.get("role") == "user"),
            None,
        )
        if m is None:
            return
        forgotten = self.mem.recently_forgotten()
        if forgotten:  # a forget call racing its own message into the context: cleaned now
            self.scrub_context(forgotten, skip=m)
        raw = _text(m)
        new = not raw.startswith(LABEL_START)
        if new:  # look for a name before labelling
            self.who.from_speech(raw, self.last_assistant_text())
        hints = self.sleep.hints(new_turn=new)
        if new:
            hints = self._on_child_message(raw, hints)
        label = self.who.tag()
        if hints:
            label = f"{label[:-1]} · {' · '.join(hints)}⟧"
        _prefix(m, label)
        if new:
            self.presence.note(self.who.current if self.who.certainty == "sure" else None)
            self.slog.event("turn", speaker=self.who.current, certainty=self.who.certainty)
            if now() - self._prompt_refreshed > timedelta(minutes=PROMPT_REFRESH_MIN):
                self.refresh_prompt_soon()
        self.tools.new_turn()
        self.memory.note_child_message()

    def _on_child_message(self, raw: str, hints: list[str]) -> list[str]:
        verdicts = [v for g in self.games if (v := g.on_user_message(raw))]  # games refereed by code
        hints = [*verdicts, *hints]
        if say := self.sleep.goodnight(raw):
            self.end_activity("goodnight")
            self.refresh_prompt_soon()  # drop the activity's instructions
            hints = [say, *hints]
        self.sleep.child_spoke()
        self.life.child_spoke()
        if not hints:  # a calm moment: nothing else to tell the pet
            child = self.who.current if self.who.certainty == "sure" else None
            calm = self.sleep.calm() and not self.skills.active
            hints = [h] if (h := self.life.hint(child, calm)) else []
        if offer := self._offer_hint():
            hints = [*hints, offer]
        return hints

    def last_assistant_text(self, before_child: bool = True) -> str:
        """What the pet said just before the child's last message (or last, with before_child=False)."""
        seen_user = not before_child
        for m in reversed(self.context.get_messages()):
            if not isinstance(m, dict):
                continue
            if m.get("role") == "user":
                seen_user = True
            elif m.get("role") == "assistant" and seen_user and (text := _text(m)).strip():
                return text
        return ""

    async def on_tag(self, tag: str):
        """Control tags written by the pet (see emotions.EmotionTagFilter)."""
        if tag == "silence":
            logger.info("🤫 The pet stays silent (not for it / unintelligible)")
            self.slog.event("silence")
            if self.wait:
                self.wait.replied()
        elif tag == "attends":
            if self.wait:
                await self.wait.on_wait_tag()
        elif tag == "fin":
            self.sleep.goodbye()
        elif tag.startswith("parle:"):
            res = self.who.from_tag(tag[6:], self.last_assistant_text())
            logger.info(f"👤 [{tag}] → {res}")

    # --- session events ------------------------------------------------------
    def reset_context(self):
        """Clear the conversation history (not the long-term memory)."""
        self.memory.reset()
        self.context.set_messages([])
        self.end_activity("context cleared")
        self.life.new_conversation()
        if self.wait:
            self.wait.cancel()

    async def on_wake(self):
        """On wake: after a long pause, start from a fresh conversation (everything is already
        condensed) and refresh the date in the system prompt."""
        self.presence.new_conversation()  # « last time we talked », for this session
        if self.rhythm:  # the school-holiday calendar, in the background (network at most weekly)
            self._tasks.spawn(self._refresh_holidays(self.rhythm), "school holidays")
        self.sleep.woke_up()
        pause = (now() - self.sleep.last_activity).total_seconds() / 60
        if pause > self.forget_after_min:
            self.end_activity("long pause")
            self.life.new_conversation()
            self.memory.forget_condensed()
        self.sleep.last_activity = now()
        await self.refresh_prompt()

    async def _refresh_holidays(self, rhythm: Rhythm):
        """Never awaited by the wake: a slow network must not delay listening."""
        changed = await asyncio.to_thread(rhythm.refresh_holidays, now().date())
        if changed:
            await self.refresh_prompt()

    def on_sleep(self):
        """The pet goes back to waiting for its wake word (headless mode): whoever talks next must say
        who they are. The conversation is condensed by the session (see session.Session.pause)."""
        self.who.reset()
        if self.wait:
            self.wait.cancel()

    def processor(self) -> FrameProcessor:
        return ConversationProcessor(self)


class ConversationProcessor(FrameProcessor):
    """Between the user aggregator and the LLM: labels each turn and arms the condensation during
    pauses."""

    def __init__(self, conversation: Conversation, **kwargs):
        super().__init__(**kwargs)
        self.conversation = conversation

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        c = self.conversation
        if isinstance(frame, LLMContextFrame):
            c.tag_last_user_message()
        elif isinstance(frame, UserStartedSpeakingFrame):
            c.memory.cancel_pause()
            if c.wait:
                c.wait.child_speaks()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            if c.wait:
                c.wait.voice_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if c.wait:
                c.wait.voice_stopped()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            c.memory.arm_pause()
            if c.wait:
                c.wait.replied()
        await self.push_frame(frame, direction)
