"""The pet's long-term memory.

- `store`: `Memory`, one pet's memory folder: reading, search, forget, applying updates.
- `cards`: the on-disk format (person cards, the pet's life file, folder names).
- `updates`: the model's structured update turned into file contents.
- `forget`: what a forget request matches, and how text is cleaned.
- `repo`: the memory's git history.
- `text`: French text helpers (accents, slugs, stopwords, rude words).
- `condensation`: the model turns conversation chunks into structured updates.
- `tools`: search_memory / read_memory / forget, exposed to the model.
- `speakers`: who is talking (first names confirmed before being used).
- `keeper`: condenses the conversation continuously and trims the context.
- `life_moments`: the « ma vie » moments (the pet's own life, chosen by the children).
"""

from aipet.memory.condensation import SCHEMA, condense, turns_from_messages
from aipet.memory.keeper import MemoryKeeper
from aipet.memory.speakers import Speakers
from aipet.memory.store import Memory, now, parse_card, render_card, spoken_date
from aipet.memory.tools import MEMORY_TOOLS, register_tools
from aipet.tool_gate import MAX_TOOLS_PER_TURN, TOOLS_EXHAUSTED

__all__ = [
    "MAX_TOOLS_PER_TURN",
    "MEMORY_TOOLS",
    "SCHEMA",
    "TOOLS_EXHAUSTED",
    "Memory",
    "MemoryKeeper",
    "Speakers",
    "condense",
    "now",
    "parse_card",
    "register_tools",
    "render_card",
    "spoken_date",
    "turns_from_messages",
]
