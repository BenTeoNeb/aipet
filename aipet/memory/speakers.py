"""Speakers: who is talking to the pet right now (names confirmed before use)."""

from __future__ import annotations

import re

from loguru import logger

from aipet.memory.text import normalize_text
from aipet.session_log import NO_LOG
from aipet.timefmt import WEEKDAYS_FR, now

# --------------------------------------------------------------------------
# Speakers: who is talking to the pet right now
# --------------------------------------------------------------------------
# Model-facing instructions (French on purpose).
SPEAKER_INSTRUCTIONS = """
## Qui te parle
- Chaque message commence par une étiquette ⟦jour heure · qui parle⟧ ajoutée par le système : l'enfant
  ne la dit pas et ne la voit pas. Ne la répète jamais à voix haute.
- Indique qui te parle avec [parle:Prénom], seulement pour un prénom connu (fiches de la famille) ou
  qu'un enfant vient de te CONFIRMER en réponse à ta question. Si tu ne sais pas qui parle, n'écris pas
  de balise [parle:…] du tout, et réponds normalement.
- La transcription entend parfois mal les prénoms (« Lila » au lieu de « Mila »). Un prénom nouveau, ou
  qui ressemble à un prénom connu, se confirme TOUJOURS d'abord : « Tu t'appelles bien Lila ? Ou c'est
  Mila ? ». Tant que ce n'est pas confirmé, n'appelle personne par ce prénom. Si l'étiquette indique
  « à confirmer », pose la question.
- Il y a souvent plusieurs enfants : quelqu'un peut arriver, partir ou prendre la parole à la place d'un
  autre. Guette les indices (« à moi », un prénom, une autre façon de parler). Si tu ne sais pas qui
  parle, demande-le avec légèreté (« C'est Mila ou Noam qui parle ? »), sans bloquer le jeu et pas à
  chaque phrase. Ne nomme jamais quelqu'un au hasard.
- Quand un nouvel enfant est confirmé, sois curieux de lui : au bon moment, demande son âge, puis plus
  tard une chose qu'il aime. Adapte-toi à chacun (son âge, ses goûts d'après ta mémoire).
"""


def speaker_instructions(pet) -> str:
    """SPEAKER_INSTRUCTIONS with the pet's grammatical gender."""
    return SPEAKER_INSTRUCTIONS.replace("sois curieux de lui", f"sois {pet.g('curieux', 'curieuse')} de lui")


# Certainty values (code) → words shown in the model-facing label (French).
CERTAINTY_FR = {"sure": "sûr", "probable": "probable", "unknown": "inconnu"}


