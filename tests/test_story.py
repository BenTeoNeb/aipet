"""Stories: real plan, framed length, characters and universes remembered (no network)."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from aipet.games import story as story_mod
from aipet.games.common import Host
from aipet.games.story import STEPS_DAY, STEPS_SHORT, StoryGame
from aipet.memory import Memory
from aipet.rhythm import TZ, Rhythm


class Who:
    def __init__(self, name):
        self.current, self.certainty, self.present = name, "sure", []


class Keeper(Host):
    def __init__(self, name, rhythm=None):
        self.who, self._rhythm = Who(name), rhythm

    @property
    def rhythm(self):
        return self._rhythm


def plan(title, steps, characters=("Plumette",)):
    return {
        "title": title,
        "universe": "Une forêt enchantée",
        "characters": [
            {"name": n, "description": f"{n} la chouette", "trait": "curieuse", "role": "héroïne"} for n in characters
        ],  # fmt: skip
        "steps": [{"event": f"événement {i + 1}", "choice": f"choix {i + 1}"} for i in range(steps)],
        "ending": "tout le monde rentre au chaud",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls = []
    clock = {"t": datetime(2026, 11, 12, 15, 0, tzinfo=TZ)}

    def fake_plan(cfg, idea, who, steps, evening, recurring, past, guests=None):
        calls.append({"idea": idea, "who": who, "steps": steps, "evening": evening, "recurring": recurring,
                      "past": past, "guests": guests})  # fmt: skip
        return plan(f"Histoire {len(calls)}", steps, ("Plumette", f"Ami{len(calls)}"))

    monkeypatch.setattr(story_mod, "generate_plan", fake_plan)
    monkeypatch.setattr(story_mod, "now", lambda: clock["t"])
    mem = Memory(tmp_path)
    return mem, calls, clock


def game(mem, name="Mila", rhythm=None):
    return StoryGame({}, mem.root, Keeper(name, rhythm))


def test_plan_steps_prompt_and_progress(env):
    mem, calls, clock = env
    g = game(mem, rhythm=Rhythm(holidays=[]))
    res = asyncio.run(g.start("un dragon gentil"))
    assert res["steps"] == STEPS_DAY and calls[0]["evening"] is False and calls[0]["idea"] == "un dragon gentil"
    section = g.prompt_section()
    assert "## Histoire en cours : « Histoire 1 »" in section and "→ 1. événement 1 (choix : choix 1)" in section
    assert "Plumette (héroïne)" in section
    assert g.on_user_message("le rouge !") == f"histoire : étape 1 sur {STEPS_DAY}"
    nxt = g.next_step("Plumette trouve une clé", "elle prend la porte rouge")
    assert nxt == {"step": f"2 sur {STEPS_DAY}", "event": "événement 2", "choice": "choix 2"}
    assert "1. événement 1 (choix : choix 1) [fait]" in g.prompt_section()
    assert "Plumette trouve une clé elle prend la porte rouge" in g.prompt_section()
    for _ in range(STEPS_DAY - 3):
        g.next_step("…")
    last = g.next_step("presque")  # the last step: its ending must be told right away
    assert last["step"] == f"{STEPS_DAY} sur {STEPS_DAY}" and "Dernière étape" in last["note"]
    assert "dernière étape" in g.next_step("fin proche")["note"]
    clock["t"] += timedelta(minutes=16)
    assert "il est temps de conclure" in g.on_user_message("et après ?")


def test_evening_and_little_ones_are_shorter(env):
    mem, calls, clock = env
    clock["t"] = datetime(2026, 11, 12, 19, 30, tzinfo=TZ)  # 1 h before bedtime
    asyncio.run(game(mem, rhythm=Rhythm(holidays=[])).start(""))
    assert calls[-1]["evening"] is True and calls[-1]["steps"] == STEPS_SHORT
    clock["t"] = datetime(2026, 11, 12, 15, 0, tzinfo=TZ)
    mem.apply({"memory": {"participants": [], "topics": [], "summary": ""},
               "people": [{"name": "Noam", "age": "6 ans", "role": "enfant", "likes": [], "dislikes": [], "facts": [],
                           "school_class": "CP", "school_topics": [], "remove": []}],
               "stories": [], "pet_life": {}, "pet_summary": ""}, clock["t"])  # fmt: skip
    asyncio.run(game(mem, "Noam").start("des pirates"))
    assert calls[-1]["steps"] == STEPS_SHORT and "Noam, en CP" == calls[-1]["who"]


def test_end_remembers_reaction_and_recurring_characters_grow(env):
    mem, calls, _ = env
    g = game(mem)
    asyncio.run(g.start("une chouette"))
    g.end("elle a adoré", ["Plumette"], [{"name": "Plumette", "new_trait": "a appris à voler la nuit"}])
    assert g.story is None and g.prompt_section() == ""
    data = g.store.load()
    plumette = data["characters"]["plumette"]
    assert plumette["appearances"] == 1 and plumette["liked_by"] == {"Mila": 1}
    assert plumette["traits"] == ["curieuse", "a appris à voler la nuit"]
    assert data["stories"][0]["reaction"] == "elle a adoré" and data["stories"][0]["finished"]
    asyncio.run(g.start("une autre histoire"))  # the loved character is offered first to the planner
    assert (
        calls[1]["recurring"][0].startswith("Plumette : Plumette la chouette") and "adoré" in calls[1]["recurring"][0]
    )
    assert calls[1]["past"] == ["Histoire 1"]
    assert g.store.load()["characters"]["plumette"]["appearances"] == 2


def test_unfinished_story_is_kept_and_resumed(env):
    mem, _, _ = env
    g = game(mem)
    asyncio.run(g.start("un robot"))
    g.next_step("le robot se réveille", "il veut voir la mer")
    g.stop()
    assert g.story is None and g.unfinished("Mila")["step"] == 1
    assert "error" in asyncio.run(game(mem, "Noam").start(resume=True))  # nothing to resume for Noam
    g2 = game(mem)
    res = asyncio.run(g2.start(resume=True))
    assert res["resumed"] == "Histoire 1" and res["step"] == f"2 sur {STEPS_DAY}"
    assert "le robot se réveille il veut voir la mer" in g2.prompt_section()


def test_plan_failure_falls_back(env, monkeypatch):
    mem, _, _ = env

    def boom(*a):
        raise RuntimeError("model down")

    monkeypatch.setattr(story_mod, "generate_plan", boom)
    g = game(mem)
    assert "error" in asyncio.run(g.start("un chat")) and g.story is None
    assert "error" in g.next_step("x") and "error" in g.end("bof")


def test_duplicate_step_is_rejected(env):
    mem, _, _ = env
    g = game(mem)
    asyncio.run(g.start("un robot"))
    assert g.next_step("le robot se réveille", step_done=1)["step"] == f"2 sur {STEPS_DAY}"
    assert "déjà passée" in g.next_step("le robot se réveille", step_done=1)["error"]
    assert g.story["step"] == 1


# ---- real people: guests of a story, never recurring characters --------------------------
def test_real_people_are_guests_never_characters(env, monkeypatch):
    """Seen in a real session: « Noam, un petit lapin », « Mila, une souris » and Flocon were saved
    as recurring invented characters."""
    mem, calls, clock = env
    (mem.root / "famille").mkdir(exist_ok=True)
    (mem.root / "famille" / "mila.md").write_text("---\nprenom: Mila\n---\n")

    def mixed_plan(cfg, idea, who, steps, evening, recurring, past, guests=None):
        calls.append({"guests": guests})
        p = plan("Le gâteau", steps, ("Piko", "Noam le lapin", "Mila", "Flocon"))
        p["guests"] = [{"name": "Noam", "role": "un petit lapin"}, {"name": "Piko", "role": "faux invité"}]
        return p

    monkeypatch.setattr(story_mod, "generate_plan", mixed_plan)
    g = game(mem, "Noam")
    g.host.who.present = ["Noam", "Mila"]
    asyncio.run(g.start("un gâteau"))
    assert calls[-1]["guests"] == ["Noam", "Mila", "Flocon"]  # first story: guests allowed
    assert [c["name"] for c in g.story["plan"]["characters"]] == ["Piko"]
    assert g.story["plan"]["guests"] == [{"name": "Noam", "role": "un petit lapin"}]  # Piko isn't real
    section = g.prompt_section()
    assert "- Noam : un petit lapin" in section and "pour cette histoire seulement" in section
    g.end("il a adoré", ["Noam", "Mila", "Piko"], [{"name": "Mila", "new_trait": "rit fort"}])
    chars = json.loads(mem.root.joinpath("histoires", "univers.json").read_text())["characters"]
    assert list(chars) == ["piko"] and chars["piko"]["liked_by"] == {"Noam": 1}
    # Next stories: no guests (at most one in GUEST_EVERY)…
    asyncio.run(g.start("un dragon"))
    assert calls[-1]["guests"] is None and g.story["plan"]["guests"] == []
    assert "ne sont pas des personnages" in g.prompt_section() or "n'êtes pas des personnages" in g.prompt_section()
    g.end("bien")
    # … unless the child asks.
    asyncio.run(g.start("moi en pirate", with_children=True))
    assert calls[-1]["guests"] == ["Noam", "Mila", "Flocon"]


def test_a_preparation_stopped_meanwhile_starts_nothing(env, monkeypatch):
    """Preparations are asynchronous (a child talking no longer cancels them): one whose activity
    was stopped, or replaced by a newer start, must not start it when its plan arrives."""
    import threading

    mem, calls, clock = env
    release = threading.Event()

    def slow_plan(*a, **kw):
        release.wait(2)
        return plan("Lente", STEPS_DAY)

    monkeypatch.setattr(story_mod, "generate_plan", slow_plan)
    g = game(mem)

    async def run():
        task = asyncio.create_task(g.start("un loup gentil"))
        await asyncio.sleep(0.05)
        g.stop()  # the activity ends while the plan is being written
        release.set()
        return await task

    res = asyncio.run(run())
    assert res.get("cancelled") and g.story is None
    release.clear()
    res = asyncio.run(_two_starts(g, release))
    assert g.story["title"] == "Lente" and res[0].get("cancelled") and "title" in res[1]


def test_resuming_cancels_a_story_being_prepared(env, monkeypatch):
    import threading

    mem, _, _ = env
    g = game(mem)
    asyncio.run(g.start("un robot"))
    g.stop()  # « Histoire 1 » is unfinished
    release = threading.Event()

    def slow_plan(*a, **kw):
        release.wait(2)
        return plan("Nouvelle", STEPS_DAY)

    monkeypatch.setattr(story_mod, "generate_plan", slow_plan)

    async def run():
        new = asyncio.create_task(g.start("un dragon"))
        await asyncio.sleep(0.05)
        resumed = await g.start(resume=True)  # « non, la suite ! »
        release.set()
        return resumed, await new

    resumed, new = asyncio.run(run())
    assert resumed["resumed"] == "Histoire 1" and new.get("cancelled")
    assert g.story["title"] == "Histoire 1"


async def _two_starts(g, release):
    first = asyncio.create_task(g.start("un loup"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(g.start("un loup et une petite fille"))  # the newer one wins
    await asyncio.sleep(0.05)
    release.set()
    return await asyncio.gather(first, second)


def test_preparation_tools_are_asynchronous():
    from aipet.games import learn, quiz, yes_no
    from aipet.games.common import Activity

    seen = {}

    class LLM:
        def register_function(self, name, handler, **kw):
            seen[name] = kw.get("cancel_on_interruption", True)

    for mod in (story_mod, quiz, learn, yes_no):
        mod.register_tools(LLM(), Activity())
    assert {n: seen[n] for n in ("story_start", "quiz_start", "learn_start", "yesno_start")} == dict.fromkeys(
        ("story_start", "quiz_start", "learn_start", "yesno_start"), False
    )
    assert seen["story_next"] is True and seen["quiz_answer"] is True


def test_story_names_for_speech_recognition(env):
    """« Raya » was heard as « Royal », « Plumette » as « Plunette »: the names go to the transcription."""
    mem, _, _ = env
    g = game(mem)
    assert g.names() == []
    asyncio.run(g.start("un dragon"))
    assert g.names()[:2] == ["Plumette", "Ami1"]


def test_story_end_suggests_something_else(env):
    """After a story, the pet offered another story: the end now carries the code's suggestion."""
    mem, _, _ = env
    g = game(mem)
    asyncio.run(g.start("un dragon"))
    assert "propose" not in g.end("il a aimé")  # a game on its own: no suggestion
    g.host.next_offer = lambda: "si tu proposes une activité, propose d'abord un quiz"
    asyncio.run(g.start("un autre dragon"))
    assert g.end("il a adoré")["propose"].endswith("un quiz")
