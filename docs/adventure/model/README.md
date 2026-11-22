# Adventure data model

The format is specified in `storyworld/SPEC.md` (with its JSON Schema, `storyworld/schema/`); this
file keeps the design notes: how the model got there, and why. We change the adventure data model step by step on one reference adventure
(`le-phare-de-piko.json` here, a copy of `aipet/data/adventures/le-phare-de-piko.json`). The engine,
the generator, the bank and the explorer page follow once the model has converged.

## Model 2 (implemented in the engine; the reference is `le-phare-de-piko.json`, `"model": 2`)

The engine runs model 2; version 1 graphs (the bank, stored adventures, the generator's output)
are upgraded when loaded (`adventure_engine.upgrade`: each action's verb/object/target becomes its
only trigger, lossless; whole-description `variants` are still read). Propagation (rewriting the
bank, the generator's schema and prompt, migrating stored adventures) comes once the model has
converged on the reference.

### Step 1: one action = one consequence, with its triggers (Inform's kinds of action, TADS' remap)

```json
{"id": "allumer_lampe",
 "triggers": [{"verb": "light", "object": "lampe"},
              {"verb": "use", "object": "allumettes", "target": "lampe"},
              {"verb": "give", "object": "allumettes", "target": "papy"}],
 "when": ["has:allumettes"], "effects": ["end:feu"], "points": 20, "not_yet": "...", "text": "..."}
```

- A trigger lists only what really differs; verb synonyms (`VERBS`) and names (`aliases`) stay in
  the engine. A losing action answers each trigger's exact verb only.
- Decided: no per-trigger `when` or `text` until an adventure needs it; a clue stays an action
  without effects; the name is `triggers`.

### Step 2: things have kinds and states (Inform's world model)

- **Containers** (`container`, `openable`, `open`): an item `at` a container id is inside; seen and
  taken once the container is open. Opening or searching a closed container opens it (built in).
- **Doors**: an exit names its `door` (an openable item, on both sides); going through opens it.
- **Locks and keys** (`locked`, `key`): opening a locked thing with its key in the bag unlocks it
  first (Inform's Locksmith extension), also when walking through a door. Closing is refused
  (« mieux vaut laisser ouvert ») so the hero can never be locked out.
- **Texts** of opening: `texts: {open, locked, unlock, closed}`.
- **Value states** (Inform's « can be empty or full »): `state` + `states: {name: {name, aliases,
  description, initial, says}}` on items and characters; condition `is:X:S`, effect `set:X:S`.
  The jar is one item, `vide` then `plein` (no more `bocal` → `bocal_plein` swap); the crab is
  `grognon` then `content` (no more flag).
- Conditions `open:X`, `locked:X`, `is:X:S`; effects `open:`, `unlock:`, `set:` (`close:` and `lock:`
  were dropped at step 4: the world only moves forward).

### Step 3: composed descriptions (Inform's room description, storylets)

- `description` (what never changes) + `details: [{when, text, else}]` on places, items and
  characters.
- `initial` on items and characters (Inform's « initial appearance »): a sentence in the place's
  description while the thing was never moved (« Sur la table, un bocal vide. ») or while the
  character is there; a state may override it (« Pinces le crabe grignote, tout content. »).
- Piko's cabin went from 5 whole descriptions to 1 base + 1 detail + the jar's initial
  appearance; no `variants` left in the reference.

### Step 4: after the review (22/11/2026) — what the generator writes, and what the child says

An independent review of model 2 (engine, generator, bibliography) found that the concepts were
right but the encoding would hurt the generator, and that the engine still made children guess the
verb. Changed before propagating:

- **Kinds, not booleans** (Inform's kinds, TextWorld's types): `kind: "thing" | "scenery" |
  "container" | "door"` replaces `takeable` / `container` / `openable` (still read, for version 1).
  Inconsistent booleans (a door left takeable, a carried container) can't be written any more.
- **States as a list**, the first is the start: `states: [{id: "vide"}, {id: "plein", name: …}]`
  (no `state` field; a map keyed by state name doesn't go through strict structured output).
- **A door is written once**: `exit.door` on one side; the engine closes the way back with the
  same door (a conflict is an error), and a door needs no `at`.
- **A character holds a thing**: `at: "pinces"` on the key; « Pinces le crabe tient… » (`tenu`),
  taking it answers « C'est Pinces qui l'a », `reveal:cle` makes him let it go. No more key « nowhere »
  plus prose plus a stem-matching heuristic.
- **The things say it, not the verb** (TADS' remap as a default): when a trigger's two things are
  named, any verb does it (« prendre les lucioles avec le bocal » = « remplir le bocal »), and
  « attraper les lucioles » does it when the jar is in the bag. Never for a loss, a blow, a cuddle,
  a look, talking or playing (`NO_REMAP`), nor for a one-thing trigger (there the verb matters).
  Pulling, pushing or using a drawer opens it; taking what a closed drawer holds opens it first.
- **A world that only moves forward**: no `close:`, `lock:`, `unflag:`, `hide:` (TextWorld's quests
  are monotonic too): the hero is never locked out and the solver stays small.
- **The checker plays the game**: every way to win the solver finds is replayed through `act()`
  (`replay`), so a scripted action can't shadow a built-in one the solver counted on; a trigger
  that only opens something built in is an error. An unreachable ending says why (« action X needs
  has:Y, and no effect ever makes it true »); actions that can never happen are warnings. The
  solver runs even when the structure has errors, so the one repair sees everything.
- **Puzzles first** (Ron Gilbert's puzzle dependency chart): the generator writes `endings →
  puzzles → actions → items → characters → places`, each puzzle `{goal, action, needs, clue_in}`;
  the checker verifies the ids and warns when `clue_in` names none of what is needed.
- **Difficulty in puzzles**: the shortest win must solve 2–6 (facile), 4–9 (moyen), 6–14 (difficile)
  puzzles (actions and openings on the path, the bank's range), besides its steps.
- **Hints**: an action's `hint` (written by the author, Invisiclues style) is the second-level hint;
  hints name things as they are now and the trigger at hand (the matches in the bag, not the lamp).
- The generator's mechanical slips are fixed by code (`_clean`): a missing way back, empty optional
  fields, a key without its lock.

- **Code locks** (escape-game mini puzzles): `code: {press: [buttons in order], right, wrong, done,
  hint}` on a container or a door. Pressing (push, use, touch, hit…) a button plays it: the right
  one moves the code on (`pressed`, a property of the thing), a wrong one starts it again, the last
  opens the thing and says what is inside. The order is shown elsewhere (Piko: three shells drawn on
  the sand, three shells glued on the drawer: blue, pink, white). The buttons are scenery, no action
  may use them; a code counts as one puzzle; the second hint points at the clue, the third gives the
  next button. The solver only presses the right next button (a wrong one only starts again).

- **Riddles and passwords**: `answer: {question, accept, right, wrong, hint, effects}` on a character
  (or a door or chest that speaks, then locked until answered). The narrator sends the child's words
  with the verb `answer`; talking to the riddler gives the `question`; a wrong answer repeats it.
- **Companions**: the effect `follow:X` makes a character go wherever the hero goes (`here:X` then
  holds further on); `move:X:P` sends them away.

### The catalogue of patterns (`tests/test_adventure_patterns.py`)

One tiny model 2 adventure per classic mechanism (escape games, gamebooks, point-and-click), each
accepted by the checker and won by speaking: key and door; something hidden found by searching;
A + B = C; a trade with someone who holds the thing; a character won over in steps (states);
collecting several things; a way blocked until something is done; a map revealing a way; a code;
a riddle; a companion; a choice and a folly announced before it loses. A change to the model must keep
them green; the generator's prompt lists the same recipes. Left out on purpose: timers (a tide in N
turns: against « never stuck », maybe later for « difficile »), dialogue trees (the narrator talks
freely), limited inventories and dice.

- **Lines that follow the situation** (storylets): `says` and `initial` may be a list of
  `{when, text}`, the first that holds is said (the last one, without `when`, is the default):
  Piko says « Tu as la clé ! Vite, au phare ! » once the hero has it. A companion's `initial` is not
  told while it follows the hero.
- **Texts that would lie are warned** (`check` → `warnings`): a place's lasting text naming someone
  who moves (it belongs in their `initial`), « fermé à clé » told where the door may be open or the
  key in hand (it belongs in a detail with `!has:KEY`). The narrator believes the texts: in a played
  test it told « fermée à clé » to a child holding the key.
- **The first hint points at the clue**: when the puzzle chart says where the clue is
  (`clue_in`) and it isn't here, « Et si tu retournais voir le dessin sur le sable, du côté de la
  plage ? ».

### Testing with a simulated child (`uv run pet-playtest`)

A model plays a child (age, style: curieux, pressé, rêveur) against the real narrator (the pet's base
prompt, the adventure instructions, the tools, the engine; text instead of voice); a judge reads
every narrator turn against what the engine answered (contradictions, solution given away, invented
leads, actions told without calling the engine). Reported: what the world didn't understand (free,
absent, impossible: missing aliases or triggers) and the narrator's slips. Each game is a session log
in `logs/playtests/`, shown on the explorer page under « Parties » (🤖). The first one found the
drawing on the beach was only a sentence (« regarder les coquillages » → free): it is now a thing.

### Step 5: after a grown-up tested the limits (22/11/2026)

A tester erased the drawing that gives the code, threw things, picked up stones that weren't there,
and got the fireflies' « Le savais-tu ? » after winning with the matches. What changed:

- **« Never stuck » is the checker's promise about the adventure as written, not a ban on the child.**
  What is plausible is allowed: `destroy` (effacer, casser, jeter à la mer) makes a thing gone for good
  — never a character, nor what is `sturdy` (a reason) or a container or a door. If a win is still
  reachable (another ending, a code that can be tried), the game goes on. If not, the engine notes it
  (`State.doomed`) and says nothing: the game ends (built-in ending `_coince`, « Sans la clé,
  impossible d'aller plus loin ») when the child runs into it — a failed try, asking for help, or going
  round in circles — and the « sauvegarde » is the moment before the throw. (Sierra's « walking dead »,
  told when it matters instead of never.)
- **Memory of places, characters and things**: the narrator's invented details (`remember`, with
  `about`) are kept per place, character or thing, and come back in the description and when looking
  at them (« détails inventés »): they are true from then on.
- **A « Le savais-tu ? » per ending**: `endings[].fact`, else the adventure's `fact`.
- **The hero's bag** is built in (« mon sac », « mes poches », unless a thing of the adventure is
  meant): open or look → the inventory; put something in → take it; drop or throw it → refused.
- **Decor**: `places[].decor` lists a few things seen there that serve no purpose (cailloux,
  flaques); the narrator may imagine others that fit the place. Something not in the adventure but
  plausible is played along with, and never helps (`absent`'s note).
- **« Où est le crabe ? »** (`where`) is answered from what the hero knows (seen there, in the bag,
  gone), never guessed; for a place, the first way to it.
- **Hints are counted**: a failed try says `essais_avant_indice`, so the narrator knows the engine
  will help soon and doesn't give the solution away first.

### Step 6: a library (`storyworld/`, 22/11/2026)

After a second review aimed at « something that can become an open-source standard »:

- **The narrator names, the engine checks.** The narrator understands the child and sends ids (each
  answer lists what is `in_reach`); the engine accepts an id, or a name or alias exactly (articles
  aside, singular or plural alike). No more guessing from words (fuzzy matching, prefixes of verbs,
  qualifiers): the language model judges, the engine stays exact.
- **Inform's implicit taking**: giving, using or throwing a thing that lies here takes it first.
- **Destroying is opt-in for scenery** (`fragile`); a thing one carries can always be lost.
- **The engine is a package**, `storyworld/` (no import of the app): `model`, `rules` (conditions and
  effects parsed once into typed rules, one table for parser, checker and docs), `world`, `solver`,
  `check` (`validate` → `Diagnostic(code, message, severity)`, the format apart from the pet's
  difficulty `profiles`), `hints`, `schema/` (JSON Schema 2020-12), `locale/` (French). Outcomes have
  English keys; the pet's narrator reads them in French (`locale.localize`).
- **A spec** (`storyworld/SPEC.md`), a `format` field (`model` before) and one `migrate()`.
- **The checker plays every move** the solver explores through the turn (`disagreements`), and
  property tests fuzz graphs and intents.

Not taken, on purpose: supporters, light and dark, wearables, per-trigger conditions, PDDL export,
social simulation (Versu, Façade): a children's spoken adventure doesn't need them.

### Piko, version 1 → 2

11 actions → 6 (8 triggers); the drawer and the door need no action any more (built-in opening,
the key unlocks); `bocal_plein` and the flags `tiroir_ouvert`, `porte_ouverte`, `crabe_content` are
gone. Solver: same endings (feu, lucioles; loss: pince), 108 states, shortest win 12 steps.

### Next

- The generator writes model 2 (schema, prompt, `_clean`); the bank is still version 1, read through
  `upgrade` and the booleans. Rewrite it in model 2 (kinds, triggers merging duplicate actions,
  `details` instead of `variants`, held things), then drop the version 1 reading. Stored adventures
  keep their own graph: no migration needed while `upgrade` stays.
- Character lines that depend on the situation (`says` with details): Piko still says « Pinces a la
  clé » once the hero has it.
- A lint for place descriptions naming a movable character or thing (it belongs in `initial`).
- Offline, for the bank: a simulated child playing each adventure, mining `free` / `absent` /
  `impossible` answers for missing aliases and triggers.
- Characters: a persona (LIGHT) and goals (Drammar) for the narrator.

## Prior art (research, 22/11/2026)

What we borrow, and what we leave:

- **Inform 7** (world model + Standard Rules): kinds of things (room, thing, container, supporter,
  door, person) with states (open/closed, locked, lit, portable, scenery); the words the player uses
  (`Understand "taper [something]" as attacking`) are separate from what happens (check / carry out /
  report rules); **kinds of action** let one rule answer several actions ("pushing the drum is
  noisy behaviour; instead of noisy behaviour…"). → our `triggers` (step 1); object states instead
  of flags and swapped items (next).
- **TADS 3 / adv3Lite**: verify (does it make sense?) → preconditions → check (allowed now?) → action
  → report; `remap` sends « use matches on lamp » to « light lamp ». → our `not_yet` vs
  `impossible`; `triggers` again.
- **TextWorld** (Microsoft): types, predicates and rules with preconditions / postconditions
  (PDDL/STRIPS style: `container.twl`, `door.twl`, `key.twl`), quests generated by forward or
  backward chaining. → our `when` / `effects` already are STRIPS; typed predicates for doors, keys,
  containers.
- **Puzzle dependency charts** (Ron Gilbert, LucasArts): the design tool for adventure puzzles,
  designed backwards from the end. → the explorer's « Énigmes » view.
- **Storylets / quality-based narrative** (Failbetter, Emily Short): small content units gated by
  qualities. → composed place descriptions and character lines, each with its `when`.
- **LIGHT** (Facebook AI): characters with a persona, objects with affordances (container, food,
  weapon, wearable). → characters' persona; affordances.
- **Narrative ontologies** (GOLEM 2025, Drammar, OntoMedia, Propp in OWL by Peinado & Gervás): made
  to annotate and analyse stories, not to play them. → not our data model; Propp's functions and
  Drammar's goals may help the generator write the story arc and the characters' motives.
- **D&D 5e SRD** (5e-bits, Open5e): rules content (monsters, spells, classes), no world or puzzles.
  → not relevant.
- **LLM-era generators** (STORY2GAME, WorldWeaver, RPGBench with its BFS validity checker,
  ByteSized32, Text2World): the same split as ours — the model writes, a symbolic state holds the
  truth, a checker proves the game can be won.
