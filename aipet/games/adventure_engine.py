"""The adventure engine of the pet: the `storyworld` library (world model, turn, solver, checker),
with the pet's difficulty profiles and French. This module only re-exports it for the app.

See storyworld/SPEC.md (the format) and docs/adventure/model/README.md (the design notes)."""

import functools
import hashlib
import json
from pathlib import Path

from storyworld.check import (  # noqa: F401
    CONDITIONS,
    EFFECTS,
    _builtin_twin,
    _mentions,
    _never_true,
    _prose,
    _texts,
    _why_not,
    check,
)
from storyworld.hints import HINT_STATES, _clue_pointer, _trigger_for, hint_text, useful_steps  # noqa: F401
from storyworld.locale import localize
from storyworld.locale.fr import (  # noqa: F401
    ABSENT_NOTE,
    BAG,
    DESTROY_NOTE,
    EXIT_STOPWORDS,
    FREE_NOTE,
    KEYS,
    LOCKED_WORDS,
    TRUTH_NOTE,
    VERB_FR,
    VERBS,
    WARNING,
    _cap,
    _norm,
    _norm_keep,
    _stems,
    _way,
    _words,
    canonical_verb,
    normalize_text,
    of,
)
from storyworld.model import *  # noqa: F401,F403
from storyworld.model import _all_conditions, _normalized  # noqa: F401
from storyworld.profiles import DIFFICULTIES, WINS  # noqa: F401
from storyworld.rules import parse  # noqa: F401
from storyworld.solver import MAX_STATES, _key, _moves, _path, disagreements, intent_for, replay, solve  # noqa: F401
from storyworld.verbs import *  # noqa: F401,F403
from storyworld.world import World as _World


class World(_World):
    """The library's world with the pet's policy (its difficulty's profile), answering the narrator
    with French keys (« lieu », « à_portée »)."""

    def __init__(self, graph: dict, lang=None, policy: dict | None = None):
        level = graph.get("difficulty") if isinstance(graph, dict) else None
        super().__init__(graph, lang, policy or DIFFICULTIES.get(level or "", DIFFICULTIES["facile"]))

    def act(self, st: State, intent: dict) -> dict:  # noqa: F405
        return localize(super().act(st, intent), KEYS)

    def describe(self, st: State) -> dict:  # noqa: F405
        return localize(super().describe(st), KEYS)

    def options(self, st: State) -> dict:  # noqa: F405
        return localize(super().options(st), KEYS)

    def nudge(self, st: State) -> dict:  # noqa: F405
        return localize(super().nudge(st), KEYS)


# ---- the verdict, kept: an adventure is checked once per version of the engine ----------------
_VERDICTS = "adventure_verdicts.json"  # in the cache folder: {graph hash:engine hash → errors}
_KEPT = 500
_memo: dict[str, list[str]] = {}


@functools.cache
def engine_hash() -> str:
    """The engine's sources (storyworld and this module): any change checks every adventure again."""
    h = hashlib.sha256()
    for p in sorted([*_storyworld_dir().rglob("*.py"),
                     *_storyworld_dir().rglob("*.json"), Path(__file__)]):  # fmt: skip
        h.update(p.as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:16]


def _storyworld_dir() -> Path:
    import storyworld

    return Path(storyworld.__file__).parent


def graph_hash(graph: dict) -> str:
    text = json.dumps(graph, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def errors_of(graph: dict) -> list[str]:
    """`check`'s errors, kept per adventure and engine version (checking Mimi takes a second or more:
    never while a child waits to start). A graph or an engine changed is checked again."""
    from aipet import paths
    from aipet.fileio import atomic_write_json, load_json

    key = f"{graph_hash(graph)}:{engine_hash()}"
    if key in _memo:
        return list(_memo[key])
    store = paths.cache_dir() / _VERDICTS
    kept = load_json(store, {})
    kept = kept if isinstance(kept, dict) else {}
    if isinstance(kept.get(key), list):
        _memo[key] = kept[key]
        return list(kept[key])
    errors = check(graph)[0]
    _memo[key] = errors
    kept[key] = errors
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(store, dict(list(kept.items())[-_KEPT:]), indent=None)
    except OSError:  # a read-only cache: kept in memory only
        pass
    return list(errors)
