# Changelog

Notable changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## [Unreleased]

### Fixed
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
- Resuming a story while another was being prepared let the new one replace it; preparations had
  no overall time limit; a goodbye after a recharge hung up after 1 s.

### Security
- Debug and parents' routes answer connections from this machine only, even with `--host 0.0.0.0`.
- `read_memory` reads memory files only (never git internals, state files or pending notes).

### Changed
- The memory is split into small modules (`cards`, `updates`, `forget`, `repo`, `text`); the games
  share their tool handlers and background preparations; `build_worker` is split into builders.
- Reading the pet's energy no longer changes it: a rested pet wakes up at its next activity.
- CI runs mypy and enforces a coverage floor; actions are pinned; Dependabot keeps them current.

## 2026-11-14

First public version: Flocon and Plume, long-term memory with parents' corrections, games refereed
by code, a sense of time and tiredness, hanging up at night or on goodbye, the parents' space.
