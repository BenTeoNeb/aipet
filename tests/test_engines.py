"""Swappable engines: configuration, offline construction, JSON call per provider."""

import asyncio
import json
import sys
import types

import pytest
from pipecat.processors.frame_processor import FrameDirection as D

from aipet import engines
from aipet.memory import SCHEMA


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("CONVERSATION_MODEL", "CONVERSATION_PROVIDER", "TTS_PROVIDER"):
        monkeypatch.delenv(f"PET_{k}", raising=False)
        monkeypatch.delenv(f"FLOCON_{k}", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "ELEVENLABS_API_KEY",
        "MISTRAL_API_KEY",
        "OPEN_ROUTER_API",
    ):
        monkeypatch.setenv(k, "test-key")


def build(f, *a):
    """As in the app: services are built inside an asyncio loop."""

    async def go():
        return f(*a)

    return asyncio.run(go())


def cfg(**roles):
    return {"engines": roles, "responsiveness": {"retry_llm_after_secs": 6}}


# --- settings ---------------------------------------------------------------------------------
def test_defaults_without_section():
    r = engines.settings({}, "conversation")
    assert r["provider"] == "google-vertex" and r["model"].startswith("gemini")


def test_switching_provider_drops_google_settings():
    r = engines.settings(cfg(tts={"provider": "openai"}), "tts")
    assert "Chirp3" not in str(r.get("voice")) and r["model"] == "gpt-4o-mini-tts" and r["speed"] == 1.0
    r = engines.settings(cfg(stt={"provider": "openai"}), "stt")
    assert "region" not in r and r["model"] == "gpt-4o-transcribe"


def test_environment_variable_override(monkeypatch):
    monkeypatch.setenv("PET_CONVERSATION_MODEL", "gemini-3.8-flash")
    assert engines.settings(cfg(conversation={"model": "other"}), "conversation")["model"] == "gemini-3.8-flash"
    monkeypatch.setenv("PET_CONVERSATION_PROVIDER", "anthropic")
    monkeypatch.delenv("PET_CONVERSATION_MODEL")
    r = engines.settings({}, "conversation")
    assert r["provider"] == "anthropic" and r["model"].startswith("claude")


def test_unknown_provider():
    with pytest.raises(engines.UnknownEngine, match="possible"):
        engines.settings(cfg(conversation={"provider": "skynet"}), "conversation")
    with pytest.raises(engines.UnknownEngine):
        engines.settings(cfg(tts={"provider": "anthropic"}), "tts")  # no TTS there


