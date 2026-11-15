"""Environment: the repository's .env file, and PET_<NAME> overrides (the older FLOCON_<NAME>
names are still read, for existing setups)."""

import os
from pathlib import Path
from typing import overload

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@overload
def get(name: str) -> str | None: ...
@overload
def get(name: str, default: str) -> str: ...
def get(name: str, default: str | None = None) -> str | None:
    v = os.getenv(f"PET_{name}")
    if v is None:
        v = os.getenv(f"FLOCON_{name}")
    return default if v is None else v