class Speakers:
    """Who is speaking: current speaker, people present, names waiting for confirmation."""

    def __init__(self, pet_name: str = "", known=None, slog=None):
        self.pet_name = pet_name  # never a child's name (« c'est Flocon »)
        self.current: str | None = None
        self.certainty = "unknown"  # "sure" | "probable" | "unknown"
        self.present: list[str] = []
        self.previous: str | None = None
        self.history: list[dict] = []
        self.slog = slog or NO_LOG
        self.known = known or list  # () -> names from the family cards
        self.confirmed: set[str] = set()  # names confirmed while the pet is running
        self.to_confirm: dict[str, str | None] = {}  # name -> known name it resembles

    def set(self, name: str, certainty: str = "probable", present: list[str] | None = None):
        name = name.strip().capitalize()
        self.to_confirm.pop(name, None)
        if name != self.current:
            self.slog.event("speaker", name=name, certainty=certainty, previous=self.current)
        if name != self.current:
            self.history.append({"time": f"{now():%H:%M:%S}", "name": name, "certainty": certainty})
            self.history = self.history[-30:]
        self.current, self.certainty = name, certainty
        for p in [name, *(present or [])]:
            p = p.strip().capitalize()
            if p and p not in self.present:
                self.present.append(p)

    def from_tag(self, name: str, pet_just_said: str = "") -> str:
        """[parle:Name] tag written by the pet. A known name is accepted; a new name only if
        the pet just asked for it (the child confirmed); otherwise it stays "to confirm" and the
        label of the next message reminds the pet.
        Returns "empty" | "known" | "confirmed" | "to_confirm"."""
        name = name.strip().capitalize()
        if not name:
            return "empty"
        known = {normalize_text(n): n for n in [*self.known(), *self.confirmed]}
        if normalize_text(name) in known:
            self.set(known[normalize_text(name)], "sure")
            return "known"
        resembles = _resembles(name, list(known.values()))
        asked = bool(re.search(r"\b" + re.escape(normalize_text(name)) + r"\b", normalize_text(pet_just_said)))
        if asked:  # the pet asked just before and the child answered: confirmed
            self.confirmed.add(name)
            self.to_confirm.pop(name, None)
            self.set(name, "sure")
            return "confirmed"
        self.to_confirm[name] = resembles
        self.slog.event("speaker_to_confirm", name=name, resembles=resembles)
        logger.info(f"👤 {name} to confirm" + (f" (resembles {resembles})" if resembles else ""))
        return "to_confirm"

    def from_speech(self, text: str, pet_just_said: str = "") -> str | None:
        """Clues in what the child says, without waiting for the model:
        « je m'appelle X », « moi c'est X », « c'est X »…: known name → that child is speaking;
        new name → to confirm. « oui » to « tu t'appelles bien X ? » → X confirmed.
        Returns "known" | "confirmed" | "to_confirm" | None."""
        t = text.strip()
        if self.to_confirm and re.match(
            r"(?i)^\W*(oui|ouais|ouii+|voil[aà]|c'est (ça|ca|moi)|exactement|bien s[uû]r)\b", t
        ):
            # Only if the question was about the name (« tu t'appelles bien Zoé ? »),
            # not about something else (« Zoé, tu as quel âge ? »).
            questions = [
                q
                for q in re.findall(r"[^.!?]*\?", normalize_text(pet_just_said))
                if re.search(r"appel|prenom|c'est bien|bien toi|tu es |ou c'est|ou tu es", q)
            ]
            for p in list(self.to_confirm):
                if any(re.search(r"\b" + re.escape(normalize_text(p)) + r"\b", q) for q in questions):
                    self.confirmed.add(p)
                    self.set(p, "sure")
                    logger.info(f"👤 {p} confirmed by « {t[:30]} »")
                    return "confirmed"
        m = re.search(
            r"(?:je m'appelle|je m appelle|moi c'est|moi, c'est|c'est moi|mon prénom c'est|"
            r"mon nom c'est|ici c'est|non,? c'est|c'est)\s+([A-ZÀ-ÖØ-Ý][\w-]+)",
            t,
        )
        if not m:
            return None
        name = m.group(1).capitalize()
        if normalize_text(name) in {normalize_text(self.pet_name), "moi", "toi", "ca", "pas", "quoi"}:
            return None
        known = {normalize_text(n): n for n in [*self.known(), *self.confirmed]}
        if normalize_text(name) in known:
            for p in list(self.to_confirm):  # « non, c'est Mila »: Lila was a mistake
                if _resembles(p, [known[normalize_text(name)]]):
                    self.to_confirm.pop(p, None)
            self.set(known[normalize_text(name)], "probable")
            return "known"
        self.to_confirm[name] = _resembles(name, list(known.values()))
        self.slog.event("speaker_to_confirm", name=name, resembles=self.to_confirm[name])
        logger.info(f"👤 {name} heard → to confirm")
        return "to_confirm"

    def reset(self):
        """After sleeping: the next person may not be the same."""
        if self.current:
            self.previous = self.current
        self.current, self.certainty, self.present = None, "unknown", []
        self.to_confirm = {}

    def tag(self) -> str:
        """Label prepended to each user message (model-facing, French)."""
        if self.current:
            who = f"{self.current} ({CERTAINTY_FR.get(self.certainty, self.certainty)})"
        else:
            who = "prénom pas encore connu" + (f", avant c'était {self.previous}" if self.previous else "")
        extra = f" · présents : {', '.join(self.present)}" if len(self.present) > 1 else ""
        for p, r in self.to_confirm.items():
            extra += f" · à confirmer : {p}" + (f" (ressemble à {r})" if r else "")
        d = now()
        return f"⟦{WEEKDAYS_FR[d.weekday()][:3]}. {d:%d/%m %H:%M} · parle : {who}{extra}⟧"

    def state(self) -> dict:
        return {
            "current": self.current,
            "certainty": self.certainty,
            "present": self.present,
            "previous": self.previous,
            "history": self.history,
            "to_confirm": dict(self.to_confirm),
        }


def _resembles(name: str, known: list[str]) -> str | None:
    """Known name close enough to be a transcription error (Lila ~ Mila)."""
    import difflib

    a = normalize_text(name)
    best, score = None, 0.0
    for n in known:
        r = difflib.SequenceMatcher(None, a, normalize_text(n)).ratio()
        if r > score:
            best, score = n, r
    return best if score >= 0.6 else None


TAG_RE = re.compile(r"^⟦[^⟧]*⟧\s*")
