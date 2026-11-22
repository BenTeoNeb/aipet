# storyworld

An interactive-fiction world model, turn engine and checker for adventures **told by a language
model**. The model writes the adventure and narrates it; storyworld holds what is true, plays each
intent the narrator understood, and proves, before anyone plays, that the adventure can be won.

It stands on the interactive-fiction standards (Inform 7's world model and kinds of action, TADS 3's
remapping, TextWorld's typed STRIPS rules, Ron Gilbert's puzzle dependency charts, storylets), cut
down to what a spoken adventure for children needs.

## In short

```python
from storyworld import World, validate

diagnostics, report = validate(graph)  # format, references, rules, playability
assert not [d for d in diagnostics if d.severity == "error"]

world = World(graph)
state = world.start()
outcome = world.act(state, {"verb": "give", "object": "algue", "target": "pinces"})
# {"outcome": "done", "text": "Pinces croque l'algue…", "changes": [...], "in_reach": {...}}
```

- **The format** — `SPEC.md`, with its JSON Schema in `schema/adventure.schema.json` (draft
  2020-12). Version 2 (`"format": 2`); older graphs are migrated on load (`migrate`).
- **The turn** — `World.act(state, intent)`: an intent is a canonical verb and ids (the narrator
  names things by the ids `in_reach` gives; the engine never guesses from words). Every intent gets an
  outcome to tell: `done`, `not_yet`, `impossible`, `absent`, `no_exit`, `ask`, `free`, `clue`,
  `warning`, `hint`, `end`.
- **The checker** — `validate(graph, profile=None)` returns `Diagnostic(code, message, severity)`
  and the solver's report. A valid adventure can be won, never leaves the hero stuck, and the turn
  does what the solver counts on, on every move it explores (`disagreements`). A `profile` adds an
  application's policy (sizes, steps, puzzles: `profiles.py` has the pet's).
- **Languages** — `locale/`: the words children say for each verb, how names are normalized, the
  messages and the narrator's notes, the outcome keys a narrator reads. French first.

## Modules

| Module | What it holds |
|---|---|
| `model.py` | versions and `migrate`, kinds, normalized things, `State` |
| `rules.py` | conditions and effects: the mini-language, parsed once into typed `Rule`s, with the table of kinds |
| `world.py` | `World`: what is true and around, the turn |
| `solver.py` | the state space, the ways to win, agreement between solver and turn |
| `check.py` | `validate`, `Diagnostic`, the format's lints |
| `hints.py` | hints from the solver's next useful step |
| `schema/` | the JSON Schema |
| `locale/` | languages (`fr.py`), `localize` |
| `profiles.py` | the pet's difficulty profiles (an application's policy, not the format) |

## Guarantees and tests

- Deterministic: no randomness, ordered iteration.
- Never raises on odd input: a graph too broken to check is reported, an odd intent is told.
- Tested as properties (`tests/test_storyworld_properties.py`): every solver edge equals the turn,
  the solver's reduction keeps the verdict, no crash on fuzzed graphs and intents, states round-trip
  through JSON, migration is stable; and on a catalogue of puzzle patterns
  (`aipet/games/adventure_patterns.py`) and a reference adventure.

## Status

Extracted from an application (a voice pet for children) and still living in its repository: the app
uses it through `aipet/games/adventure_engine.py`. Before publishing on its own: its own
`pyproject.toml`, a licence for the library and the format (the repository is AGPL-3.0-only), a
second language to prove the locale abstraction, and an immutable state for the solver.