def test_missing_key_clear_message(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        engines.make_llm(cfg(conversation={"provider": "openai"}), "x")


def test_compatible_server_without_key(monkeypatch):
    """Local Ollama: a url, no key."""
    monkeypatch.delenv("OPENAI_API_KEY")
    llm = engines.make_llm(
        cfg(conversation={"provider": "openai", "model": "qwen3", "url": "http://localhost:11434/v1"}), "x"
    )
    assert "OpenAILLMService" in [c.__name__ for c in type(llm).__mro__]


def test_describe():
    d = engines.describe(
        cfg(
            conversation={
                "provider": "openai",
                "model": "mistral-small",
                "url": "https://api.mistral.ai/v1",
                "key_env": "MISTRAL_API_KEY",
            }
        )
    )
    assert d["conversation"] == "openai · mistral-small (https://api.mistral.ai/v1)"
    assert d["tts"].startswith("google") and d["project"] == "test-project"
    all_openai = {r: {"provider": "openai"} for r in ("stt", "conversation", "condensation", "tts")}
    assert "project" not in engines.describe(cfg(**all_openai))


# --- building services (no network call) ------------------------------------------------------
@pytest.mark.parametrize(
    "provider,cls",
    [
        ("google-vertex", "GoogleVertexLLMService"),
        ("google", "GoogleLLMService"),
        ("anthropic-vertex", "AnthropicLLMService"),
        ("anthropic", "AnthropicLLMService"),
        ("openai", "OpenAILLMService"),
        ("openrouter", "OpenRouterLLMService"),
    ],
)
def test_conversation_all_providers(provider, cls, monkeypatch):
    # Vertex refreshes the real Google credentials at construction: fake them.
    from google.oauth2.credentials import Credentials
    from pipecat.services.google.vertex.llm import GoogleVertexLLMService

    monkeypatch.setattr(
        GoogleVertexLLMService, "_get_credentials", staticmethod(lambda *a, **k: Credentials(token="fake-token"))
    )
    llm = build(engines.make_llm, cfg(conversation={"provider": provider}), "Tu es Flocon.")
    assert cls in [c.__name__ for c in type(llm).__mro__]  # possibly wrapped (llm_retry)
    # What the rest of the code needs, whatever the provider:
    assert type(llm).Settings(system_instruction="new instructions")  # refresh_prompt
    assert hasattr(llm, "register_function") and hasattr(llm, "event_handler")
    assert "on_completion_timeout" in llm._event_handlers


@pytest.mark.parametrize(
    "provider,cls", [("google", "GoogleSTTService"), ("openai", "OpenAISTTService"), ("openrouter", "OpenAISTTService")]
)
def test_stt_all_providers(provider, cls):
    stt = build(engines.make_stt, cfg(stt={"provider": provider}), ["Flocon", "Noam", "Mila"])
    assert type(stt).__name__ == cls


@pytest.mark.parametrize(
    "provider,extra,cls",
    [
        ("google", {}, "GoogleTTSService"),
        ("openai", {"voice": "coral"}, "OpenAITTSService"),
        ("elevenlabs", {"voice": "voice-id"}, "ElevenLabsTTSService"),
        ("openrouter", {}, "OpenRouterTTSService"),
    ],
)
def test_tts_all_providers(provider, extra, cls):
    tts = build(engines.make_tts, cfg(tts={"provider": provider, **extra}))
    assert type(tts).__name__ == cls


# --- JSON call (condensation) -----------------------------------------------------------------
ANSWER = {"memory": {"participants": [], "topics": [], "summary": ""}}


def test_strict_schema_openai():
    s = engines._strict_schema(SCHEMA)
    assert s["additionalProperties"] is False
    assert s["properties"]["people"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in SCHEMA  # the original is not modified

    def objects(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                yield n
            for v in n.values():
                yield from objects(v)
        elif isinstance(n, list):
            for v in n:
                yield from objects(v)

    for o in objects(s):  # strict mode: every field required
        assert set(o["required"]) == set(o["properties"])


def test_json_openai_compatible(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            seen["client"] = kw
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen["req"] = kw
            msg = types.SimpleNamespace(content=json.dumps(ANSWER))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    c = cfg(
        condensation={
            "provider": "openai",
            "model": "mistral-medium-latest",
            "url": "https://api.mistral.ai/v1",
            "key_env": "MISTRAL_API_KEY",
        }
    )
    assert engines.complete_json(c, "instructions", "text", SCHEMA) == ANSWER
    assert seen["client"]["base_url"] == "https://api.mistral.ai/v1" and seen["client"]["api_key"] == "test-key"
    assert seen["req"]["model"] == "mistral-medium-latest"
    assert seen["req"]["messages"][0] == {"role": "system", "content": "instructions"}
    assert seen["req"]["response_format"]["json_schema"]["strict"] is True
    assert seen["req"]["extra_body"] is None


def test_json_claude_through_openrouter_uses_plain_json_mode(monkeypatch):
    """Claude refuses a big schema as a grammar (« compiled grammar is too large »): plain JSON mode,
    the schema in the instructions, and the object found even inside fences or a sentence."""
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen["req"] = kw
            msg = types.SimpleNamespace(content="```json\n" + json.dumps(ANSWER) + "\n```\nVoilà !")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPEN_ROUTER_API", "k")
    c = cfg(condensation={"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"})
    assert engines.complete_json(c, "instructions", "text", SCHEMA) == ANSWER
    assert seen["req"]["response_format"] == {"type": "json_object"}
    assert "suit ce schéma" in seen["req"]["messages"][0]["content"]


def test_openrouter_routing_and_reasoning(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            seen["client"] = kw
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen["req"] = kw
            msg = types.SimpleNamespace(content="```json\n" + json.dumps(ANSWER) + "\n```")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    c = cfg(conversation={"provider": "openrouter"}, condensation={"provider": "openrouter"})
    assert engines.complete_json(c, "i", "t", SCHEMA, role="conversation") == ANSWER  # fenced JSON accepted
    assert seen["client"]["base_url"] == engines.OPENROUTER_URL and seen["req"]["model"] == "google/gemini-3.6-flash"
    assert seen["req"]["extra_body"] == {
        "provider": {"data_collection": "deny"},
        "reasoning": {"effort": "minimal"},
        "safety_settings": engines.SAFETY_SETTINGS,  # Gemini through OpenRouter: same filters as direct
    }
    engines.complete_json(c, "i", "t", SCHEMA)  # condensation: the model's own reasoning
    assert seen["req"]["model"] == "google/gemini-3.8-flash" and "reasoning" not in seen["req"]["extra_body"]
    llm = build(engines.make_llm, c, "Tu es Flocon.")
    assert llm._settings.extra == {
        "extra_body": {"reasoning": {"effort": "minimal"}, "safety_settings": engines.SAFETY_SETTINGS}
    }
    assert llm._settings.provider.data_collection == "deny"


def test_json_anthropic(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw
            self.messages = types.SimpleNamespace(create=self.create)

        def create(self, **kw):
            seen["req"] = kw
            return types.SimpleNamespace(
                stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(ANSWER))]
            )

    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeClient, AnthropicVertex=FakeClient)
    )
    assert engines.complete_json(cfg(condensation={"provider": "anthropic-vertex"}), "s", "p", SCHEMA) == ANSWER
    assert seen["client"]["project_id"] == "test-project"
    assert seen["req"]["output_config"]["format"]["schema"] is SCHEMA
    assert seen["req"]["model"].startswith("claude")


def test_json_google(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, **kw):
            seen["req"] = kw
            return types.SimpleNamespace(text=json.dumps(ANSWER))

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw
            self.models = FakeModels()

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    assert (
        engines.complete_json(cfg(condensation={"provider": "google", "model": "gemini-x"}), "s", "p", SCHEMA) == ANSWER
    )
    assert seen["client"]["api_key"] == "test-key" and seen["req"]["model"] == "gemini-x"
    engines.complete_json({}, "s", "p", SCHEMA)  # default: Vertex
    assert seen["client"]["vertexai"] is True and seen["client"]["project"] == "test-project"


def test_openrouter_speech_defaults_and_voice(monkeypatch):
    c = cfg(stt={"provider": "openrouter"}, tts={"provider": "openrouter"})
    stt = build(engines.make_stt, c, ["Mila"])
    assert stt._settings.model == "openai/gpt-transcribe" and str(stt._client.base_url).startswith(
        engines.OPENROUTER_URL
    )
    tts = build(engines.make_tts, c)
    assert tts._settings.model == "mistralai/voxtral-mini-tts-3-0" and tts._settings.voice == "fr_marie_neutral"
    sent = {}

    class Resp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def iter_bytes(self, n):
            yield b"\x00\x01" * 10

    def create(**kw):
        sent.update(kw)
        return Resp()

    tts._client = types.SimpleNamespace(
        audio=types.SimpleNamespace(
            speech=types.SimpleNamespace(with_streaming_response=types.SimpleNamespace(create=create))
        )
    )

    async def go():
        tts._sample_rate = 24000

        async def noop(*a, **k):
            return None

        tts.start_tts_usage_metrics = tts.stop_ttfb_metrics = noop
        return [f async for f in tts.run_tts("Miaou", "ctx")]

    frames = asyncio.run(go())
    assert sent == {"input": "Miaou", "model": "mistralai/voxtral-mini-tts-3-0", "voice": "fr_marie_neutral",
                    "response_format": "pcm"}  # fmt: skip
    assert type(frames[0]).__name__ == "TTSAudioRawFrame"


def test_preparation_defaults_to_the_conversation_model_with_more_reasoning():
    c = cfg(conversation={"provider": "openrouter", "model": "anthropic/claude-haiku-5.5", "reasoning": "none"})
    r = engines.settings(c, "preparation")
    assert r["model"] == "anthropic/claude-haiku-5.5" and r["reasoning"] == "medium"
    c["engines"]["preparation"] = {"reasoning": "high"}  # same provider: only the effort changes
    assert engines.settings(c, "preparation")["model"] == "anthropic/claude-haiku-5.5"
    assert engines.settings(c, "preparation")["reasoning"] == "high"
    c["engines"]["preparation"] = {"provider": "google-vertex"}
    r = engines.settings(c, "preparation")
    assert r["provider"] == "google-vertex" and r["model"] == "gemini-3.6-flash"
    assert "preparation" in engines.describe(c)


def test_openrouter_voice_follows_emotions():
    from pipecat.frames.frames import LLMFullResponseStartFrame, TTSSpeakFrame

    from aipet.emotions import RESP_KEY, EmotionFrame

    voices = {"joie": "fr_marie_happy", "triste": "fr_marie_sad"}
    tts = build(engines.make_tts, cfg(tts={"provider": "openrouter", "emotion_voices": voices}))

    async def go():
        async def nothing(*a, **k):
            return None

        tts.push_frame = nothing
        start = LLMFullResponseStartFrame()
        start.metadata[RESP_KEY] = 1
        await tts.process_frame(start, D.DOWNSTREAM)
        # « [joie] Bravo Mila ! [triste] Oh, ta dent… » : offsets in letters before each tag
        await tts.process_frame(EmotionFrame(emotion="joie", response=1, offset=0), D.DOWNSTREAM)
        await tts.process_frame(EmotionFrame(emotion="triste", response=1, offset=len("BravoMila!")), D.DOWNSTREAM)
        await tts.process_frame(EmotionFrame(emotion="joie", response=2, offset=0), D.DOWNSTREAM)  # next reply, early
        got = [tts.voice_for("Bravo Mila !"), tts.voice_for("Oh, ta dent…"), tts.voice_for("Tu as mal ?")]
        tts._speaking_line = True  # a filler (TTSSpeakFrame) keeps the base voice
        filler = tts.voice_for("Hmm, voyons…")
        tts._speaking_line = False
        start2 = LLMFullResponseStartFrame()
        start2.metadata[RESP_KEY] = 2
        await tts.process_frame(start2, D.DOWNSTREAM)
        return got, filler, tts.voice_for("Coucou !")

    got, filler, next_reply = asyncio.run(go())
    assert got == ["fr_marie_happy", "fr_marie_sad", "fr_marie_sad"]  # an emotion lasts until the next tag
    assert filler == "fr_marie_neutral" and next_reply == "fr_marie_happy"
    assert TTSSpeakFrame  # imported for the filler case


def test_openrouter_voice_without_emotion_table_stays_the_same():
    tts = build(engines.make_tts, cfg(tts={"provider": "openrouter", "voice": "fr_marie_curious"}))
    assert tts.voice_for("Bonjour !") == "fr_marie_curious"


def test_preparation_per_activity_override():
    c = cfg(
        conversation={"provider": "openrouter", "model": "google/gemini-3.6-flash", "reasoning": "minimal"},
        preparation={"provider": "openrouter", "model": "google/gemini-3.6-flash", "reasoning": "low",
                     "activities": {"learn": {"model": "google/gemini-3.8-flash"}, "quiz": {"reasoning": "medium"}}},
    )  # fmt: skip
    assert engines.settings(c, "preparation")["model"] == "google/gemini-3.6-flash"
    assert "activities" not in engines.settings(c, "preparation")
    learn = engines.settings(c, "preparation", "learn")
    assert learn["model"] == "google/gemini-3.8-flash" and learn["reasoning"] == "low"
    quiz = engines.settings(c, "preparation", "quiz")
    assert quiz["model"] == "google/gemini-3.6-flash" and quiz["reasoning"] == "medium"
    assert engines.settings(c, "preparation", "story")["model"] == "google/gemini-3.6-flash"
    assert "learn: google/gemini-3.8-flash" in engines.describe(c)["preparation"]


def test_safety_settings_only_for_gemini_through_openrouter():
    assert "safety_settings" in engines._openrouter_body({"model": "google/gemini-3.6-flash"}, "conversation")
    assert "safety_settings" not in engines._openrouter_body({"model": "openai/gpt-6-luna"}, "conversation")
