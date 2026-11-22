"""storyworld: an interactive-fiction world model, turn engine and checker for adventures told by a
language model.

The model writes and narrates; storyworld holds what is true. An adventure is a JSON graph (the
format: SPEC.md, schema/adventure.schema.json): places, things of four kinds (thing, scenery,
container, door), characters, actions as consequences with their triggers, endings. A turn takes
an intent the narrator understood (a canonical verb and ids) and returns an outcome to tell; the
checker proves an adventure can be won, never gets stuck, and that the turn does what the solver
counts on.

    from storyworld import World, validate
    diagnostics, report = validate(graph)
    world = World(graph)
    state = world.start()
    outcome = world.act(state, {"verb": "give", "object": "algue", "target": "pinces"})

Languages: French is the first (`storyworld.locale.fr`): the verbs' words, names' normalization and
the narrator's notes. Difficulty profiles (`storyworld.profiles`) are an application's policy.
"""

from storyworld.check import Diagnostic, check, validate
from storyworld.hints import hint_text, useful_steps
from storyworld.model import FORMAT, KINDS, State, format_of, migrate
from storyworld.rules import CONDITIONS, EFFECTS, Rule, parse
from storyworld.solver import disagreements, replay, solve
from storyworld.world import World

__all__ = [
    "CONDITIONS",
    "EFFECTS",
    "FORMAT",
    "KINDS",
    "Diagnostic",
    "Rule",
    "State",
    "World",
    "check",
    "disagreements",
    "format_of",
    "hint_text",
    "migrate",
    "parse",
    "replay",
    "solve",
    "useful_steps",
    "validate",
]
__version__ = "0.1.0"
