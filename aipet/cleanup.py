"""Text safety nets (ideas borrowed from Pistache, a friend's companion project, MIT).

- `TranscriptFilter` (between STT and the user aggregator): drops, without calling the model,
  obviously meaningless transcripts — punctuation only, a single letter, a word repeated in a
  loop ("oui oui oui oui oui"), or phantom phrases that speech engines are known to "hear".
  Less obvious cases are left to the model (the [silence] tag).
- `SpokenTextCleaner` (after the LLM, before the emotion filter and TTS): removes what cannot be
  read aloud (asterisk bold/italics, # headings, bullets, code, emojis) in case the model ignores
  the "no markdown" instruction. "7 * 5" becomes "7 fois 5".
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from aipet.session_log import NO_LOG

# --- transcripts -------------------------------------------------------------------------------
_ONLY_PUNCT = re.compile(r"^[\W_]+$", re.UNICODE)
_LOOP = re.compile(r"\b(\w+)(?:[\s,.!?]+\1\b){4,}", re.IGNORECASE | re.UNICODE)
# Phrases some engines "hear" in silence or music (French speech: kept in French).
_PHANTOMS = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|n'oubliez pas de vous abonner",
    re.IGNORECASE,
)


def valid_transcript(text: str) -> bool:
    t = " ".join(text.split())
    if len(re.sub(r"[\W_]", "", t)) < 2:  # empty, punctuation only, a single letter
        return False
    if _ONLY_PUNCT.fullmatch(t) or _LOOP.search(t) or _PHANTOMS.search(t):
        return False
    return True


class TranscriptFilter(FrameProcessor):
    def __init__(self, slog=None, **kwargs):
        super().__init__(**kwargs)
        self._slog = slog or NO_LOG

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
            and direction == FrameDirection.DOWNSTREAM
            and not valid_transcript(frame.text)
        ):
            if isinstance(frame, TranscriptionFrame):
                logger.info(f"🗑️  Transcript dropped: {frame.text!r}")
                self._slog.event("transcript_dropped", text=frame.text[:200])
            return
        await self.push_frame(frame, direction)


# --- text spoken aloud ------------------------------------------------------------------------
_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff️‍]")
_TIMES = re.compile(r"(\d)\s*[*×]\s*(\d)")
_BULLET = re.compile(r"(^|\n)[ \t]*(?:[-•–]|\d+[.)])[ \t]+")
_HEADING = re.compile(r"(^|\n)[ \t]*#{1,6}[ \t]*")


def clean_spoken(text: str, at_line_start: bool = False) -> str:
    """Clean one chunk of text (the model streams it in small pieces)."""
    t = _TIMES.sub(r"\1 fois \2", text)
    t = _EMOJI.sub("", t)
    t = t.replace("`", "").replace("**", "").replace("__", "")
    t = t.replace("*", "")  # italics / stage directions like "*purrs*"; "7 * 5" is already handled
    t = _HEADING.sub(r"\1", ("\n" + t) if at_line_start else t)
    t = _BULLET.sub(r"\1", t)
    if at_line_start and t.startswith("\n"):
        t = t[1:]
    return t


class SpokenTextCleaner(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._line_start = True

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._line_start = True
        elif isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM and frame.text:
            cleaned = clean_spoken(frame.text, self._line_start)
            self._line_start = frame.text.endswith("\n")
            if cleaned != frame.text:
                frame.text = cleaned  # same frame: its other attributes are kept
            if not cleaned:
                return
        await self.push_frame(frame, direction)
