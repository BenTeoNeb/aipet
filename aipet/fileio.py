"""Safe file helpers shared by the memory, the games and the pet's state files.

- `lock_for(path)`: one re-entrant lock per file or folder for the whole process, so the voice pipeline
  and the web page (parents' corrections) never write the same file at the same time.
- `atomic_write_text` / `atomic_write_json`: a crash mid-write never leaves a half-written file.
- `load_json`: a missing file gives the default; a corrupt one is set aside (never silently
  overwritten with an empty one) and logged.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: str | Path) -> threading.RLock:
    """The process-wide lock of a file or folder (same path → same lock)."""
    key = Path(path).expanduser().resolve()
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # *.tmp is git-ignored in the memory folder
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: str | Path, data: Any, indent: int | None = 1) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n")


def load_json(path: str | Path, default: Any) -> Any:
    """The file's data, or a fresh copy of `default` when it is missing or unreadable. A corrupt file
    is renamed `<name>.corrupt-<time>` so the next save cannot erase what it still holds."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)
    except OSError as e:
        logger.error(f"Could not read {path.name}: {e}")
        return copy.deepcopy(default)
    except ValueError as e:  # invalid JSON or invalid UTF-8
        kept = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            path.replace(kept)
            logger.error(f"Unreadable {path.name} ({e}): set aside as {kept.name}, starting from empty")
        except OSError:
            logger.error(f"Unreadable {path.name} ({e}), and it could not be set aside")
        return copy.deepcopy(default)
