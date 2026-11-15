"""Pipeline assembly (shared by web and headless modes): engines, memory, skills, filters.

transport in → [wake gate] → STT → transcript filters → user aggregator → conversation (labels,
memory) → sleep gate → [hang-up] → LLM → filler → text cleaning → emotion tags → TTS → transport
out → emotion emitter → assistant aggregator
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
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
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.turns.user_mute.always_user_mute_strategy import AlwaysUserMuteStrategy
from pipecat.turns.user_start.min_words_user_turn_start_strategy import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from aipet import character, engines, paths, skills
from aipet.character import Character
from aipet.cleanup import SpokenTextCleaner, TranscriptFilter
from aipet.config import load_config
from aipet.conversation import Conversation
from aipet.emotions import EmotionEmitter, EmotionTagFilter
from aipet.energy import Energy, SleepGate
from aipet.games import make_games
from aipet.hangup import HangUp
from aipet.memory import MEMORY_TOOLS, Memory, register_tools
from aipet.prompts import build_system_prompt, check_safety
from aipet.rhythm import Rhythm
from aipet.session_log import SessionObserver
from aipet.turns import (
    QuietVADUserTurnStartStrategy,
    ShortReplyKeeper,
    SilenceNudge,
    ThinkingFiller,
    UnfinishedSentenceWait,
)
from aipet.wake import WakeWordGate


def build_worker(
    transport,
    *,
    mute_while_speaking: bool,
    min_volume: float | None = None,
    gate: WakeWordGate | None = None,
    session_log=None,
    on_hang_up: Callable[[str], Awaitable[None]] | None = None,
    cfg: dict | None = None,
) -> tuple[PipelineWorker, LLMContext, Conversation]:
    """Build the pipeline around an audio transport; returns (worker, context, conversation)."""
    cfg = cfg or load_config()
    pet = character.of(cfg)
    logger.info(f"Listening: {cfg.get('listening', {})}")
    logger.info(f"Engines: {engines.describe(cfg)}")

    worker: PipelineWorker | None = None  # built last; the callbacks below only run once it exists

    async def queue(frames):
        await worker.queue_frames(frames)

    mem = Memory.shared(paths.memory_dir(cfg, pet.id), pet.name)
    conversation = _conversation(cfg, mem, session_log, queue)
    # Recognition hints: known first names (« Mila » rather than « Lila ») and the pet's.
    stt = engines.make_stt(cfg, sorted({*mem.known_names(), pet.name}))
    llm = engines.make_llm(cfg, conversation.system_prompt())
    tts = engines.make_tts(cfg)
    _register_tools(llm, mem, conversation)

    @llm.event_handler("on_completion_timeout")
    async def on_timeout(service):
        # Still nothing even after the retry: say so rather than staying silent.
        logger.warning("The model is not answering: apology spoken")
        await queue([TTSSpeakFrame(pet.line("model_timeout"), append_to_context=False)])

    steps = _processors(
        cfg, pet, transport, conversation, stt, llm, tts,
        gate=gate, mute_while_speaking=mute_while_speaking, min_volume=min_volume,
        session_log=session_log, on_hang_up=on_hang_up,
    )  # fmt: skip
    worker = PipelineWorker(
        Pipeline(steps),
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # Always on: while asleep no audio flows, and Pipecat must not stop the
        # pipeline after 5 minutes of "inactivity".
        idle_timeout_secs=None,
        observers=[SessionObserver(session_log)] if session_log else [],
    )
    conversation.bind(llm, worker)  # to refresh the system prompt
    return worker, conversation.context, conversation


def _conversation(cfg: dict, mem: Memory, session_log, queue) -> Conversation:
    lc, mc = cfg.get("listening", {}), cfg.get("memory", {})
    skill_set = skills.Skills.from_config(cfg, slog=session_log)
    logger.info(f"Skills: {', '.join(skill_set.all()) or 'none'}")
    wait = UnfinishedSentenceWait(
        lc.get("unfinished_sentence_wait_secs", 2.5),
        enabled=lc.get("wait_for_unfinished_sentence", True),
        slog=session_log,
        queue=queue,
    )
    return Conversation(
        mem,
        cfg,
        LLMContext(),
        build_system_prompt(cfg),
        skills=skill_set,
        rhythm=Rhythm.from_config(cfg),  # sense of time: part of the system prompt
        energy=Energy.from_config(cfg, slog=session_log),
        wait=wait,
        games=lambda host: make_games(cfg, mem.root, host, slog=session_log),
        slog=session_log,
        pause_secs=mc.get("condense_after_pause_secs", 25),
        max_turns=mc.get("condense_every_turns", 8),
        keep=mc.get("kept_messages", 12),
    )


def _register_tools(llm, mem: Memory, conversation: Conversation) -> None:
    """Every tool is registered; the model receives the base ones plus the running activity's
    (tool_gate.ToolGate). Checked once at startup against the safety rules."""
    base_tools = MEMORY_TOOLS | skills.SKILL_TOOLS
    tools = ToolsSchema(
        standard_tools=[
            *register_tools(
                llm, mem, conversation.tool_budget_exhausted, conversation.forget_in_context
            ).standard_tools,
            *skills.register_tools(llm, conversation),
            *(schema for game in conversation.games for schema in game.register_tools(llm)),
        ]
    )
    conversation.tools.register(tools.standard_tools, base_tools)
    check_safety(tools, conversation.system_prompt(), base_tools.union(*(game.tools for game in conversation.games)))


def _user_aggregators(cfg: dict, context: LLMContext, *, min_volume: float | None, mute_while_speaking: bool):
    lc = cfg.get("listening", {})
    return LLMContextAggregatorPair(
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


def _processors(
    cfg: dict,
    pet: Character,
    transport,
    conversation: Conversation,
    stt,
    llm,
    tts,
    *,
    gate: WakeWordGate | None,
    mute_while_speaking: bool,
    min_volume: float | None,
    session_log,
    on_hang_up,
) -> list[FrameProcessor]:
    lc, ec, rc = cfg.get("listening", {}), cfg.get("energy", {}), cfg.get("responsiveness", {})
    energy = conversation.sleep.energy
    user_agg, assistant_agg = _user_aggregators(
        cfg, conversation.context, min_volume=min_volume, mute_while_speaking=mute_while_speaking
    )

    def waiting_for_an_answer() -> bool:
        """In an activity, a missed answer gets one « tu m'as répondu ? »: only when the pet asked
        something (not after « bonne nuit ! ») and is awake."""
        return (
            bool(conversation.skills.active)
            and not conversation.sleep.reason()
            and "?" in conversation.last_assistant_text(before_child=False).rstrip()[-100:]
            and (gate is None or gate.awake)
        )

    async def protect_sad_moments(emotion: str):
        if energy and emotion in ("triste", "calin"):
            energy.protect()  # never fall asleep in the middle of a sad moment

    # One- or two-word replies while the pet speaks: held, then handled when it stops (only if the
    # mic stays open while it speaks, otherwise it would hear itself).
    keep_short = [] if mute_while_speaking else [ShortReplyKeeper(lc.get("words_to_interrupt", 3), slog=session_log)]
    hang_up = (
        [HangUp(conversation.sleep.hang_up_reason, on_hang_up, after=ec.get("hang_up_after_secs", 8), slog=session_log)]
        if on_hang_up and ec.get("hang_up", True)
        else []
    )
    return [
        transport.input(),
        *([gate] if gate else []),
        stt,
        TranscriptFilter(slog=session_log),  # drops nonsense transcripts, without a model call
        *keep_short,
        SilenceNudge(
            waiting_for_an_answer, lc.get("nudge_after_silence_secs", 12), slog=session_log, lines=pet.lines["nudges"]
        ),
        user_agg,
        conversation.processor(),  # labels each turn, condenses during pauses
        SleepGate(  # asleep: sleepy line, no model call
            conversation.sleep.reason,
            energy,
            slog=session_log,
            nap_lines=pet.lines["nap"],
            night_lines=pet.lines["night"],
        ),
        *hang_up,
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
        # Emotion tags: removed before the voice (never spoken, nor kept in the context), then
        # signalled to the UI when the sentence is played.
        EmotionTagFilter(on_tag=conversation.on_tag),
        tts,
        transport.output(),
        EmotionEmitter(on_emotion=protect_sad_moments),
        assistant_agg,
    ]
