# Intent benchmark

Can a fast decision model turn what a child says during an adventure into the right engine
actions, so that the narrator only has to tell the story (one model call per turn instead of two)?

## The set

Each case is a child's sentence attached to a real state of the engine (`state`), with what the
narrator just said (`told`), the child's previous sentence (`before`), and the truth (`gold`): the
actions a careful game master would send, by ids, or `hors_jeu` (chit-chat) or `preciser` (too
ambiguous: ask the child). The truth was written by Claude Opus agents following
[LABELING.md](LABELING.md), and every label was played in the engine (`validate.py`).

| | cases |
|---|---|
| sentences written by Opus, 5 per situation, 120 situations of the 15 bank adventures (`situations.py`) | 600 |
| a simulated child's sentences (`pet-playtest`), replayed in the engine | 57 |
| real children's sentences | 44, **kept out of this public repository** (`~/.aipet/intent-bench/`) |

`dev.json` (to tune the questions) and `test.json` (measured once, at the end) never share a
situation. `dbl_gold.json`: 70 cases labelled a second time by an independent agent: the two
labellings agree on 66/70 (94 %), the ceiling worth aiming for.

A decision is **right** when playing it leaves the world in the same state *and* tells the child the
same thing as the truth (so `use` or `push` on a shell button are both right); for `hors_jeu` and
`preciser`, when it plays no action.

## Results (24/11/2026, all 701 cases, then the test set)

The questions that worked (`bench.py`, variant `v5`): how many actions (0, 1, 2, 3, or « pas clair »),
then for each action its verb, thing, target and place, each a choice among this turn's ids (things
in reach, ways, places already seen), with the engine's conventions spelled out. Afterwards each verb
keeps only its fields, and an answer to a riddle is the child's own words.

| on the 288 test cases | right overall | decides (confidence ≥ 0.7) | right when it decides | median latency | cost / turn |
|---|---|---|---|---|---|
| **TypeSafe Jev 1.14** | 83.7 % | **50 %** of the turns | **97.2 %** | 0.42 s | ≈ $0.0002 |
| OpenAI GPT-6 Luna Decisions | 74.0 % | 22 % | 96.9 % | 0.61 s | ≈ $0.0004 |
| Jev ≥ 0.7, else Luna ≥ 0.8 | | 52 % | 97.3 % | | |

At confidence ≥ 0.8, Jev decides 37 % of the turns and was right on all of them. Combining with
Luna adds little. Without the real children's sentences (the public set), Jev at 0.7: 51 %, 97.1 %.

What a fixed-choice model cannot do: name a thing that is not in reach yet (« je vais au campement
et je prends la torche »: the torch is at the camp), or more than 3 actions: about 6 % of the set.
It rarely is confident on those.

## Running it

```sh
uv run python docs/adventure/intent-bench/bench.py docs/adventure/intent-bench/dev.json typesafe/jev-1.14 v5
uv run python docs/adventure/intent-bench/ensemble.py docs/adventure/intent-bench/dev.json v5   # after both models
```

Answers are cached per request in `~/.aipet/cache/intent-bench/` (a rerun costs nothing). The
Decisions API is OpenRouter's (`POST /api/alpha/decisions`); Jev is called through its zero data
retention endpoint.
