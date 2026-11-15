# Contributing to aipet

Thanks for your interest! Issues and pull requests are welcome: bug reports, new pets, new
activities, other languages, better turn-taking.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- aipet talks with children. Changes to prompts, guardrails, memory or anything a child hears are
  reviewed with that in mind: safety and honesty (the pet never pretends to be alive) come first.

## Conventions

- **Language:** code, comments, docstrings, config keys and commit messages are in English. Text the
  model reads or the child hears (prompts, personas, spoken lines, `SKILL.md`) is in French.
- **Never commit real children's data:** no real names, voices, transcripts or memory files, not even
  in tests. Use made-up names (Léa, Mila, Noam, Tom…).
- **Tests cost nothing:** they must not call any real model or API (network is blocked in
  `tests/conftest.py`). Fake the service instead.

## Workflow

```bash
uv sync
uvx pre-commit install          # optional: the same checks before each commit
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                   # with coverage; CI fails below the floor in pyproject.toml
```

Please follow the [code of conduct](CODE_OF_CONDUCT.md), and add a line to
[CHANGELOG.md](CHANGELOG.md) under « Unreleased » for anything a user or a parent would notice.

Keep pull requests focused, with tests for new behaviour. A new pet only needs a folder in
`characters/` and a passing test suite.

## License of contributions

aipet is licensed under the AGPL-3.0 and also offered under a commercial license. By submitting a
contribution, you agree that it is licensed under the AGPL-3.0 **and** that Benjamin Farcy may also
distribute it under other licenses, including commercial ones. You confirm that you have the right
to make the contribution.
