"""Skills: reading SKILL.md files, catalog, active skill, tools."""

import asyncio
import os
import time
import types
from datetime import timedelta

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from aipet import config, memory, prompts, skills
from aipet.conversation import Conversation
from aipet.memory import Memory
from aipet.skills import Skills, read_skill


def write(folder, name, description="Une activité.", instructions="Fais ceci.", header_name=None):
    d = folder / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {header_name or name}\ndescription: {description}\n---\n{instructions}\n")
    return d / "SKILL.md"


@pytest.fixture
def sk(tmp_path):
    shipped, family = tmp_path / "shipped", tmp_path / "family"
    write(shipped, "quiz", "Un quiz.", "Cinq questions.")
    write(shipped, "story", "Une histoire.", "Raconte doucement.")
    return Skills([shipped, family])


@pytest.fixture
def keeper(tmp_path, sk):
    k = Conversation(Memory(tmp_path / "mem"), {"memory": {}}, LLMContext(), "BASE", max_turns=99)
    k.skills = sk
    return k


class FakeLLM:
    def __init__(self):
        self.functions = {}

    def register_function(self, name, f):
        self.functions[name] = f


async def call(llm, tool, **arguments):
    out = {}

    async def cb(res):
        out["res"] = res

    await llm.functions[tool](types.SimpleNamespace(arguments=arguments, result_callback=cb))
    return out["res"]


# --- reading ---------------------------------------------------------------------------------
def test_shipped_skills_are_valid():
    s = Skills.from_config({"skills": {"family_dir": "/nowhere"}})
    assert {"quiz", "story", "yes-no-game", "guess-what-im-thinking"} <= set(s.all()) and not s.errors


@pytest.mark.parametrize(
    "problem,kw",
    [
        ("invalid", {"header_name": "Quiz Génial"}),
        ("differs from the folder name", {"header_name": "other"}),
        ("description", {"description": ""}),
        ("instructions", {"instructions": ""}),
        ("description", {"description": "x" * 400}),
    ],
)
def test_invalid_skill(tmp_path, problem, kw):
    p = write(tmp_path, "quiz", **kw)
    with pytest.raises(ValueError, match=problem):
        read_skill(p)


def test_missing_header(tmp_path):
    (tmp_path / "quiz").mkdir()
    (tmp_path / "quiz" / "SKILL.md").write_text("just text")
    with pytest.raises(ValueError, match="header"):
        read_skill(tmp_path / "quiz" / "SKILL.md")


def test_invalid_skill_ignored_without_blocking_others(sk, tmp_path):
    write(tmp_path / "family", "broken", description="")
    assert set(sk.all()) == {"quiz", "story"}
    assert any("broken" in f for f in sk.errors)


def test_family_skill_replaces_shipped_one(sk, tmp_path):
    write(tmp_path / "family", "quiz", "Notre quiz.", "Dix questions sur les dinosaures.")
    assert sk.all()["quiz"].instructions == "Dix questions sur les dinosaures."


def test_add_and_edit_without_restart(sk, tmp_path):
    assert "riddles" not in sk.all()
    p = write(tmp_path / "family", "riddles", "Des devinettes.", "v1")
    assert sk.all()["riddles"].instructions == "v1"
    p.write_text("---\nname: riddles\ndescription: Des devinettes.\n---\nv2\n")
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    assert sk.all()["riddles"].instructions == "v2"


def test_deleted_skill_is_no_longer_active(sk, tmp_path):
    p = write(tmp_path / "family", "riddles")
    sk.use("riddles")
    p.unlink()
    assert sk.all() and sk.active is None and sk.active_instructions() == ""


# --- instructions ----------------------------------------------------------------------------
def test_catalog_only_has_descriptions(keeper):
    p = keeper.system_prompt()
    assert "- quiz : Un quiz." in p and "use_skill" in p
    assert "Cinq questions." not in p  # progressive disclosure


def test_active_skill_in_system_prompt(keeper):
    res = keeper.skills.use("Quiz ")
    assert res["instructions"] == "Cinq questions." and keeper.skills.active == "quiz"
    p = keeper.system_prompt()
    assert "ACTIVITÉ EN COURS : quiz" in p and "Cinq questions." in p and "ne les remplacent jamais" in p
    # after the memory block: the fixed rules come first and win
    assert p.index("Cinq questions.") > p.index("FIN DE TA MÉMOIRE")
    keeper.skills.end()
    assert "Cinq questions." not in keeper.system_prompt()


def test_unknown_skill(sk):
    res = sk.use("chess")
    assert "inconnue" in res["error"] and res["available"] == ["quiz", "story"] and sk.active is None


def test_one_skill_at_a_time(sk):
    sk.use("quiz")
    sk.use("story")
    assert sk.active == "story"


def test_automatic_end(keeper):
    async def go():
        keeper.skills.use("quiz")
        keeper.reset_context()
        assert keeper.skills.active is None
        keeper.skills.use("quiz")
        keeper.on_sleep()  # a short sleep keeps the activity (the long-pause rule ends it)
        assert keeper.skills.active == "quiz"

    asyncio.run(go())


