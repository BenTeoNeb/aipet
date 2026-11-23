# Changelog

Notable changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## [Unreleased]

### Added
- Adventures where the child is the hero (`adventure_start`, `adventure_act`, `adventure_end`): a bank
  of 15 hand-written adventures in three difficulties, new ones invented on the child's idea, resume,
  replay and « sauvegardes » after a loss. Code locks, riddles, companions, things held by
  characters, containers and doors with keys, destroying what is plausible, memories of what the
  child did, « où est… ? », hints that point at the clue.
- `storyworld`: the adventure engine as a library (world model, turn, solver, checker), with a
  written format (`SPEC.md`), a JSON Schema, structured diagnostics and French as its first language.
- `/adventures` and `pet-adventures`: an explorer page (map, puzzle chain, the data model, games
  played and replayed through the engine). `pet-playtest`: a simulated child plays an adventure
  against the real narrator, with a judge for the narrator's slips.
- Activity suggestions: the pet proposes something new or not done for a while, and something the
  child likes. Story characters' names are given to speech recognition.
- Riddles in four bank adventures (Grognon's bridge, the Viking runes, the lantern riddle, the
  princess's Sphinx-like riddle) and recipes, concrete and silly (a smelly mitten for the goat, a
  peppered scarf that makes the guard sneeze, a papyrus mouse for the cat), each a second way to
  solve a puzzle or a harmless joke. Three recipe patterns in the catalogue.

### Fixed
- The solver now takes what an action's words name, as the turn does (combining with something
  lying here), and a flag set by a riddle's answer counts as reachable.
- `pet-headless` crashed at startup (it read a config key that does not exist).
- A forget request could be undone by a condensation running at the same time, and missed the
  pet's life file, the stories, the card history and the game records; the forgotten words were
  also logged again.
- The web page and the voice pipeline could write the memory at the same time; a parent deleting
  a file could reach the memory's `.git` folder; a corrupt game file was replaced by an empty one.
- A deleted confidence stayed readable in the memory's git history (and in the commit message).
- Parents' and debug pages could block the audio while reading logs; the school-holiday calendar
  was fetched on the audio event loop.
- Closing an old browser tab cleared the newer tab's session.
- `search_memory` found the pending notes, which quote forget requests word for word.
- A forget request or a parent's correction did not reach the conversation in progress (context,
  system prompt); Pipecat's debug lines copied what was said into the log file.
- A one-word forget about someone only cleaned their card; any parent correction (not only a
  deletion) now wipes the git history; a condensation running during a parent's correction could
  write the old text back; a card deleted by a parent came back at the next condensation.
- A cough or a dropped transcript got « Hmm… » then an apology; the end of a filler cancelled an
  `[attends]` wait; a tool loop kept pushing the turn's deadline back.
- Guess game: a yes to a property question (« c'est rouge ? ») counted as a win.
- Spoken-text cleanup cut « 4. » or « - » in mid-sentence; « 7 * 5 » split across chunks.
- A cancelled tool call kept the Pi's microphone open for up to 60 s; `[parle:Marie Lou]` was spoken.
- A slow school-holiday server delayed the wake (and was retried on every wake); the cache file
  was not written atomically.
- The "retry if the model is slow" never fired through OpenRouter (it only bounded the headers).
- Long stories recharged the pet's energy instead of draining it.
- On the Pi, a noise or a background voice just after the child spoke cancelled every reply before
  a sound came out (« bip, puis rien »). Between the child's turn and the pet's reply, voice alone no
  longer starts a turn: the child who goes on talking still cuts in once words are transcribed.
- A tired pet said goodbye for its nap but never hung up (no `[fin]` when the pet itself ends the
  conversation), and answered the next « d'accord ? » as if awake: its goodbye now carries `[fin]`,
  and a goodbye while very tired starts a real nap (from its current level, never during a sad
  moment). Merely tired, the child can still call it back.
- Resuming a story while another was being prepared let the new one replace it; preparations had
  no overall time limit; a goodbye after a recharge hung up after 1 s.

### Security
- Debug and parents' routes answer connections from this machine only, even with `--host 0.0.0.0`.
- `read_memory` reads memory files only (never git internals, state files or pending notes).

### Changed
- Each person has a long card (the record, which keeps growing: history and school topics are no
  longer cut) and a short card (`fiches_courtes/`, at most 700 characters) that is the only one in
  the prompt. A model rewrites the short card after a condensation when it is a week old or five
  items behind; until then the newest items are shown under it. Forget requests and parents'
  corrections drop it (a trimmed long card stands in until the next rewrite).
- The memory is split into small modules (`cards`, `updates`, `forget`, `repo`, `text`); the games
  share their tool handlers and background preparations; `build_worker` is split into builders.
- Reading the pet's energy no longer changes it: a rested pet wakes up at its next activity.
- CI runs mypy and enforces a coverage floor; actions are pinned; Dependabot keeps them current.

## 2026-11-14

First public version: Flocon and Plume, long-term memory with parents' corrections, games refereed
by code, a sense of time and tiredness, hanging up at night or on goodbye, the parents' space.
