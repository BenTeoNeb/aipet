# Storyworld adventure format, version 2

## 1. Purpose

An adventure is a JSON document describing a small interactive-fiction world: places, things,
characters, the actions that change the world, and the endings. A program (the engine) holds the
truth of the world and decides what each turn does; a narrator (a person or a language model) tells
it. The format is designed so that a checker can prove, before anyone plays, that the adventure can
be won and that the player can never get stuck.

The world model follows the interactive-fiction tradition: Inform 7 (kinds, states, initial
appearance, implicit actions), TADS 3 (remap, verify/check), TextWorld (STRIPS-style rules, a world
that only moves forward), storylets (texts gated by conditions) and Ron Gilbert's puzzle dependency
charts.

Normative artefacts:

- `schema/adventure.schema.json` (JSON Schema draft 2020-12): the structure. A document that does
  not validate is not a format 2 adventure.
- This document: the meaning of every field, the condition and effect language, the built-in
  behaviours, and the semantic rules the checker enforces on top of the schema.

The key words MUST, SHOULD and MAY are used as in RFC 2119. Text data (names, descriptions, lines)
may be in any language; the reference adventures are in French.

## 2. Versioning

- A document declares its version with the top-level integer `"format": 2`.
- `"model": 2` is a deprecated alias (files written before the field was renamed). Readers MUST
  accept either; writers SHOULD write `format` only. One of the two is required.
- Version 1 graphs (one `verb`/`object`/`target` per action, whole-description `variants`, items
  typed with `takeable`/`container`/`openable` booleans) are not covered by this specification. They
  are migrated to version 2 by the library's `migrate()` before being validated or played.
- A few version 1 forms are still accepted by the schema and marked `deprecated`: the item booleans
  `takeable`, `container`, `openable`; `states` as a map with a separate `state`; a place's
  `variants`. New documents SHOULD NOT use them.
- **Extensions**: any object of the format MAY carry extra properties whose names start with `x-`
  (`"x-editor": {...}`). Engines ignore them and tools SHOULD preserve them. Any other unknown
  property is an error.

## 3. Identifiers and references

- Places, items and characters share one namespace: their `id`s MUST be unique across the three
  lists and non-empty. Actions and endings each have their own namespace.
- An id MUST NOT contain `:` or whitespace and MUST NOT start with `!` (ids appear inside condition
  and effect strings). Lowercase ASCII with underscores (`pied_phare`) is recommended.
- Ending ids starting with `_` are reserved for built-in endings (`_coince`, see 7.9).
- A field holding an id is a *reference*; every reference MUST resolve (section 8).

## 4. Entities

Defaults apply when a field is absent. An empty string or list means the same as absent.

### 4.1 Root

