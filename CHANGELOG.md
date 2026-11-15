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
