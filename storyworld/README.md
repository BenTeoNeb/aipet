# storyworld

An interactive-fiction world model, turn engine and checker for adventures **told by a language
model**. The model writes the adventure and narrates it; storyworld holds what is true, plays each
intent the narrator understood, and proves, before anyone plays, that the adventure can be won.

It stands on the interactive-fiction standards ([Inform 7][wi]'s world model and
[kinds of action][wi-7-15], [TADS 3's remapping][tads-remap], [TextWorld][textworld]'s typed,
STRIPS-like rules, Ron Gilbert's [puzzle dependency charts][gilbert-charts], [storylets][storylets]),
cut down to what a spoken adventure for children needs. The sources are listed under
[References](#references).

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

What `validate` proves, by exploring every reachable state (up to 60,000):

- a win can be reached, every ending can be reached, and the hero can never get stuck as written;
- a code or a riddle is played only once its clue (`puzzles[].clue_in`) has been met: winnable
  means winnable by a child who doesn't know the answer beforehand;
- every move the solver counts on is what the turn does (`disagreements`).

What is not proven, and how it is tested instead:

- that the turn never leads outside the solver's states: tested as a property on the patterns, the
  reference adventure and a sample of states of the easy bank adventures, with every verb on every
  thing in reach (`test_the_turn_never_leaves_the_solvers_states`);
- that the solver's take-first reduction keeps the verdict: argued in `World._eager_ok`, tested on
  the corpus and on random worlds with dark places, lights and uses;
- the player's `destroy`: outside the proof; the engine checks after it whether a win is still
  reachable, and makes the hero think twice, in the story, before one that would not (SPEC 7.9).

Also: deterministic (no randomness, ordered iteration); never raises on odd input (a graph too
broken to check is reported, an odd intent is told); states round-trip through JSON; migration is
stable. The derived puzzle chart (`chart.py`: depth, width) is in the report.

## References

What the library builds on, and what it takes from each.

**The world model and the built-in behaviours**

- Inform 7 (Graham Nelson), [*Writing with Inform*][wi]. The kinds of things, each with its
  properties: [§3.5 Kinds][wi-3-5], [§3.8 Scenery][wi-3-8], [§3.12 Doors][wi-3-12],
  [§3.13 Locks and keys][wi-3-13]. The `initial` sentence of a thing, told until it is first picked
  up: [§3.11 Two descriptions of things][wi-3-11]. Dark places and lights:
  [§3.15 Light and darkness][wi-3-15]. One consequence for several ways of saying it (`triggers`):
  [§7.15 Kinds of action][wi-7-15].
- Emily Short, [Locksmith][locksmith], an extension that ships with Inform 7. Opening a locked thing
  with its key in the bag unlocks it first; a door opens when the hero walks through it.
- *Getting Started in TADS 3*: [Remap][tads-remap], [Verify][tads-verify], [Check][tads-check]. One
  command played as another when the things say it (a scripted action found by its things, whatever
  the verb). The split between what never makes sense (`impossible`) and what makes sense but cannot
  happen now (`not_yet`).

**The rules and the checker**

- Marc-Alexandre Côté et al., [TextWorld: A Learning Environment for Text-based Games][textworld]
  (2018). Typed rules with preconditions and effects (`when`, `effects`; the paper writes them in
  linear logic), and a quest as a chain of such rules that a program can build and check.
- Richard Fikes and Nils Nilsson, [STRIPS: A New Approach to the Application of Theorem Proving to
  Problem Solving][strips] (1971). An action as preconditions and effects on a state of facts: what
  the solver searches.

**Puzzle design and fair play**

- Ron Gilbert, [Puzzle Dependency Charts][gilbert-charts] (2014). The `puzzles` list: each puzzle
  with what it needs and where its clue is, written backwards from the end.
- Emily Short, [Storylets: You Want Them][storylets] (2019). Content with prerequisites: the texts
  gated by conditions (`details`, `says`, `initial`). An action (conditions, a text, effects) is the
  closest thing here to a full storylet.
- Graham Nelson, [*The Craft of the Adventure*][craft] (2nd edition, 1995), "Bill of Player's
  Rights". The rights the engine and the checker answer: not to be killed without warning (the
  `warning` before a loss), not to have the game closed off without warning ("never stuck"), not to
  have to type exactly the right verb (verb families, remap), to have a good reason why something is
  impossible (`fixed`, `sturdy`).
- Andrew Plotkin's [cruelty scale][cruelty]. A valid adventure, as written, is "merciful" or
  "polite": the hero may lose, but never gets stuck. `destroy` is the one exception (SPEC 7.9).
- Infocom's [InvisiClues][invisiclues]. Hints that say more each time: the three hint levels and an
  action's `hint`.

[wi]: https://ganelson.github.io/inform-website/book/WI_1_1.html
[wi-3-5]: https://ganelson.github.io/inform-website/book/WI_3_5.html
[wi-3-8]: https://ganelson.github.io/inform-website/book/WI_3_8.html
[wi-3-11]: https://ganelson.github.io/inform-website/book/WI_3_11.html
[wi-3-12]: https://ganelson.github.io/inform-website/book/WI_3_12.html
[wi-3-13]: https://ganelson.github.io/inform-website/book/WI_3_13.html
[wi-3-15]: https://ganelson.github.io/inform-website/book/WI_3_15.html
[wi-7-15]: https://ganelson.github.io/inform-website/book/WI_7_15.html
[locksmith]: https://rileypb.github.io/I7extdocs/10.1/Extensions/Emily%20Short/Locksmith.html
[tads-remap]: https://www.tads.org/t3doc/doc/gsg/remap.htm
[tads-verify]: https://www.tads.org/t3doc/doc/gsg/verify.htm
[tads-check]: https://www.tads.org/t3doc/doc/gsg/check.htm
[textworld]: https://arxiv.org/abs/1806.11532
[strips]: https://ijcai.org/proceedings/1971/63
[gilbert-charts]: https://grumpygamer.com/puzzle_dependency_charts
[storylets]: https://emshort.blog/2019/11/29/storylets-you-want-them/
[craft]: https://ifarchive.org/if-archive/info/Craft.Of.Adventure.pdf
[cruelty]: https://www.ifwiki.org/Cruelty_scale
[invisiclues]: https://www.ifwiki.org/InvisiClues

## Status

Extracted from an application (a voice pet for children) and still living in its repository: the app
uses it through `aipet/games/adventure_engine.py`. Before publishing on its own: its own
`pyproject.toml`, a licence for the library and the format (the repository is AGPL-3.0-only), a
second language to prove the locale abstraction, and an immutable state for the solver.
