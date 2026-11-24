"""An adventure is checked once per version of the engine: the verdict is kept (memory and the cache
folder), and a graph or an engine changed is checked again."""

import json

from aipet.games import adventure_engine as eng
from aipet.games.adventure import library


def counting(monkeypatch):
    calls = []
    real = eng.check

    def check(graph):
        calls.append(graph.get("title"))
        return real(graph)

    monkeypatch.setattr(eng, "check", check)
    monkeypatch.setattr(eng, "_memo", {})
    return calls


def test_a_verdict_is_kept_across_processes(monkeypatch):
    calls = counting(monkeypatch)
    graph = next(g for g in library() if g["difficulty"] == "facile")
    assert eng.errors_of(graph) == [] and eng.errors_of(graph) == []
    assert len(calls) == 1
    monkeypatch.setattr(eng, "_memo", {})  # a new process: read back from the cache folder
    assert eng.errors_of(graph) == [] and len(calls) == 1


def test_a_changed_graph_or_engine_is_checked_again(monkeypatch):
    calls = counting(monkeypatch)
    graph = next(g for g in library() if g["difficulty"] == "facile")
    eng.errors_of(graph)
    broken = json.loads(json.dumps(graph))
    broken["start"]["place"] = "nulle_part"
    assert eng.errors_of(broken)  # a damaged record is still refused
    monkeypatch.setattr(eng, "engine_hash", lambda: "another-engine")
    eng.errors_of(graph)
    assert len(calls) == 3
