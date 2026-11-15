"""The memory's git history: every change is a commit, so a parent can see what changed and when.
Best effort: the files are the truth, git is only the history (a missing git never stops the pet).
Callers hold the memory's file lock (`fileio.lock_for(root)`); the lock is re-entrant."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from aipet.fileio import lock_for

GIT_TIMEOUT_SECS = 30


class MemoryRepo:
    def __init__(self, root: Path, author: str):
        self.root, self.author = root, author or "pet"
        self.lock = lock_for(root)

    @property
    def exists(self) -> bool:
        return (self.root / ".git").exists()

    def _git(self, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECS, check=check
        )

    def init(self) -> None:
        with self.lock:
            try:
                self._git("init", "-q", check=True)
                self._git("config", "user.name", self.author, check=True)
                self._git("config", "user.email", "pet@localhost", check=True)
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning(f"git unavailable for memory history: {e}")

    def commit(self, message: str) -> None:
        if not self.exists:
            return
        with self.lock:
            try:
                for args in (("add", "-A"), ("commit", "-qm", message)):
                    r = self._git(*args)
                    if r.returncode and "nothing to commit" not in r.stdout + r.stderr:
                        logger.warning(f"Memory git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
                        return
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning(f"Memory git unavailable: {e}")

    def wipe(self, message: str) -> None:
        """Forget for real: start the history again from the current files."""
        if not self.exists:
            return
        with self.lock:
            try:
                shutil.rmtree(self.root / ".git")
            except OSError as e:
                logger.warning(f"Could not clear the memory history: {e}")
                return
            self.init()
            self.commit(message)