def test_end_after_long_pause(keeper):
    async def go():
        keeper.skills.use("quiz")
        keeper.sleep.last_activity = memory.now() - timedelta(minutes=keeper.forget_after_min + 1)
        await keeper.on_wake()
        assert keeper.skills.active is None
        keeper.skills.use("quiz")
        await keeper.on_wake()  # short pause: the skill goes on
        assert keeper.skills.active == "quiz"

    asyncio.run(go())


# --- tools -----------------------------------------------------------------------------------
def test_tools(keeper):
    async def go():
        llm = FakeLLM()
        schemas = skills.register_tools(llm, keeper)
        assert {s.name for s in schemas} == skills.SKILL_TOOLS
        refreshed = []

        async def refresh():
            refreshed.append(keeper.skills.active)

        keeper.refresh_prompt = refresh
        res = await call(llm, "use_skill", name="quiz")
        assert res["skill"] == "quiz" and refreshed == ["quiz"]
        assert (await call(llm, "end_skill"))["skill_ended"] == "quiz" and refreshed[-1] is None
        assert "aucune" in (await call(llm, "end_skill"))["note"]

    asyncio.run(go())


def test_budget_shared_with_memory_tools(keeper):
    async def go():
        llm = FakeLLM()
        skills.register_tools(llm, keeper)

        async def refresh():
            pass

        keeper.refresh_prompt = refresh
        keeper.tools.calls = memory.MAX_TOOLS_PER_TURN
        res = await call(llm, "use_skill", name="quiz")
        assert res == memory.TOOLS_EXHAUSTED and keeper.skills.active is None

    asyncio.run(go())


def test_startup_check_accepts_skill_tools(keeper):
    prompt = prompts.build_system_prompt(config.load_config()) + keeper.system_prompt()
    names = memory.MEMORY_TOOLS | skills.SKILL_TOOLS
    tools = types.SimpleNamespace(standard_tools=[types.SimpleNamespace(name=x) for x in names])
    prompts.check_safety(tools, prompt, names)


# ---- tools per activity ------------------------------------------------------------------
def test_skill_header_declares_tools(tmp_path):
    d = tmp_path / "jeu"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: jeu\ndescription: Un jeu.\ntools: outil_a, outil_b\n---\nJoue.\n")
    assert read_skill(d / "SKILL.md").tools == ("outil_a", "outil_b")


def test_builtin_skills_declare_existing_tools():
    from aipet.games import guess, learn, quiz, story, yes_no

    every_tool = quiz.QUIZ_TOOLS | yes_no.YES_NO_TOOLS | guess.GUESS_TOOLS | story.STORY_TOOLS | learn.LEARN_TOOLS
    builtin = Skills.from_config({"skills": {"family_dir": "/nowhere"}}).all()
    assert all(set(s.tools) <= every_tool for s in builtin.values())
    assert set(builtin["quiz"].tools) == quiz.QUIZ_TOOLS and set(builtin["story"].tools) == story.STORY_TOOLS


def test_tools_follow_the_running_activity(keeper, tmp_path):
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    def schema(name):
        return FunctionSchema(name=name, description=name, properties={}, required=[])

    write(tmp_path / "family", "jeu", "Un jeu.", "Joue.")
    (tmp_path / "family" / "jeu" / "SKILL.md").write_text(
        "---\nname: jeu\ndescription: Un jeu.\ntools: outil_jeu, outil_fantome\n---\nJoue.\n"
    )
    names = ["search_memory", "use_skill", "end_skill", "outil_jeu", "outil_quiz"]
    keeper.tools.schemas = {n: schema(n) for n in names}
    keeper.tools.base = {"search_memory", "use_skill", "end_skill"}

    def sent():
        return {t.name for t in keeper.context.tools.standard_tools}

    keeper.tools.apply(())
    assert sent() == keeper.tools.base

    async def go():
        llm = FakeLLM()
        skills.register_tools(llm, keeper)

        async def refresh():
            pass

        keeper.refresh_prompt = refresh
        await call(llm, "use_skill", name="jeu")
        assert sent() == keeper.tools.base | {"outil_jeu"}  # unknown « outil_fantome » ignored
        await call(llm, "end_skill")
        assert sent() == keeper.tools.base
        await call(llm, "use_skill", name="jeu")
        keeper.on_sleep()  # a short sleep keeps the activity and its tools
        assert sent() == keeper.tools.base | {"outil_jeu"}
        keeper.reset_context()  # a reset ends them
        assert sent() == keeper.tools.base

    asyncio.run(go())


def test_switching_activity_stops_the_previous_game(keeper):
    class Game:
        stopped = 0

        def stop(self):
            Game.stopped += 1

    keeper.games = [Game()]

    async def go():
        llm = FakeLLM()
        skills.register_tools(llm, keeper)

        async def refresh():
            pass

        keeper.refresh_prompt = refresh
        await call(llm, "use_skill", name="quiz")
        assert Game.stopped == 0  # nothing to replace
        await call(llm, "use_skill", name="story")  # quiz → story: the quiz game stops
        assert Game.stopped == 1

    asyncio.run(go())
