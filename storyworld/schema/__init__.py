"""The format's JSON Schema (draft 2020-12): `adventure.schema.json`."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("adventure.schema.json")


@cache
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_errors(graph: dict) -> list[tuple[str, str]]:
    """(JSON pointer, message) for each place the graph breaks the schema."""
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema())
    out = []
    for e in sorted(validator.iter_errors(graph), key=lambda e: list(e.absolute_path)):
        pointer = "/" + "/".join(str(p) for p in e.absolute_path)
        out.append((pointer, e.message[:200]))
    return out