| Field | Type | Meaning | Default |
|---|---|---|---|
| `format` | integer, `2` | Format version (required unless `model` is given). | — |
| `model` | integer, `2` | Deprecated alias of `format`. | — |
| `title` | string | The adventure's title. Required. | — |
| `difficulty` | `facile` \| `moyen` \| `difficile` | Difficulty label. | none |
| `tags` | string[] | Theme keywords (to match a player's idea). | `[]` |
| `fact` | string | A true "did you know?" anecdote told at the end. | none |
| `intro` | string | Told before the first turn. | none |
| `goal` | string | The mission, in one sentence. | none |
| `start` | Start | Where the hero starts. Required. | — |
| `places` | Place[] | At least one. Required. | — |
| `items` | Item[] | Things, scenery, containers, doors. | `[]` |
| `characters` | Character[] | People and animals. | `[]` |
| `actions` | Action[] | Scripted actions (the puzzles). | `[]` |
| `endings` | Ending[] | At least one; required. | — |
| `puzzles` | Puzzle[] | The puzzle dependency chart. | `[]` |

`difficulty` is only a label in the format. What it implies (number of places, length of the
shortest win, number of puzzles and lose endings, how soon hints come, how many retries) is an
**application policy**, configured and checked by the application, not part of the format.

**Start**: `place` (place id, required); `inventory` (item ids in the bag at the start, default
`[]`).

### 4.2 Shared building blocks

- **Text**: a string told to the player.
- **Aliases**: other names the player may use. Matching ignores articles, case, accents and plural
  marks; a thing is also known by its id (underscores as spaces) and by every state's names.
- **Conditions** (`when`): a list of condition strings that must *all* hold; an empty list always
  holds (grammar in section 5).
- **Detail** `{when, text, else}`: a sentence appended to a description: `text` while `when` holds,
  else `else` (if any). A detail without `when` is always told.
- **Composed description**: `description` (what never changes) followed by each `details` entry that
  yields a text, in order.
- **Situated text** (`says`, `initial`): either a string, or a list of lines `{when?, text}`; the
  first line whose `when` holds is used. The last line SHOULD have no `when` (it is the default; the
  checker reports an error otherwise).

### 4.3 Place

| Field | Type | Meaning | Default |
|---|---|---|---|
| `id` | id | Unique id. Required. | — |
| `name` | string | Name, with its article ("la plage"). Required. | — |
| `aliases` | string[] | Other names. | `[]` |
| `description` | string | What never changes here. | `""` |
| `details` | Detail[] | Situation-dependent sentences. | `[]` |
| `decor` | string[] | Plain things seen here that serve no purpose ("galets", "flaques"). | `[]` |
| `exits` | Exit[] | The ways out. | `[]` |
| `dark` | boolean \| string | Dark: without a light, nothing is seen here but the ways out (7.12). A string is what the hero feels there in the dark. | `false` |
| `senses` | Senses | What is heard, smelled, felt here (7.14). | none |
| `variants` | `{when, description}`[] | Deprecated: version 1 whole descriptions. | — |

**Exit**:

| Field | Type | Meaning | Default |
|---|---|---|---|
| `to` | place id | Destination. Required. | — |
| `label` | string | How the way is named ("vers la plage"). | the destination's name |
| `dir` | string | A direction word ("nord", "haut"), also accepted as the way's name. | `""` |
| `door` | item id (kind `door`) | A door closing this way (see 7.4). | none |
| `when` | Conditions | What must hold to go through. | `[]` |
| `blocked` | string | Told while `when` does not hold. | a generic text |

Exits are one-way: a way back is a separate exit on the destination (generators usually add it).

### 4.4 Item

| Field | Type | Meaning | Default |
|---|---|---|---|
| `id` | id | Unique id. Required. | — |
| `kind` | `thing` \| `scenery` \| `container` \| `door` | Inform's kind (below). | `thing` |
| `name` | string | Name, with its article. Required. | — |
| `aliases` | string[] | Other names. | `[]` |
| `description` | string | What examining it tells. | `""` |
| `details` | Detail[] | Situation-dependent sentences about it. | `[]` |
| `at` | location | Where it starts: a place id, a container id (inside it), a character id (held by them), or `""` (not in the world until an effect brings it). A door needs none. | `""` |
| `fixed` | string | Why it cannot be taken, told when the player tries. | a generic text |
| `sturdy` | boolean \| string | Cannot be destroyed; a string is the reason told. | `false` |
| `fragile` | boolean | Scenery that can be destroyed (a drawing in the sand). | `false` |
| `initial` | situated text | Initial appearance (7.1). | none |
| `open` | boolean | Starts open (container, door). | `false` |
| `locked` | boolean | Starts locked (container, door). | `false` |
| `key` | item id | The item that unlocks it (container, door). | none |
| `texts` | `{open, locked, unlock, closed, empty}` | What opening says: `open` when it opens, `locked` when it is locked and cannot open yet, `unlock` when the key unlocks and opens it, `closed` for an exit behind this unlocked, closed door; `empty` when it has no use left (7.13). | generic texts |
| `uses` | integer ≥ 1 | How many times it can be used before it is refilled (7.13). | none |
| `light` | boolean \| Conditions | Gives light in dark places: always, or while the conditions hold (7.12). | none |
| `senses` | Senses | What is heard, smelled, felt about it (7.14). | none |
| `states` | State[] | Value states (4.6). | none |
| `code` | Code | A code lock that opens it (7.6). | none |
| `answer` | Answer | A riddle or password it asks (7.7). | none |
| `takeable`, `container`, `openable` | boolean | Deprecated version 1 booleans, read only when `kind` is absent. | — |
| `state` | string | Deprecated: starting state of a map-form `states`. | — |

Kinds:

- **thing**: can be taken, carried, given, used, lost or destroyed.
- **scenery**: stays where it is (taking it answers `fixed`); destroyed only when `fragile`.
- **container**: scenery that holds things (items whose `at` is its id) and opens. What it holds is
  seen and reachable only while it is open.
- **door**: scenery that closes the exits naming it in `door`.

A `key`, `locked: true` or a `code` requires kind `container` or `door` (the schema enforces it). A
`code` or an `answer` makes the item locked until it is solved, without a key.

### 4.5 Character

| Field | Type | Meaning | Default |
|---|---|---|---|
| `id` | id | Unique id. Required. | — |
| `name` | string | Name. Required. | — |
| `aliases` | string[] | Other names. | `[]` |
| `description` | string | What examining them tells. | `""` |
| `details` | Detail[] | Situation-dependent sentences about them. | `[]` |
| `at` | place id or `""` | Where they start (`""`: nowhere until `move`d). | `""` |
| `says` | situated text | What they say when talked to. | `"…"` |
| `initial` | situated text | A sentence in the place's description while they are there and not following the hero. | none |
| `states` | State[] | Value states (4.6). | none |
| `answer` | Answer | A riddle or password they ask (7.7). | none |
| `state` | string | Deprecated, as for items. | — |

A character can hold items (items whose `at` is the character's id): they are seen ("Pinces tient
une petite clé"), not taken; `reveal:ITEM` makes the character let go of it.

### 4.6 State

Inform's "can be empty or full". `states` is a list; the **first** is the starting state.

| Field | Type | Meaning |
|---|---|---|
| `id` | id | The state's name, unique within its owner (used by `is:` and `set:`). Required. |
| `name` | string | The owner's name while in this state. |
| `aliases` | string[] | More names (all states' names are always understood). |
| `description` | string | The owner's description while in this state. |
| `initial` | situated text | The owner's initial appearance while in this state. |
| `says` | situated text | What the owner says while in this state. |

While a state is current, each field it gives replaces the owner's.

### 4.7 Action

An action is **one consequence** with the ways of saying it (Inform's "kinds of action").

| Field | Type | Meaning | Default |
|---|---|---|---|
| `id` | id | Unique among actions. Required. | — |
| `triggers` | Trigger[] | At least one. Required. | — |
| `place` | place id | Only in this place. | anywhere its things are in reach |
| `when` | Conditions | What must hold. | `[]` |
| `effects` | Effects | What it changes. Empty: a clue or a joke (7.10). | `[]` |
| `text` | string | Told when done. | `""` |
| `not_yet` | string | Told when tried before `when` holds. | a generic text |
| `hint` | string | The second-level hint for this step (Invisiclues style). | a generic hint |
| `points` | integer | Score when done. | `5` |
| `repeatable` | boolean | May be done again (only meaningful for non-idempotent effects such as `gain`). | `false` |
| `warning` | string | For an action that ends in a loss: the warning told the first time. | a generic warning |

**Trigger** `{verb, object?, target?}`: `verb` is a canonical verb (below); `object` and `target`
are ids of items, characters or places. A trigger lists only what really differs: verb synonyms and
aliases are handled by the engine.

Canonical verbs a trigger may use: `examine search give use combine open close talk answer ask eat
drink climb swim jump push pull light wear read hit destroy smell touch listen wait sit sing dance
throw fill`. The verbs `go take drop look inventory help where other` are built in and MUST NOT
appear in a trigger. A trigger that only does what a built-in opening does (opening a container or a
door, possibly with its key) is an error: write `texts.open` / `texts.unlock` instead.

### 4.8 Ending

| Field | Type | Meaning | Default |
|---|---|---|---|
| `id` | id | Unique among endings; not starting with `_`. Required. | — |
| `kind` | `win` \| `lose` | Required. | — |
| `text` | string | The ending told. | `""` |
| `fact` | string | A "did you know?" for this ending. | the root `fact` |

### 4.9 Puzzle

A node of the puzzle dependency chart, written backwards from the end. Not needed to play; used by
authors, generators and the hint system, and checked.

| Field | Type | Meaning |
|---|---|---|
| `goal` | string | What solving it achieves. |
| `action` | id | What solves it: an action id, an item with a `code`, or the id of a riddle's asker. Required. |
| `needs` | id[] | Items, characters or places it needs. |
| `clue_in` | id | The item, character or place whose texts give the clue. Required. |

## 5. Conditions and effects

### 5.1 Grammar

```ebnf
condition = [ "!" ] , ( unary-cond , ":" , id | "is" , ":" , id , ":" , id ) ;
unary-cond = "has" | "flag" | "at" | "visited" | "here" | "open" | "locked" | "charged" ;

effect    = unary-eff , ":" , id | ( "move" | "set" ) , ":" , id , ":" , id ;
unary-eff = "gain" | "lose" | "flag" | "reveal" | "follow" | "goto" | "end" | "open" | "unlock" | "spend" | "refill" ;

id        = first-char , { char } ;      (* no ":" and no whitespace *)
first-char = ? any character except "!", ":" and whitespace ? ;
char      = ? any character except ":" and whitespace ? ;
```

Effects are never negated. Flag names are free (they need not be declared).

### 5.2 Conditions

| Condition | Holds when | Argument |
|---|---|---|
| `has:X` | item X is in the hero's bag | item id |
| `flag:N` | flag N has been set | any name |
| `at:P` | the hero is in place P | place id |
| `visited:P` | the hero has been in P (the start place counts) | place id |
| `here:C` | character C is in the hero's place | character id |
| `open:X` | container or door X is open | openable item id |
| `locked:X` | container or door X is locked | openable item id |
| `is:X:S` | thing X (item or character) is in state S | id and one of its state ids |
| `charged:X` | item X has at least one use left (7.13) | item id with `uses` |

`!` negates. A list holds when every condition holds.

### 5.3 Effects

Applied in order.

| Effect | Does | Argument |
|---|---|---|
| `gain:X` | puts item X in the bag (from wherever it is) | item id |
| `lose:X` | removes X from the bag, gone for good (no-op if not carried) | item id |
| `flag:N` | sets flag N | any name |
| `reveal:X` | item X appears in the hero's place, if it was nowhere (`at` `""`) or held by a character (who lets it go) | item id |
| `move:C:P` | character C goes to place P (and stops following) | character, place |
| `follow:C` | C joins the hero and goes wherever the hero goes | character id |
| `goto:P` | the hero (with any companions) goes to P | place id |
| `end:E` | the adventure ends with ending E | ending id |
| `open:X` | opens (and unlocks) container or door X | openable item id |
| `unlock:X` | unlocks X | openable item id |
| `set:X:S` | X's state becomes S | id and one of its state ids |
| `spend:X` | X has one use less (an action that spends X needs `charged:X`, implicitly) | item id with `uses` |
| `refill:X` | X has all its uses again | item id with `uses` |

**A world that only moves forward.** There is no `close`, `lock`, unflag or hide effect, and the
player cannot close, lock or drop things (7.8): what was done stays done. Uses are the one thing
that goes back and forth (`spend`, `refill`, 7.13): the solver counts them, and "never stuck" still
holds (a use that runs out must be refillable, or never needed). The hero is never locked
out, and the state space the checker explores stays small (TextWorld's quests are monotonic too).

## 6. A turn

The narrator turns the player's words into an **intent** `{verb, object?, target?, to?}` (ids or
names) and the engine answers with an **outcome**. The engine never answers "unknown command":

| Outcome | Meaning |
|---|---|
| `done` | It happened (an action, a move, a pickup, an opening, a look, a line said…). May carry `already` when it was already done. |
| `not_yet` | Makes sense but cannot happen now (TADS' *check*): a condition is missing, a door is locked, a way is blocked, a code was wrong. Carries `not_yet`, `blocked` or `texts.locked`. |
| `impossible` | Never possible (TADS' *verify*): taking scenery (`fixed`), destroying something `sturdy`, closing, dropping. |
| `absent` | A thing named is not here (not in the adventure, or elsewhere). Plausible things may be played along with, and never help. |
| `no_exit` | No way out matches. |
| `ask` | Ambiguous: which thing, which way, to whom; carries the choices. |
| `free` | Not modelled and changes nothing (singing, dancing, imagination); told as story. |
| `clue` | An action without effects (a clue or a joke written by the author). |
| `warning` | The first try of an action that would lose: warned once, the player may insist. |
| `hint` | The answer to a request for help. |
| `end` | An ending was reached (`win` or `lose`), with its text. |

The scope of a turn (what can be named) is: the bag; the items in the place and in its open
containers (recursively); the doors of its exits; the characters here and what they hold; the place.

**Matching.** A scripted action is chosen by its triggers: the exact verb first, then a verb of the
same family (apply, talk, look); when the trigger's two things are named, any verb does it (TADS'
remap: "catch the fireflies with the jar" is "fill the jar"), except for verbs that mean something
else (hit, touch, talk, look, go, drop, close, destroy…) and except for losing actions, which answer
their exact verb only. An action is done once (then `done` with `already`) unless it is `repeatable`.
If no action matches, the built-in behaviours apply.

**Hints.** After a few failed tries (policy) or on request, the engine finds the next step of a
shortest win and hints at it in three levels: (1) where the clue is (`puzzles[].clue_in`), else a
general nudge; (2) the author's `hint` (action, `code.hint`, `answer.hint`) or a pointer at the
thing; (3) what to do.

## 7. Built-in behaviours

No action is written for these.

### 7.1 Descriptions (Inform's room description)

A place's description is its composed description, then the `initial` of every visible item that
was never moved and is still where it started (Inform's "initial appearance", lost once "handled"),
then the `initial` of every character here who is not following the hero. States override `name`,
`description`, `initial` and `says`. Examining a container lists what it holds when open.

### 7.2 Taking

Taking a visible `thing` puts it in the bag. Taking scenery, a container or a door answers `fixed`
(`impossible`). Taking what a character holds answers `not_yet` ("C'est Pinces qui l'a"): getting it
is a puzzle. **Implicit taking** (Inform): giving, using, combining, throwing, filling, lighting,
wearing, eating or drinking a thing lying here takes it first.

### 7.3 Opening and unlocking (Inform's opening; the Locksmith extension)

Opening, pulling, pushing or using a closed container or door opens it; so does searching a closed
container, or applying its key to it ("use the key on the door"). A locked one opens only with its
`key` in the bag, which unlocks it first (`texts.unlock`); otherwise `not_yet` with `texts.locked`.
**Implicit opening**: taking a thing inside a closed container here opens the container first when
it can be opened.

### 7.4 Doors (written once, mirrored)

An exit names its `door`. The door is written on one side only: the engine gives the way back the
same door (two different doors on one way is an error). A door needs no `at`; it stands where it is
first named. Going through a closed door opens it on the way when it can (Locksmith included);
otherwise the move answers `not_yet` with `texts.locked` or `texts.closed`.

### 7.5 Containers

An item whose `at` is a container is inside it: seen and reachable only while the container is open.
Opening it reports what it holds.

### 7.6 Code locks (`press`)

`code.press` lists scenery buttons in the order that opens the item. Pressing a button (push, use,
touch, hit, pull, open) plays it: the right next button advances the code (`code.right`), a wrong one
starts again (`code.wrong`; a wrong press that is the first button counts as the first step), the
last one opens the item (`code.done`, else `texts.open`). The order is shown elsewhere in the
adventure (the puzzle's clue). Buttons MUST be scenery, belong to one code only, and never appear in
an action's trigger. A code counts as one puzzle.

### 7.7 Riddles and passwords (`answer`)

Talking to the asker gives the `question`. The player's words are sent with the verb `answer`; they
match when they contain every significant word of one of `accept` (articles and plural marks
ignored). A right answer tells `right`, applies `effects`, and opens the asker if it is a container
or door (a speaking door or chest is locked until then). A wrong answer tells `wrong` and repeats
the question (`not_yet`). An item that asks a riddle MUST be openable or have effects.

### 7.8 What the hero keeps

Closing anything is refused ("better leave it open"), and so is dropping a carried thing: nothing the
adventure needs can be stranded. The hero's bag is built in ("mon sac", "mes poches"): looking in it
or opening it gives the inventory; putting something in it takes that thing.

### 7.9 Destroying, and the built-in end `_coince`

`destroy` (erase, break, throw into the sea) makes a thing gone for good when plausible:

- a `thing` (carried or here) can be destroyed;
- scenery only when `fragile`;
- never a character, a container, a door, or anything `sturdy` (`impossible`, with the `sturdy`
  text as the reason when it is a string); a thing a character holds answers `not_yet`.

Losing a needed thing is allowed: "never stuck" is a promise about the adventure as written, not a
ban on the player. After a destroy the engine checks whether a win is still reachable. If not, the
game is *doomed* (Sierra's "walking dead") but says nothing yet; it ends with the built-in lose
ending `_coince` ("Sans X, impossible d'aller plus loin…") when the player runs into it: a failed
try, a request for help, or several turns without progress. Applications may offer to resume from
just before the destroy.

### 7.10 Clues, jokes and red herrings

An action without effects, or whose `when` needs a flag that no effect ever sets, is a clue or a
joke: told as `clue` (its `text` when its conditions hold, else `not_yet`), never counted as a step
or a failed try, ignored by the solver.

### 7.11 Companions, where-is, decor

A character that `follow`s goes wherever the hero goes (`here:C` keeps holding) until `move`d.
"Where is X?" is answered from what the hero has seen, never guessed. Words naming the place's
`decor` are played along with and never help.

### 7.12 Light and darkness (Inform's darkness)

A `dark` place is dark unless a light shines there: an item with `light` in the bag or on the ground
there, whose conditions hold (`light: ["is:lampe:allumee"]`; `true`: always) and, if it has `uses`,
with one left. In the dark the hero sees nothing: the description is the `dark` text (a generic one
when `dark` is `true`), no item, character or decor is in reach, only the ways out (felt, doors
included), the bag and the companions who follow. Naming a thing there answers `not_yet` ("Il fait
trop noir…"). The ways out always work, so the hero can always go back to the light.

### 7.13 Uses (charges)

An item with `uses: N` can be used N times: an action with `spend:X` needs a use left (an implicit
`charged:X`), and when none is left it answers `not_yet` with `texts.empty`. `refill:X` gives back
all N (doing it when full is already done). A light with uses burns one each time the hero leaves a
dark place it lit (unless a light without uses shone there too); the last one tells `texts.empty`
("La lanterne s'éteint…"). Examining it and the inventory tell the uses left.

### 7.14 Senses

`senses: {listen, smell, touch}` on a place or an item: listening to, smelling or touching it (or
listening with nothing named: here) tells that text when no action is written for it. In the dark,
the place's senses are given to the narrator with the `dark` text: what the hero hears and smells
there. A sense puzzle is an action triggered by `listen`, `smell` or `touch` ("écouter le mur" → a
hiding place).

## 8. What a valid adventure guarantees

Validity has three layers. Tools MUST apply the first two; the engine's checker applies the third.

**1. Structure**: the document validates against `schema/adventure.schema.json` (types, enums,
condition and effect syntax, no unknown fields except `x-`).

**2. References** (the checker reports each as an error):

- ids are unique and non-empty across places, items and characters; action ids are unique;
- `start.place` is a place; `start.inventory` lists items;
- an item's `at` is a place, a container or a character; a character's `at` is a place;
- `key` is an item; a locked or keyed item is a container or door; a door closes at least one exit;
  an exit's `to` is a place and its `door` an openable item; no way has two different doors;
- `texts`, `code`, `answer` and states have only their own fields; state ids are unique per owner;
- every condition and effect names an existing thing of the right sort (`has:` an item, `here:` a
  character, `open:`/`locked:`/`open`/`unlock` an openable item, `is:`/`set:` a declared state,
  `end:` an ending, `goto:`/`at:`/`visited:` and `move:`'s second argument a place);
- the last line of a situated text has no `when`;
- trigger verbs are canonical and scriptable; trigger things are ids; no trigger names a code button
  or only re-does a built-in opening; an action's `place` is a place;
- a code has at least two scenery buttons, each in one code only; a riddle has a question and
  accepted answers;
- puzzles name an action, a coded item or a riddle's asker; `needs` and `clue_in` are ids;
- every ending's kind is `win` or `lose`.

**3. Playability**: the checker explores the whole state space from the start (bounded; an
adventure too big to explore is rejected) and proves that:

- at least one win ending is reachable, and every ending (win or lose) is reachable;
- the hero can never get stuck: from every reachable state that has not ended, a win is still
  reachable (losing is allowed; a lose ending is an end, not a trap);
- **the solver and the turn agree**: every move the solver counts on is replayed through the turn
  engine with the words a player would use, and must change the world the same way (so a scripted
  action can never shadow a built-in behaviour the solver relied on).

An unreachable ending is reported with its reason ("action X needs has:Y, and no effect ever makes
it true"). Warnings, not errors: actions that can never happen, a `clue_in` whose texts name none of
what the puzzle needs, texts that would lie (a place's lasting text naming a character who moves; a
"locked" sentence told while the key may be in hand).

Applications add their own policy on top (for example, per `difficulty`: the number of places and
lose endings, the length and number of puzzles of the shortest win).

## 9. Minimal example

```json
{
  "format": 2,
  "title": "La clé du jardin",
  "goal": "Rapporter la boîte à musique à Mamie.",
  "start": {"place": "jardin"},
  "places": [
    {"id": "jardin", "name": "le jardin", "description": "Un petit jardin plein de fleurs.",
     "exits": [{"to": "cabane", "label": "vers la cabane", "dir": "nord", "door": "porte"}]},
    {"id": "cabane", "name": "la cabane", "description": "Une cabane qui sent le bois.",
     "exits": [{"to": "jardin", "label": "vers le jardin", "dir": "sud"}]}
  ],
  "items": [
    {"id": "buisson", "kind": "scenery", "name": "le buisson", "at": "jardin",
     "description": "Un gros buisson touffu.", "fixed": "Il a de trop longues racines."},
    {"id": "cle", "name": "une petite clé", "aliases": ["clef"],
     "description": "Une petite clé rouillée."},
    {"id": "porte", "kind": "door", "name": "la porte de la cabane",
     "description": "Une porte en bois.", "locked": true, "key": "cle",
     "texts": {"locked": "La porte est fermée à clé.", "unlock": "Clic ! La porte s'ouvre."}},
    {"id": "boite", "name": "une boîte à musique", "at": "cabane",
     "description": "Elle joue une valse.", "initial": "Sur l'étagère, une boîte à musique."}
  ],
  "characters": [
    {"id": "mamie", "name": "Mamie", "at": "jardin", "description": "Mamie, dans son fauteuil.",
     "says": [{"when": ["has:boite"], "text": "Ma boîte à musique ! Donne-la-moi !"},
              {"text": "J'ai perdu la clé de la cabane, quelque part dans le buisson…"}]}
  ],
  "actions": [
    {"id": "fouiller_buisson", "triggers": [{"verb": "search", "object": "buisson"}],
     "effects": ["reveal:cle"],
     "text": "Tu fouilles le buisson : une petite clé tombe par terre !"},
    {"id": "rendre_boite", "triggers": [{"verb": "give", "object": "boite", "target": "mamie"}],
     "when": ["has:boite"], "effects": ["end:merci"], "points": 20,
     "text": "Mamie remonte la boîte, et la valse commence."}
  ],
  "endings": [{"id": "merci", "kind": "win", "text": "Mamie danse avec toi dans le jardin."}],
  "puzzles": [
    {"goal": "Trouver la clé", "action": "fouiller_buisson", "needs": ["buisson"],
     "clue_in": "mamie"}
  ]
}
```

Played: search the bush (the key appears), take the key, go north (the key in the bag unlocks and
opens the door: no action written for it), take the music box, go south, give it to Mamie. The
checker finds one win in 6 steps and no way to get stuck. The door is written once; the way back
gets it automatically.
