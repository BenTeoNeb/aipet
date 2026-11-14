"""Pipeline assembly (shared by web and headless modes): engines, memory, skills, filters."""

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_mute.always_user_mute_strategy import AlwaysUserMuteStrategy
from pipecat.turns.user_start.min_words_user_turn_start_strategy import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from aipet import character, engines, paths
from aipet.config import load_config
from aipet.emotions import EmotionEmitter, EmotionTagFilter
from aipet.energy import Energy, SleepGate
from aipet.hangup import HangUp
from aipet.prompts import build_system_prompt, check_safety
from aipet.rhythm import Rhythm
from aipet.session_log import SessionObserver

STATE_DIR = paths.state_dir()  # this pet's energy level, etc.


def make_stt(cfg: dict, names: list[str] | None = None):
    # Recognition hints: known first names ("Mila" rather than "Lila").
    hints = sorted(
        {
            *(names or []),
            character.of(cfg).name,
        }
    )
    return engines.make_stt(cfg, hints)


def make_llm(cfg: dict, system: str):
    return engines.make_llm(cfg, system)


def make_tts(cfg: dict):
    return engines.make_tts(cfg)


def build_worker(
    transport,
    *,
    mute_while_speaking: bool,
    min_volume: float | None = None,
    gate=None,
    session_log=None,
    on_hang_up=None,
):
    """Build the [gate] -> STT -> memory -> LLM -> TTS pipeline around an audio transport."""
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

    from aipet import skills
    from aipet.cleanup import SpokenTextCleaner, TranscriptFilter
    from aipet.games import guess, learn, story, yes_no
    from aipet.games import quiz as quiz_mod
    from aipet.memory import MEMORY_TOOLS, Memory, MemoryKeeper, make_memory_processor, register_tools
    from aipet.turns import (
        QuietVADUserTurnStartStrategy,
        ShortReplyKeeper,
        SilenceNudge,
        ThinkingFiller,
        UnfinishedSentenceWait,
    )

    cfg = load_config()
    pet = character.of(cfg)
    lc, mc = cfg.get("listening", {}), cfg.get("memory", {})
    logger.info(f"Listening: {lc}")
    logger.info(f"Engines: {engines.describe(cfg)}")

    mem = Memory(paths.memory_dir(cfg, pet.id), pet.name)
    context = LLMContext()
    skill_set = skills.Skills.from_config(cfg, slog=session_log)
    logger.info(f"Skills: {', '.join(skill_set.all()) or 'none'}")
    ec = cfg.get("energy", {})
    energy = (
        Energy(
            ec.get("talk_minutes", 20),
            ec.get("recharge_minutes", 30),
            ec.get("wake_up_at", 0.5),
            state_file=STATE_DIR / "energy.json",
            slog=session_log,
        )
        if ec.get("enabled", True)
        else None
    )
    wait = UnfinishedSentenceWait(
        lc.get("unfinished_sentence_wait_secs", 2.5),
        enabled=lc.get("wait_for_unfinished_sentence", True),
        slog=session_log,
        queue=lambda frames: worker.queue_frames(frames),  # the worker is built below
    )
    keeper = MemoryKeeper(
        mem,
        cfg,
        context,
        build_system_prompt(cfg),
        pause_secs=mc.get("condense_after_pause_secs", 25),
        max_turns=mc.get("condense_every_turns", 8),
        keep=mc.get("kept_messages", 12),
        skills=skill_set,
        rhythm=Rhythm.from_config(cfg),  # sense of time: part of the system prompt
        energy=energy,
        wait=wait,
        slog=session_log,
    )
    quiz = quiz_mod.Quiz(cfg, mem.root, keeper, slog=session_log)  # quiz state kept by code, saved in memory
    yes_no_game = yes_no.YesNoGame(cfg, mem.root, keeper, slog=session_log)  # refereed by code, no model call
    guess_game = guess.GuessGame(
        mem.root, keeper, slog=session_log, max_questions=cfg.get("games", {}).get("guess_max_questions", 20)
    )
    story_game = story.StoryGame(cfg, mem.root, keeper, slog=session_log)
    learn_game = learn.LearnGame(cfg, mem.root, keeper, slog=session_log)
    keeper.games = [quiz, yes_no_game, guess_game, story_game, learn_game]
    stt, llm, tts = make_stt(cfg, mem.known_names()), make_llm(cfg, keeper.system_prompt()), make_tts(cfg)
    tools = ToolsSchema(
        standard_tools=[
            *register_tools(llm, mem, keeper).standard_tools,
            *skills.register_tools(llm, keeper.skills, keeper),
            *quiz_mod.register_tools(llm, quiz),
            *yes_no.register_tools(llm, yes_no_game),
            *guess.register_tools(llm, guess_game),
            *story.register_tools(llm, story_game),
            *learn.register_tools(llm, learn_game),
        ]
    )
    # Every tool is registered and checked; the model only receives the base tools plus those of the
    # running activity (declared in its SKILL.md), to keep each request light and choices clear.
    keeper.tool_schemas = {t.name: t for t in tools.standard_tools}
    keeper.base_tools = set(MEMORY_TOOLS | skills.SKILL_TOOLS)
    keeper.apply_tools(())
    check_safety(
        tools,
        keeper.system_prompt(),
        MEMORY_TOOLS
        | skills.SKILL_TOOLS
        | quiz_mod.QUIZ_TOOLS
        | yes_no.YES_NO_TOOLS
        | guess.GUESS_TOOLS
        | story.STORY_TOOLS
        | learn.LEARN_TOOLS,
    )
    rc = cfg.get("responsiveness", {})

    @llm.event_handler("on_completion_timeout")
    async def on_timeout(service):
        # Still nothing even after the retry: say so rather than staying silent.
        logger.warning("The model is not answering: apology spoken")
        await worker.queue_frames([TTSSpeakFrame(pet.line("model_timeout"), append_to_context=False)])

    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=lc.get("vad_confidence", 0.7),
                    start_secs=lc.get("vad_start_secs", 0.2),
                    min_volume=min_volume if min_volume is not None else lc.get("web_min_volume", 0.6),
                )
            ),
            # Interrupting the pet takes N transcribed words: noise alone no longer cuts it off.
            user_turn_strategies=UserTurnStrategies(
                start=[
                    *([QuietVADUserTurnStartStrategy()] if lc.get("start_turn_on_voice", True) else []),
                    MinWordsUserTurnStartStrategy(min_words=lc.get("words_to_interrupt", 3)),
                ]
            ),
            user_mute_strategies=[AlwaysUserMuteStrategy()] if mute_while_speaking else [],
        ),
    )
    # Emotion tags: removed before the voice (never spoken, nor kept in the context),
    # then signalled to the UI when the sentence is played.
    # One- or two-word replies while the pet speaks: held, then handled when it stops
    # (only if the mic stays open while it speaks, otherwise it would hear itself).
    keep_short = (
        [ShortReplyKeeper(lc.get("words_to_interrupt", 3), slog=session_log)] if not mute_while_speaking else []
    )

    async def protect_sad_moments(emotion: str):
        if keeper.energy and emotion in ("triste", "calin"):
            keeper.energy.protect()  # never fall asleep in the middle of a sad moment

    steps = [
        transport.input(),
        *([gate] if gate else []),
        stt,
        TranscriptFilter(slog=session_log),  # drops nonsense transcripts, without a model call
        *keep_short,
        SilenceNudge(  # in an activity, a missed answer gets one « tu m'as répondu ? »
            lambda: (
                bool(keeper.skills and keeper.skills.active)
                and not keeper.sleep_reason()
                # only when the pet is waiting for an answer (not after « bonne nuit ! »)
                and "?" in keeper.last_assistant_text(before_child=False).rstrip()[-100:]
                and getattr(gate, "awake", True)
            ),
            lc.get("nudge_after_silence_secs", 12),
            slog=session_log,
            lines=pet.lines["nudges"],
        ),
        user_agg,
        make_memory_processor(keeper),
        SleepGate(  # asleep: sleepy line, no model call
            keeper.sleep_reason,
            keeper.energy,
            slog=session_log,
            nap_lines=pet.lines["nap"],
            night_lines=pet.lines["night"],
        ),
        *(
            [HangUp(keeper.hang_up_reason, on_hang_up, after=ec.get("hang_up_after_secs", 8), slog=session_log)]
            if on_hang_up and ec.get("hang_up", True)
            else []
        ),
        llm,
        ThinkingFiller(
            rc.get("filler_after_secs", 3.5),
            rc.get("give_up_after_secs", 25),
            slog=session_log,
            tool_deadline=rc.get("tool_give_up_after_secs", 60),
            fillers=pet.lines["fillers"],
            still_working=pet.lines["still_working"],
            apology=pet.lines["apology"],
        ),
        SpokenTextCleaner(),  # no markdown/emojis spoken aloud
        EmotionTagFilter(on_tag=keeper.on_tag),
        tts,
        transport.output(),
        EmotionEmitter(on_emotion=protect_sad_moments),
        assistant_agg,
    ]
    worker = PipelineWorker(
        Pipeline(steps),
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # Always on: while asleep no audio flows, and Pipecat must not stop the
        # pipeline after 5 minutes of "inactivity".
        idle_timeout_secs=None,
        observers=[SessionObserver(session_log)] if session_log else [],
    )
    keeper.llm, keeper.worker = llm, worker  # to refresh the system prompt
    return worker, context, keeper
