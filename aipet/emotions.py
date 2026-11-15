"""The pet's emotions: small "[joie]" tags written by the model, never spoken.

The model may start a sentence with a tag (closed list, see EMOTIONS). The tag vocabulary is
model-facing French and stays French. Two links in the pipeline:

- `EmotionTagFilter`, between the LLM and TTS, removes tags from the text (so they are never
  spoken, displayed, or stored in memory, since the context is rebuilt from what the voice
  actually said) and sends an `EmotionFrame` telling where in the reply the tag was.
- `EmotionEmitter`, after audio output, holds that emotion until the sentence in question comes
  out of the speaker, then logs it and notifies the UI (RTVI server message). The face therefore
  changes together with the voice, not when the model writes (it is often several sentences ahead).

No added latency: text is only held while reading a tag, so only if it contains a "[", and never
more than MAX_TAG characters.
"""

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    SystemFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

# Tag -> when to use it (French text, copied as is into the model's instructions).
EMOTIONS: dict[str, str] = {
    "joie": "tu es content, enthousiaste, tu félicites",
    "rire": "quelque chose est drôle, tu rigoles, une blague",
    "surprise": "tu es étonné, épaté, « oh ! »",
    "curieux": "tu poses une question, tu veux en savoir plus",
    "calin": "tendresse, réconfort, bonne nuit",
    "pensif": "tu réfléchis, tu cherches, suspense dans une histoire",
    "malice": "taquinerie gentille, devinette, petit secret rigolo",
    "triste": "tu compatis quand l'enfant a de la peine ou a mal",
}

# The same, for a pet whose name takes the feminine in French.
FEMININE: dict[str, str] = {
    "joie": "tu es contente, enthousiaste, tu félicites",
    "surprise": "tu es étonnée, épatée, « oh ! »",
}

# Variants the model might write despite the instructions.
ALIASES: dict[str, str] = {
    "joyeux": "joie",
    "content": "joie",
    "heureux": "joie",
    "fier": "joie",
    "bravo": "joie",
    "rieur": "rire",
    "rires": "rire",
    "rigole": "rire",
    "drole": "rire",
    "surpris": "surprise",
    "etonne": "surprise",
    "oh": "surprise",
    "curiosite": "curieux",
    "question": "curieux",
    "tendre": "calin",
    "tendresse": "calin",
    "ronron": "calin",
    "doux": "calin",
    "caline": "calin",
    "reflechit": "pensif",
    "pense": "pensif",
    "suspense": "pensif",
    "malicieux": "malice",
    "taquin": "malice",
    "coquin": "malice",
    "tristesse": "triste",
    "empathie": "triste",
    "desole": "triste",
    "peine": "triste",
}

# Tags after which the pet says nothing (even if the model adds text by mistake).
MUTING_TAGS = ("silence", "attends")

MAX_TAG = 32  # max characters between the brackets; beyond that it is not a tag


def special_tag(name: str) -> str | None:
    """Control tags (not emotions): "silence", "attends", "fin" or "parle:Name"."""
    n = name.strip()
    low = n.lower()
    if low in ("silence", "attends", "fin"):
        return low
    if low.startswith("parle:") and len(n) > 6:
        return "parle:" + n[6:].strip()
    return None


