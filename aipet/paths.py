"""Where things live on disk. One folder per pet: a pet never sees another pet's memory.

    ~/.aipet/                       (PET_HOME)
        cache/                      household: school-holiday calendar
        skills/                     household: the family's own activities
        pets/<id>/
            memory/                 the pet's long-term memory (git): children it met, memories, games…
            state/                  energy level
            logs/                   session logs (JSONL) and the text log

A process runs one pet: the one selected by PET_CHARACTER or `character` in config.toml.
"""

import os
import tomllib
from pathlib import Path

from aipet import env
from aipet.character import DEFAULT

ROOT = Path(__file__).parent.parent  # repository root: config.toml and .env live here
CONFIG_PATH = Path(env.get("CONFIG", str(ROOT / "config.toml")))


def home() -> Path:
    return Path(os.path.expanduser(env.get("HOME", "~/.aipet")))


def pet_id() -> str:
    """The selected pet, without loading the whole configuration."""
    chosen = env.get("CHARACTER")
    if chosen:
        return chosen
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f).get("character") or DEFAULT
    except OSError:
        return DEFAULT


def pet_dir(pet: str | None = None) -> Path:
    return home() / "pets" / (pet or pet_id())


def memory_dir(cfg: dict | None = None, pet: str | None = None) -> Path:
    """PET_MEMORY_DIR, else [memory].dir (may contain {pet}), else ~/.aipet/pets/<id>/memory."""
    pet = pet or pet_id()
    explicit = env.get("MEMORY_DIR") or (cfg or {}).get("memory", {}).get("dir")
    if explicit:
        return Path(os.path.expanduser(explicit.replace("{pet}", pet)))
    return pet_dir(pet) / "memory"


def state_dir(pet: str | None = None) -> Path:
    return Path(os.path.expanduser(env.get("STATE", str(pet_dir(pet) / "state"))))


def logs_dir(pet: str | None = None) -> Path:
    return Path(os.path.expanduser(env.get("LOGS", str(pet_dir(pet) / "logs"))))


def cache_dir() -> Path:
    return Path(os.path.expanduser(env.get("CACHE", str(home() / "cache"))))


def family_skills_dir(cfg: dict | None = None) -> Path:
    explicit = env.get("SKILLS_DIR") or (cfg or {}).get("skills", {}).get("family_dir")
    return Path(os.path.expanduser(explicit or str(home() / "skills")))
