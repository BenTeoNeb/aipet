"""When the pet sleeps, says good night, or the conversation ends: one place for these decisions.

- `reason()`: 'nap' (out of energy), 'night' (bedtime, or the child went to bed) or None. A
  conversation going on at bedtime gets a short grace period to finish.
- `goodnight(text)`: the child says « dodo » — the pet says good night (and, in the evening, sleeps
  from the next message until morning).
- `goodbye()`: the pet closed the conversation ([fin]); a new child message reopens it.
- `hang_up_reason()`: why the pet should hang up once it stops talking (see hangup.HangUp).
- `hints()`: tiredness and bedtime hints for the pet's next reply (French).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from loguru import logger

from aipet import env
from aipet.memory.text import normalize_text
from aipet.session_log import NO_LOG
from aipet.timefmt import now

# The child is off to bed (normalized text: lower case, no accents).
GOODNIGHT = re.compile(
    r"\b(dodo|bonne nuit|au lit|(je vais|on va|il faut|faut|je dois) (aller )?(dormir|me coucher|se coucher))\b"
)
NOT_SLEEPY = re.compile(r"\b(pas|jamais) (encore )?(faire )?(dodo|dormir|au lit|me coucher)\b|\bveux pas\b")
EVENING_WINDOW_MIN = 60  # « dodo » this close to bedtime (or later) puts the pet to sleep too
BEDTIME_SOON_MIN = 15  # « bientôt l'heure de dormir »


def says_goodnight(text: str) -> bool:
    t = normalize_text(text)
    return bool(GOODNIGHT.search(t)) and not NOT_SLEEPY.search(t)


class SleepState:
    def __init__(self, pet, rhythm=None, energy=None, slog=None):
        self.pet = pet  # character.Character (gendered words in the hints)
        self.rhythm = rhythm  # rhythm.Rhythm (time of day, bedtime), or None
        self.energy = energy  # energy.Energy (naps), or None
        self.slog = slog or NO_LOG
        self.bedtime_grace_min = 10  # a conversation going on at bedtime may finish within this
        self.last_activity = now()  # last child message (or wake-up)
        self.asleep_until: datetime | None = None  # the child went to bed: the pet sleeps until morning
        self.closing = False  # the pet said goodbye ([fin]): it hangs up after its last words
        self._goodnight_pending = False  # the child said « dodo »: the pet says good night this turn…

    # ---------------------------------------------------------------- state
    def reason(self) -> str | None:
        """'nap' or 'night' while the pet sleeps, else None."""
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
        """It sleeps ('nap', 'night'), has just said the evening « bonne nuit » ('night'), or the
        child said goodbye ('goodbye')."""
        if self.closing:
            return "goodbye"
        return self.reason() or ("night" if self._goodnight_pending else None)

    def calm(self) -> bool:
        """Not tired, not near bedtime, no good night going on: a moment for something extra."""
        if self._goodnight_pending or self.asleep_until:
            return False
        if self.energy and self.energy.state() in ("tired", "very_tired"):
            return False
        return not (self.rhythm and self.rhythm.minutes_to_bedtime(now()) <= BEDTIME_SOON_MIN)

    # ---------------------------------------------------------------- events
    def child_spoke(self):
        self.closing = False  # the child speaks again: the conversation goes on
        self.last_activity = now()

    def woke_up(self):
        self.closing = False

    def goodbye(self):
        """[fin]: the pet closes the conversation and hangs up after its last words. If it was tired,
        the nap it announced is real: a « d'accord » or a quick reconnect gets a sleepy reply, not
        the whole conversation again."""
        logger.info("👋 The pet closes the conversation: it will hang up")
        self.closing = True
        self.slog.event("goodbye")
        if self.energy and self.energy.state() in ("tired", "very_tired"):
            self.energy.nap()

    def goodnight(self, text: str) -> str | None:
        """« Non non, dodo là »: the instruction for the pet (French), or None. Near bedtime the pet
        then falls asleep too (from the next message until morning), instead of offering one more
        game. The caller stops the running activity."""
        if self._goodnight_pending:  # good night was said last turn
            self._goodnight_pending = False
            if self.rhythm:
                self.asleep_until = self.rhythm.next_wakeup(now())
                logger.info(f"🌙 The pet sleeps until {self.asleep_until:%H:%M} (the child went to bed)")
                self.slog.event("goodnight_sleep", until=self.asleep_until.isoformat(timespec="minutes"))
        if not says_goodnight(text):
            return None
        evening = bool(self.rhythm and self.rhythm.minutes_to_bedtime(now()) <= EVENING_WINDOW_MIN)
        self.slog.event("goodnight", evening=evening)
        if evening:
            self._goodnight_pending = True
            return (
                "l'enfant va se coucher : dis-lui bonne nuit en une ou deux phrases douces, sans rien proposer "
                "d'autre, et dis que toi aussi tu vas faire dodo"
            )
        return "l'enfant parle d'aller dormir : s'il va se coucher, dis-lui bonne nuit tendrement sans rien proposer"

    # ---------------------------------------------------------------- hints
    def hints(self) -> list[str]:
        """Short French hints for the pet's next reply: energy and bedtime."""
        hints = []
        g = self.pet.g
        if self.energy:
            state = self.energy.state()
            if state == "tired":
                hints.append(
                    f"tu commences à être {g('fatigué', 'fatiguée')} : dis-le avec des mots (« Ouaaah… »), jamais « *bâille* », "
                    "et propose des choses calmes ; si tu pars faire ta sieste, dis au revoir avec [fin]"
                )
            elif state == "very_tired":
                hints.append(
                    f"tu es très {g('fatigué', 'fatiguée')} : termine en une ou deux phrases, puis dis au revoir joyeusement pour aller "
                    "faire ta sieste, avec [fin] (sauf si l'enfant a de la peine : reste avec lui)"
                )
        if self.rhythm:
            t = now()
            left = self.rhythm.minutes_to_bedtime(t)
            bed = self.rhythm.bedtime(t.date())
            if 0 < left <= BEDTIME_SOON_MIN:
                hints.append(f"bientôt l'heure de dormir ({bed.hour}h{bed.minute:02d}) : propose de finir doucement")
            elif left <= 0 and self.rhythm.is_night(t):
                hints.append("c'est l'heure de dormir : termine en une ou deux phrases et dis bonne nuit")
        return hints
