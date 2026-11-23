"""The adventure engine of the pet: the `storyworld` library (world model, turn, solver, checker),
with the pet's difficulty profiles and French. This module only re-exports it for the app.

See storyworld/SPEC.md (the format) and docs/adventure/model/README.md (the design notes)."""

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
