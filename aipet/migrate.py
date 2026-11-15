"""One-time move of the data from before character packs (~/.flocon) to the per-pet layout.

Everything in ~/.flocon belonged to Flocon, so it becomes Flocon's folder:

    ~/.flocon/memoire  ->  ~/.aipet/pets/flocon/memory   (with its git history)
    ~/.flocon/state    ->  ~/.aipet/pets/flocon/state
    ~/.flocon/logs     ->  ~/.aipet/pets/flocon/logs
    ~/.flocon/cache    ->  ~/.aipet/cache                (household)
    ~/.flocon/skills   ->  ~/.aipet/skills               (household)

Folders are moved, not copied: the children's data must exist in one place only (a forget request
has to reach every copy). A folder already present at the destination is never overwritten. Other
files in ~/.flocon are left where they are. Runs at startup; does nothing once done.
"""

import os
import shutil
from pathlib import Path

from loguru import logger

from aipet import env, paths

LEGACY_PET = "flocon"


def legacy_home() -> Path:
    return Path(os.path.expanduser(env.get("LEGACY_HOME", "~/.flocon")))


def moves(legacy: Path, home: Path) -> list[tuple[Path, Path]]:
    pet = home / "pets" / LEGACY_PET
    return [
        (legacy / "memoire", pet / "memory"),
        (legacy / "state", pet / "state"),
        (legacy / "logs", pet / "logs"),
        (legacy / "cache", home / "cache"),
        (legacy / "skills", home / "skills"),
    ]


def migrate_legacy(legacy: Path | None = None, home: Path | None = None) -> list[str]:
    """Move what is left to move; returns what was moved (empty when there is nothing to do)."""
    legacy = legacy or legacy_home()
    home = home or paths.home()
    done: list[str] = []
    if not legacy.is_dir():
        return done
    for src, dst in moves(legacy, home):
        if not src.is_dir():
            continue
        if dst.exists():
            logger.warning(f"Migration: {dst} already exists, {src} left untouched (check it by hand)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        done.append(f"{src} -> {dst}")
        logger.info(f"Migration: moved {src} -> {dst}")
    if done:
        note = legacy / "MOVED_TO_AIPET.txt"
        note.write_text(
            "The data of this folder moved to ~/.aipet (one folder per pet, see aipet/paths.py).\n"
            + "\n".join(done)
            + "\n"
        )
    return done
