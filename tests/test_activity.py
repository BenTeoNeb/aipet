"""What every game shares: tool handlers with the per-turn budget, slow preparations that can be
cancelled, difficulty steps (no network)."""

import asyncio

from aipet.games.common import STALE_PREPARATION, Activity, Host, adjust_difficulty, level_change
from aipet.tool_gate import TOOLS_EXHAUSTED


class Params:
    def __init__(self, **arguments):
        self.arguments, self.results = arguments, []

    async def result_callback(self, r):
        self.results.append(r)


def test_tool_passes_arguments_and_counts_the_budget():
    class Tired(Host):
        def tool_budget_exhausted(self):
            return True

    game = Activity(Tired())

    async def start(a):
        return {"started": a["theme"]}

    p = Params(theme="espace")
    asyncio.run(game.tool(start)(p))
    asyncio.run(game.tool(start, budget=True)(p))
    assert p.results == [{"started": "espace"}, TOOLS_EXHAUSTED]


def test_preparation_failed_or_stale():
    game = Activity()

    def boom():
        raise RuntimeError("model down")

    _, refused = asyncio.run(game.prepare(boom, failed="Pas pu préparer."))
    assert refused == {"error": "Pas pu préparer."}

    async def stopped_meanwhile():
        task = asyncio.create_task(game.prepare(lambda: "plan", failed="x"))
        await asyncio.sleep(0)
        game.cancel_preparation()  # the child said « stop » during the model call
        return await task

    assert asyncio.run(stopped_meanwhile()) == (None, STALE_PREPARATION)
    assert asyncio.run(game.prepare(lambda: "plan", failed="x")) == ("plan", None)


def test_a_preparation_that_never_ends_is_given_up(monkeypatch):
    import threading

    from aipet.games import common

    monkeypatch.setattr(common, "PREPARE_TIMEOUT_SECS", 0.05)
    release = threading.Event()
    _, refused = asyncio.run(Activity().prepare(lambda: release.wait(2), failed="Trop long."))
    release.set()
    assert refused == {"error": "Trop long."}


def test_difficulty_steps():
    assert adjust_difficulty(5, up=True, down=False) == 5
    assert adjust_difficulty(1, up=False, down=True) == 1
    assert adjust_difficulty(3, up=True, down=False) == 4 and level_change(3, 4) == "up"
    assert level_change(2, 2) is None
