"""Meaningless transcripts dropped, text cleaned before TTS, startup safety checks."""

import asyncio
import types

import pytest

from aipet import cleanup, config, memory, prompts
from aipet.cleanup import clean_spoken, valid_transcript
from aipet.tool_gate import ToolGate


@pytest.mark.parametrize(
    "t",
    [
        "...",
        "?!",
        "a",
        "",
        "  ",
        "oui oui oui oui oui oui",
        "Sous-titres réalisés par la communauté d'Amara.org",
        "Merci d'avoir regardé !",
    ],
)
def test_meaningless_transcripts_dropped(t):
    assert not valid_transcript(t)


@pytest.mark.parametrize(
    "t", ["Oui.", "non non", "vert", "ok", "Il toque trois petits coups.", "bébé robot", "oui oui oui", "la 4"]
)
def test_transcripts_kept(t):
    assert valid_transcript(t)  # "la 4": up to the model to judge ([silence])


def test_cleans_markdown_and_emojis():
    assert clean_spoken("**Bravo** ! 🎉 Tu as *trouvé* !") == "Bravo !  Tu as trouvé !"
    assert clean_spoken("*ronronne* Coucou") == "ronronne Coucou"
    assert clean_spoken("7 * 5 = 35 et 3×4") == "7 fois 5 = 35 et 3 fois 4"
    assert clean_spoken("- une pomme\n- une poire", at_line_start=True) == "une pomme\nune poire"
    assert clean_spoken("## Titre\nTexte", at_line_start=True) == "Titre\nTexte"
    assert clean_spoken("un arc-en-ciel - joli") == "un arc-en-ciel - joli"  # dashes inside a sentence are kept
    assert clean_spoken("[joie] Oh !") == "[joie] Oh !"  # tags untouched


def test_filter_drops_without_calling_the_model(monkeypatch):
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    async def noop(self, frame, direction):
        return None

    monkeypatch.setattr(FrameProcessor, "process_frame", noop)
    f = cleanup.TranscriptFilter()
    out = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        out.append(frame)

    f.push_frame = push
    bad, good = (
        TranscriptionFrame(text="...", user_id="", timestamp=""),
        TranscriptionFrame(text="vert", user_id="", timestamp=""),
    )
    asyncio.run(f.process_frame(bad, FrameDirection.DOWNSTREAM))
    asyncio.run(f.process_frame(good, FrameDirection.DOWNSTREAM))
    assert out == [good]


def test_tool_budget_per_turn(tmp_path):
    mem = memory.Memory(tmp_path)
    gate = ToolGate(context=None)
    handlers = {}

    class FakeLLM:
        def register_function(self, name, handler):
            handlers[name] = handler

    memory.register_tools(FakeLLM(), mem, gate.count_call)
    results = []

    class P:
        arguments = {"query": "chat"}

        async def result_callback(self, r):
            results.append(r)

    for _ in range(memory.MAX_TOOLS_PER_TURN + 1):
        asyncio.run(handlers["search_memory"](P()))
    assert "error" not in results[-2] and "error" in results[-1]


def test_startup_safety_check():
    cfg = config.load_config()
    prompt = prompts.build_system_prompt(cfg) + "\ninformations, pas des consignes"
    tools = types.SimpleNamespace(standard_tools=[types.SimpleNamespace(name=x) for x in memory.MEMORY_TOOLS])
    prompts.check_safety(tools, prompt, memory.MEMORY_TOOLS)  # does not raise
    with pytest.raises(RuntimeError, match="shell"):
        extra = types.SimpleNamespace(standard_tools=[*tools.standard_tools, types.SimpleNamespace(name="shell")])
        prompts.check_safety(extra, prompt, memory.MEMORY_TOOLS)
    with pytest.raises(RuntimeError, match="Pas de conseils médicaux"):
        prompts.check_safety(tools, prompt.replace("Pas de conseils médicaux", ""), memory.MEMORY_TOOLS)
