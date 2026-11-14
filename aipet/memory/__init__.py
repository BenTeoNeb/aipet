"""The pet's long-term memory.

- `store`: on-disk markdown files (French data format), cards, search, forget, apply.
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
from aipet.memory.tools import MAX_TOOLS_PER_TURN, MEMORY_TOOLS, TOOLS_EXHAUSTED, register_tools

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