def normalize(tag: str) -> str | None:
    """ "Câlin" -> "calin"; None if the tag is not a known emotion."""
    t = unicodedata.normalize("NFD", tag.strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = ALIASES.get(t, t)
    return t if t in EMOTIONS else None


def tag_instructions(wait_enabled: bool = True, wording: dict[str, str] | None = None, feminine: bool = False) -> str:
    """System-prompt section (French) explaining every tag: emotions, [parle:X], [silence], [attends].

    One place for all tags, with an explicit order, so the model never has to choose between
    conflicting "always start with…" rules. `wording`: the character's own description of some
    emotions (« tu ronronnes » for a cat); the tag set itself never changes.
    """
    words = {**EMOTIONS, **(FEMININE if feminine else {}), **(wording or {})}
    emotions = "\n".join(f"  [{k}] : {words[k]}" for k in EMOTIONS)
    wait = (
        """- [attends], seule, quand l'enfant s'est visiblement arrêté AU MILIEU de sa phrase et va continuer
  (« Et après le dragon il… », « Je veux un… euh… », « Tu sais, le… le gros… »). Rien n'est dit et il
  a le temps de finir. Seulement quand la phrase est clairement inachevée : une phrase courte mais
  complète (« Je veux une histoire », « oui », « vert », « le chat ») mérite une réponse tout de suite.
  En cas de doute, réponds normalement.
"""
        if wait_enabled
        else ""
    )
    return f"""## Tes balises
Tes balises sont entre crochets et ne sont jamais prononcées. Ce sont les seuls symboles permis.
Une réponse normale commence ainsi, dans cet ordre : [parle:Prénom] seulement si tu sais qui te parle
(voir « Qui te parle »), puis UNE émotion, puis ta phrase. Exemple : [parle:Mila] [joie] Bravo, Mila !
Tes émotions animent ta frimousse ; choisis uniquement dans cette liste :
{emotions}
- Une émotion au début de chaque réponse, puis au plus une par phrase, seulement quand ton émotion change.
- Elle doit aller avec ce que vit l'enfant : [triste] ou [calin] quand il a de la peine, jamais [joie]
  ou [rire] à ce moment-là.
Deux balises spéciales s'écrivent seules, sans rien d'autre :
- [silence], seule, quand la phrase ne t'est pas adressée (les enfants se parlent entre eux, « Noam, tu
  fais quoi ? ») ou qu'elle n'a aucun sens (bruit, télé, mots au hasard comme « un whisky », « la 4 »)
  et ne répond pas à ta question. Rien n'est dit, et tu attends la suite. Si une phrase qui t'est
  adressée est incompréhensible, ne mets pas [silence] : demande gentiment de répéter.
  Une salutation (« Bonjour », « Coucou »), une question ou une phrase dite à toi mérite TOUJOURS une
  réponse, même si tu ne sais pas qui parle : ne pas connaître le prénom n'est jamais une raison de
  te taire (réponds, et demande qui est là).
{wait}Quand l'enfant clôt la conversation (« au revoir », « salut, à plus », « j'arrête », « je vais jouer »,
« c'est fini »), dis-lui au revoir gaiement en une phrase et ajoute [fin] après l'émotion :
[parle:Mila] [joie] [fin] À bientôt, Mila ! La conversation se termine alors. Pas de [fin] pour un au
revoir dans une histoire ou un jeu, ni quand l'enfant hésite ou continue de te parler.
N'écris jamais d'autres crochets.
"""


RESP_KEY = "pet_response"  # reply number, stored in the LLMFullResponseStartFrame metadata


def letter_count(text: str) -> int:
    """Length without whitespace: insensitive to whitespace tweaks made by TTS."""
    return sum(1 for c in text if not c.isspace())


@dataclass
class EmotionFrame(SystemFrame):
    """Emotion to show when the voice reaches `offset` in reply number `response`.

    `offset` counts the characters (excluding whitespace) already written in the reply before
    the tag. It is a system frame: it overtakes pending audio and reaches `EmotionEmitter`
    immediately, which holds it until the right sentence.
    """

    emotion: str = ""
    response: int = 0
    offset: int = 0

    def __str__(self):
        return f"{self.name}(emotion: {self.emotion}, response {self.response} @ {self.offset})"


def _is_tag_char(c: str) -> bool:
    return c.isalpha() or c in "_-:"


class TagStripper:
    """Removes "[word]" tags from text received in small chunks.

    `feed(chunk)` returns the ordered list of what can go out right away: ("text", str),
    ("emotion", name) or ("tag", control). A tag can be split anywhere between two chunks: from
    a "[" on, text is held as long as it could still be a tag (letters only, at most MAX_TAG),
    and released as is as soon as it cannot ("[1, 2]", a lone "["…). Any "[word]" is removed,
    even unknown ones ("[soupir]"): TTS must never read a tag out loud.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._held = ""  # possible start of a tag, not decided yet
        self._at_start = True  # nothing emitted since the start of the reply
        self._last = ""  # last character emitted
        self._skip_ws = False  # swallow whitespace following a removed tag

    @property
    def held(self) -> str:
        return self._held

    def _emit(self, out: list, text: str):
        if not text:
            return
        if self._skip_ws:
            text = text.lstrip(" \t")
            if not text:
                return
            self._skip_ws = False
        if out and out[-1][0] == "text":
            out[-1] = ("text", out[-1][1] + text)
        else:
            out.append(("text", text))
        self._at_start = False
        self._last = text[-1]

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        plain: list[str] = []
        for c in chunk:
            if not self._held:
                if c == "[":
                    self._emit(out, "".join(plain))
                    plain = []
                    self._held = c
                else:
                    plain.append(c)
                continue
            # A tag may be in progress.
            if c == "]" and len(self._held) > 1:
                name = self._held[1:]
                self._held = ""
                emo = normalize(name)
                ctl = None if emo else special_tag(name)
                if emo:
                    out.append(("emotion", emo))
                elif ctl:
                    out.append(("tag", ctl))
                else:
                    logger.debug(f"Unknown tag removed: [{name}]")
                # "word [joie] rest": exactly one space must remain; at the start of a sentence, none.
                self._skip_ws = self._at_start or self._last in " \t\n"
            elif (_is_tag_char(c) or (c in " '’" and ":" in self._held)) and len(self._held) <= MAX_TAG:
                # After « parle: », a first name may hold a space or an apostrophe (« Marie Lou »).
                self._held += c
            else:
                # It was not a tag: give back what we were holding.
                held, self._held = self._held, ""
                self._emit(out, held)
                if c == "[":
                    self._held = c
                else:
                    plain.append(c)
        self._emit(out, "".join(plain))
        return out

    def flush(self) -> list[tuple[str, str]]:
        """End of reply: a leftover unfinished tag ("[jo") is not spoken."""
        if self._held:
            logger.debug(f"Unfinished tag ignored: {self._held!r}")
        self.reset()
        return []


class EmotionTagFilter(FrameProcessor):
    """Between the LLM and TTS: removes tags and reports emotions."""

    def __init__(self, on_tag: Callable[[str], Awaitable[None]] | None = None, **kwargs):
        """`on_tag(name)` receives control tags: "silence", "attends", "parle:Name"."""
        super().__init__(**kwargs)
        self._on_tag = on_tag
        self._stripper = TagStripper()
        self._response = 0  # number of the current reply
        self._count = 0  # characters (excluding whitespace) already sent to TTS in this reply
        self._muted = False  # after [silence] / [attends]: the rest of the reply is not spoken

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            pieces = self._stripper.feed(frame.text)
            for kind, value in pieces:
                if kind == "emotion":
                    await self.push_frame(
                        EmotionFrame(emotion=value, response=self._response, offset=self._count), direction
                    )
                    continue
                if kind == "tag":
                    if value in MUTING_TAGS:
                        self._muted = True  # nothing else from this reply may be spoken
                    if self._on_tag:
                        try:
                            await self._on_tag(value)
                        except Exception:  # a bug in a tag handler must not cut the voice, but must show
                            logger.exception(f"Tag {value!r} failed")
                    continue
                if self._muted:
                    continue
                self._count += letter_count(value)
                # Normal case, no tag: the original frame goes through unchanged.
                same = len(pieces) == 1 and value == frame.text
                await self.push_frame(frame if same else self._text_like(frame, value), direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._stripper.reset()
            self._muted = False
            self._response += 1
            self._count = 0
            frame.metadata[RESP_KEY] = self._response
        elif isinstance(frame, InterruptionFrame):
            self._stripper.reset()
        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            self._stripper.flush()
        await self.push_frame(frame, direction)

    @staticmethod
    def _text_like(src: LLMTextFrame, text: str) -> LLMTextFrame:
        f = LLMTextFrame(text)
        f.skip_tts = src.skip_tts
        f.append_to_context = src.append_to_context
        f.includes_inter_frame_spaces = src.includes_inter_frame_spaces
        f.pts = src.pts
        f.transport_destination = src.transport_destination
        return f


class EmotionEmitter(FrameProcessor):
    """After audio output: shows the emotion when its sentence starts playing.

    Audio output lets through, right before each sentence's audio, an `AggregatedTextFrame`
    with that sentence's text. We count those characters: as soon as a sentence covers the place
    where a tag was, the emotion is released.

    `on_emotion(name)` is a hook for a future screen or LEDs on the Pi.
    """

    def __init__(self, on_emotion: Callable[[str], Awaitable[None]] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._on_emotion = on_emotion
        self._pending: list[EmotionFrame] = []
        self._response = 0  # reply whose audio is playing right now
        self._count = 0  # characters (excluding whitespace) of this reply already played

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, EmotionFrame):
            self._pending.append(frame)
            return
        if isinstance(frame, InterruptionFrame):
            self._pending.clear()  # the rest will never be spoken
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._response = frame.metadata.get(RESP_KEY, self._response + 1)
            self._count = 0
            self._pending = [e for e in self._pending if e.response >= self._response]  # stale leftovers
        elif isinstance(frame, AggregatedTextFrame) and not isinstance(frame, TTSTextFrame):
            self._count += letter_count(frame.text)
            await self._release(
                lambda e: e.response < self._response or (e.response == self._response and e.offset < self._count)
            )
        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            # Tag at the very end of the reply (or text altered by TTS): better late than never.
            await self._release(lambda e: e.response <= self._response, keep_last_only=True)
        await self.push_frame(frame, direction)

    async def _release(self, due, keep_last_only: bool = False):
        ready = [e for e in self._pending if due(e)]
        if not ready:
            return
        self._pending = [e for e in self._pending if not due(e)]
        for e in ready[-1:] if keep_last_only else ready:
            await self._show(e.emotion)

    async def _show(self, emotion: str):
        logger.info(f"Emotion: {emotion}")
        await self.push_frame(RTVIServerMessageFrame(data={"type": "emotion", "emotion": emotion}))
        if self._on_emotion:
            try:
                await self._on_emotion(emotion)
            except Exception:  # a broken screen must not cut the voice
                logger.exception("on_emotion failed")
