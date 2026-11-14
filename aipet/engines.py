"""Model providers (speech-to-text, conversation, preparation, condensation, text-to-speech).

Roles: `conversation` answers the child (fast, little or no reasoning); `preparation` writes what
activities need beforehand (story plans, lessons, quiz questions: more reasoning, a few seconds is
fine); `condensation` keeps the memory.

This is the ONLY file that knows about providers: the rest of the code (pipeline, memory, tools,
emotions, logs) only handles generic Pipecat services and `complete_json()`.
Switching model or provider = editing `[engines.*]` in config.toml, nothing else.

Each role has its own section, with the same keys:
    provider   see PROVIDERS below
    model      model name at that provider
    region     (optional) service region / location
    url        (optional) address of an OpenAI-compatible API (Mistral, Ollama, OpenRouter…)
    key_env    (optional) NAME of the environment variable holding the API key
               (never the key itself in config.toml)
    reasoning  (optional, openrouter) reasoning effort: none | minimal | low | medium | high
No [engines.preparation] section: preparation uses the conversation's model, with medium reasoning.
Override without editing the file: PET_<ROLE>_PROVIDER / PET_<ROLE>_MODEL
(e.g. PET_CONVERSATION_MODEL=gemini-3.8-flash).

Provider-specific settings (Gemini content filters, Claude effort…) stay here, in each adapter.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime

from loguru import logger
from pipecat.transcriptions.language import Language

from aipet import env

ROLES = ("stt", "conversation", "preparation", "condensation", "tts")

PROVIDERS = {
    "stt": ("google", "openai", "openrouter"),
    "conversation": ("google-vertex", "google", "anthropic-vertex", "anthropic", "openai", "openrouter"),
    "condensation": ("google-vertex", "google", "anthropic-vertex", "anthropic", "openai", "openrouter"),
    "preparation": ("google-vertex", "google", "anthropic-vertex", "anthropic", "openai", "openrouter"),
    "tts": ("google", "openai", "elevenlabs", "openrouter"),
}

DEFAULTS = {
    # Chirp 3: much better than latest_long on short sentences; available in "eu"/"us".
    "stt": {"provider": "google", "model": "chirp_3", "region": "eu"},
    "conversation": {"provider": "google-vertex", "model": "gemini-3.6-flash"},
    "condensation": {"provider": "google-vertex", "model": "gemini-3.8-flash"},
    "tts": {"provider": "google", "voice": "fr-FR-Chirp3-HD-Leda", "speed": 1.0},
}

# Default models when switching provider without naming a model.
DEFAULT_MODELS = {
    ("stt", "google"): "chirp_3",
    ("stt", "openai"): "gpt-4o-transcribe",
    ("stt", "openrouter"): "openai/gpt-transcribe",
    ("conversation", "google-vertex"): "gemini-3.6-flash",
    ("conversation", "google"): "gemini-3.6-flash",
    ("conversation", "anthropic-vertex"): "claude-haiku-5-5",
    ("conversation", "anthropic"): "claude-haiku-5-5",
    ("conversation", "openai"): "gpt-5-mini",
    ("conversation", "openrouter"): "google/gemini-3.6-flash",
    ("condensation", "google-vertex"): "gemini-3.8-flash",
    ("condensation", "google"): "gemini-3.8-flash",
    ("condensation", "anthropic-vertex"): "claude-sonnet-5-5",
    ("condensation", "anthropic"): "claude-sonnet-5-5",
    ("condensation", "openai"): "gpt-5-mini",
    ("condensation", "openrouter"): "google/gemini-3.8-flash",
    ("tts", "openai"): "gpt-4o-mini-tts",
    ("tts", "elevenlabs"): "eleven_flash_v2_5",
    ("tts", "openrouter"): "mistralai/voxtral-mini-tts-3-0",
}

DEFAULT_KEY_ENVS = {
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPEN_ROUTER_API",
    "elevenlabs": "ELEVENLABS_API_KEY",
}

MAX_TOKENS = 2048  # conversation: includes any reasoning tokens

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Children's speech: only route to upstream providers that don't keep or train on the data.
OPENROUTER_ROUTING = {"data_collection": "deny"}
# Gemini's content filters, as when calling Google directly. MEDIUM, not LOW: tested, LOW blocks
# Little Red Riding Hood (the wolf eating the grandmother).
SAFETY_SETTINGS = [
    {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
    for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


def _openrouter_body(r: dict, role: str) -> dict:
    """OpenRouter-only request fields: routing preferences and reasoning effort. Conversation
    defaults to minimal reasoning (voice: the first word must come fast)."""
    body = {"provider": {**OPENROUTER_ROUTING, **(r.get("routing") or {})}}
    effort = r.get("reasoning", "minimal" if role == "conversation" else None)
    if effort:
        body["reasoning"] = {"effort": effort}
    if str(r.get("model", "")).startswith("google/"):
        # Forwarded to Google according to third-party docs; accepted, but not proven to be applied
        # (Gemini's own refusals look the same). The guardrails in the prompt remain the main defence.
        body["safety_settings"] = SAFETY_SETTINGS
    return body


class UnknownEngine(ValueError):
    pass


def settings(cfg: dict, role: str, activity: str | None = None) -> dict:
    """Settings of a role: defaults < config.toml < environment variables.

    `activity` (preparation only): `[engines.preparation.activities.<activity>]` overrides the
    model or reasoning for that activity (e.g. a better model for lessons)."""
    if role not in ROLES:
        raise UnknownEngine(f"unknown role: {role}")
    section = dict(cfg.get("engines", {}).get(role) or {})
    activities = section.pop("activities", None) or {}
    if role == "preparation" and activity and activities.get(activity):
        section = {**section, **activities[activity]}
        if "provider" in activities[activity] and "model" not in activities[activity]:
            section.pop("model", None)  # another provider: its own default model
    overrides = {k: env.get(f"{role.upper()}_{k.upper()}") for k in ("provider", "model")}
    explicit = {**section, **{k: v for k, v in overrides.items() if v}}
    if role == "preparation":
        # Same model as the conversation unless configured, but its own reasoning effort.
        conv = settings(cfg, "conversation")
        if not explicit:
            return {**{k: v for k, v in conv.items() if k != "reasoning"}, "reasoning": "medium"}
        if explicit.get("provider", conv["provider"]) == conv["provider"]:
            r = {**{k: v for k, v in conv.items() if k != "reasoning"}, **explicit}
        else:
            r = {"model": DEFAULT_MODELS.get(("conversation", explicit["provider"])), **explicit}
        r.setdefault("reasoning", "medium")
        if r["provider"] not in PROVIDERS[role]:
            raise UnknownEngine(f"[engines.{role}] unknown provider '{r['provider']}'")
        return r
    d = DEFAULTS[role]
    if explicit.get("provider", d["provider"]) == d["provider"]:
        r = {**d, **explicit}
    else:
        # Another provider: keep none of the Google-specific settings (model, region, voice…).
        r = {"model": DEFAULT_MODELS.get((role, explicit["provider"])), **explicit}
        if role == "tts":
            r.setdefault("speed", d["speed"])
    if r["provider"] not in PROVIDERS[role]:
        raise UnknownEngine(
            f"[engines.{role}] unknown provider '{r['provider']}'; possible: {', '.join(PROVIDERS[role])}"
        )
    return r


def _api_key(r: dict, family: str) -> str:
    name = r.get("key_env") or DEFAULT_KEY_ENVS[family]
    val = os.getenv(name)
    if not val and not r.get("url"):  # a local server (Ollama…) can do without a key
        raise RuntimeError(f"Missing API key: set the environment variable {name} (.env file)")
    return val or "no-key"


def _project() -> str:
    try:
        return os.environ["GOOGLE_CLOUD_PROJECT"]
    except KeyError:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT missing (.env file) for a Vertex/Google Cloud service") from None


def describe(cfg: dict) -> dict:
    """Human-readable summary (session log, debug panel, startup log)."""
    out = {}
    for role in ROLES:
        r = settings(cfg, role)
        name = r.get("voice") if role == "tts" else r.get("model")
        if role == "tts" and r.get("model") and r["provider"] != "google":
            name = f"{r['model']} / {r.get('voice')}"
        extra = r.get("region") or r.get("url")
        out[role] = f"{r['provider']} · {name}" + (f" ({extra})" if extra else "")
        if r.get("reasoning") and r["provider"] == "openrouter":
            out[role] += f" · reasoning {r['reasoning']}"
        if role == "preparation":
            for act in (cfg.get("engines", {}).get("preparation") or {}).get("activities") or {}:
                a = settings(cfg, role, act)
                out[role] += f" | {act}: {a['model']}" + (
                    f" · reasoning {a['reasoning']}" if a.get("reasoning") else ""
                )
    if any(
        settings(cfg, x)["provider"].startswith("google") or settings(cfg, x)["provider"].endswith("-vertex")
        for x in ROLES
    ):
        out["project"] = os.getenv("GOOGLE_CLOUD_PROJECT")
    return out


# ---------------------------------------------------------------------------
# Speech-to-text (STT)
# ---------------------------------------------------------------------------
def make_stt(cfg: dict, hints: list[str]):
    """`hints`: words to recognize well (family first names, assistant name)."""
    r = settings(cfg, "stt")
    p = r["provider"]
    if p == "google":
        from pipecat.services.google.stt import GoogleSTTService

        adaptation = {"phrase_sets": [{"phrases": [{"value": n, "boost": 15} for n in hints]}]} if hints else None
        return GoogleSTTService(
            location=r.get("region") or "eu",
            settings=GoogleSTTService.Settings(languages=[Language.FR_FR], model=r["model"], adaptation=adaptation),
        )
    if p in ("openai", "openrouter"):
        from pipecat.services.openai.stt import OpenAISTTService

        return OpenAISTTService(
            api_key=_api_key(r, p),
            base_url=r.get("url") or (OPENROUTER_URL if p == "openrouter" else None),
            settings=OpenAISTTService.Settings(
                model=r["model"],
                language=Language.FR,
                # Model-facing hint, in French like the speech it transcribes.
                prompt=("Prénoms possibles : " + ", ".join(hints) + ".") if hints else None,
            ),
        )
    raise UnknownEngine(p)


# ---------------------------------------------------------------------------
# Conversation (streaming LLM, with tools)
# ---------------------------------------------------------------------------
def make_llm(cfg: dict, system: str):
    r = settings(cfg, "conversation")
    p = r["provider"]
    resp = cfg.get("responsiveness", {})
    # The model sometimes takes 8 to 17 s before its first word: retry the request once if
    # nothing arrived after N seconds (often faster than waiting).
    retry = {"retry_on_timeout": True, "retry_timeout_secs": resp.get("retry_llm_after_secs", 6)}

    if p in ("google-vertex", "google"):
        from pipecat.services.google.llm import GoogleLLMService

        kwargs = dict(
            model=r["model"],
            system_instruction=system,
            max_tokens=MAX_TOKENS,
            safety_settings=SAFETY_SETTINGS,  # explicit content filters (don't depend on defaults)
        )
        if p == "google":
            return GoogleLLMService(
                api_key=_api_key(r, "google"),
                settings=GoogleLLMService.Settings(**kwargs),
                stream_idle_timeout_secs=15,
                **retry,
            )
        from pipecat.services.google.vertex.llm import GoogleVertexLLMService

        # Gemini 3 is only served from "global".
        return GoogleVertexLLMService(
            project_id=_project(),
            location=r.get("region") or "global",
            settings=GoogleVertexLLMService.Settings(**kwargs),
            stream_idle_timeout_secs=15,
            **retry,
        )

    if p in ("anthropic-vertex", "anthropic"):
        from pipecat.services.anthropic.llm import AnthropicLLMService

        llm_settings = AnthropicLLMService.Settings(
            model=r["model"], system_instruction=system, max_tokens=MAX_TOKENS, effort="low"
        )  # voice: fast
        if p == "anthropic":
            return AnthropicLLMService(api_key=_api_key(r, "anthropic"), settings=llm_settings, **retry)
        from anthropic import AsyncAnthropicVertex

        client = AsyncAnthropicVertex(project_id=_project(), region=r.get("region") or "global")
        # api_key ignored: authentication goes through the Vertex client (ADC).
        return AnthropicLLMService(api_key="vertex", client=client, settings=llm_settings, **retry)

    if p == "openrouter":
        from pipecat.services.openrouter.llm import OpenRouterLLMService

        body = _openrouter_body(r, "conversation")
        return OpenRouterLLMService(
            api_key=_api_key(r, "openrouter"),
            base_url=r.get("url") or OPENROUTER_URL,
            settings=OpenRouterLLMService.Settings(
                model=r["model"],
                system_instruction=system,
                max_tokens=MAX_TOKENS,
                provider=body.pop("provider"),
                extra={"extra_body": body},
            ),
            **retry,
        )

    if p == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        return OpenAILLMService(
            api_key=_api_key(r, "openai"),
            base_url=r.get("url"),
            settings=OpenAILLMService.Settings(model=r["model"], system_instruction=system),
            **retry,
        )
    raise UnknownEngine(p)


# ---------------------------------------------------------------------------
# One-off call with a JSON answer (memory condensation). Synchronous: called in a thread.
# ---------------------------------------------------------------------------
def _strict_schema(schema: dict) -> dict:
    """OpenAI (strict mode) requires additionalProperties=false on every object."""
    s = copy.deepcopy(schema)

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                n.setdefault("additionalProperties", False)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(s)
    return s


def _log_usage(r: dict, role: str, activity: str | None, tokens_in, tokens_out, cached=0):
    """Background calls (plans, quizzes, condensation) are not in the session metrics: their usage goes
    to <pet>/logs/usage.jsonl, for the cost view."""
    try:
        from aipet import paths

        p = paths.logs_dir() / "usage.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"t": datetime.now().astimezone().isoformat(timespec="seconds"), "role": role, "activity": activity,
               "provider": r.get("provider"), "model": r.get("model"), "input": int(tokens_in or 0),
               "output": int(tokens_out or 0), "cached": int(cached or 0)}  # fmt: skip
        with open(p, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # never break a model call for a log line
        logger.debug(f"usage not logged: {e}")


def complete_json(
    cfg: dict,
    system: str,
    prompt: str,
    schema: dict,
    role: str = "condensation",
    timeout: float = 60,
    activity: str | None = None,
) -> dict:
    r = settings(cfg, role, activity)
    p = r["provider"]

    if p in ("google-vertex", "google"):
        from google import genai
        from google.genai import types

        http = types.HttpOptions(timeout=int(timeout * 1000))
        client = (
            genai.Client(api_key=_api_key(r, "google"), http_options=http)
            if p == "google"
            else genai.Client(
                vertexai=True, project=_project(), location=r.get("region") or "global", http_options=http
            )
        )
        resp = client.models.generate_content(
            model=r["model"],
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json", response_json_schema=schema
            ),
        )
        u = getattr(resp, "usage_metadata", None)
        if u:
            _log_usage(r, role, activity, u.prompt_token_count, (u.candidates_token_count or 0)
                       + (u.thoughts_token_count or 0), u.cached_content_token_count)  # fmt: skip
        return json.loads(resp.text)

    if p in ("anthropic-vertex", "anthropic"):
        import anthropic

        client = (
            anthropic.Anthropic(api_key=_api_key(r, "anthropic"), timeout=timeout, max_retries=1)
            if p == "anthropic"
            else anthropic.AnthropicVertex(
                project_id=_project(), region=r.get("region") or "global", timeout=timeout, max_retries=1
            )
        )
        resp = client.messages.create(
            model=r["model"],
            max_tokens=16000,
            system=system,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if u := getattr(resp, "usage", None):
            _log_usage(r, role, activity, u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", 0))
        if resp.stop_reason == "refusal":
            raise RuntimeError("answer refused by the model")
        return json.loads(next(b.text for b in resp.content if b.type == "text"))

    if p in ("openai", "openrouter"):
        from openai import OpenAI

        url = r.get("url") or (OPENROUTER_URL if p == "openrouter" else None)
        client = OpenAI(api_key=_api_key(r, p), base_url=url, timeout=timeout, max_retries=1)
        resp = client.chat.completions.create(
            model=r["model"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": _strict_schema(schema)},
            },
            extra_body=_openrouter_body(r, role) if p == "openrouter" else None,
        )
        if getattr(resp, "usage", None):
            details = getattr(resp.usage, "prompt_tokens_details", None)
            _log_usage(r, role, activity, resp.usage.prompt_tokens, resp.usage.completion_tokens,
                       getattr(details, "cached_tokens", 0) if details else 0)  # fmt: skip
        text = resp.choices[0].message.content or ""
        return json.loads(text.strip().removeprefix("```json").removesuffix("```"))
    raise UnknownEngine(p)


# ---------------------------------------------------------------------------
# Text-to-speech (TTS)
# ---------------------------------------------------------------------------
def make_tts(cfg: dict):
    r = settings(cfg, "tts")
    p = r["provider"]
    speed = r.get("speed", 1.0)
    if p == "google":
        from pipecat.services.google.tts import GoogleTTSService

        # Chirp 3 HD voices: fr-FR-Chirp3-HD-Leda, -Aoede, -Puck, -Charon…
        return GoogleTTSService(
            settings=GoogleTTSService.Settings(voice=r["voice"], language=Language.FR_FR, speaking_rate=speed)
        )
    if p == "openai":
        from pipecat.services.openai.tts import OpenAITTSService

        return OpenAITTSService(
            api_key=_api_key(r, "openai"),
            base_url=r.get("url"),
            settings=OpenAITTSService.Settings(
                model=r["model"], voice=r.get("voice") or "coral", speed=speed, instructions=r.get("instructions")
            ),
        )
    if p == "openrouter":
        return _openrouter_tts(r, speed)
    if p == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        return ElevenLabsTTSService(
            api_key=_api_key(r, "elevenlabs"),
            settings=ElevenLabsTTSService.Settings(
                model=r["model"], voice=r["voice"], language=Language.FR, speed=speed
            ),
        )
    raise UnknownEngine(p)


def _openrouter_tts(r: dict, speed: float):
    """OpenAI-compatible speech endpoint of OpenRouter (e.g. Voxtral). Pipecat's OpenAI TTS only
    accepts OpenAI voice names, so the request is rebuilt here with the provider's own voice.

    `emotion_voices` (optional, in [engines.tts]): the pet's emotion → voice, e.g. joie →
    fr_marie_happy. Each sentence is spoken with the voice of the emotion in force where it
    starts (same character offsets as the face animation); a new reply starts with `voice`."""
    from openai import BadRequestError
    from pipecat.frames.frames import ErrorFrame, LLMFullResponseStartFrame, TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.services.openai.tts import OpenAITTSService

    from aipet.emotions import RESP_KEY, EmotionFrame, _letters

    base_voice = r.get("voice") or "fr_marie_neutral"
    emotion_voices = dict(r.get("emotion_voices") or {})

    class OpenRouterTTSService(OpenAITTSService):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._emotions: dict[int, list[tuple[int, str]]] = {}  # reply → [(offset, emotion)]
            self._reply, self._spoken, self._speaking_line = 0, 0, False

        def voice_for(self, text: str) -> str:
            """Voice for the next sentence of the current reply (and count its characters)."""
            if self._speaking_line or not emotion_voices:  # filler / canned line: base voice
                return base_voice
            start, end = self._spoken, self._spoken + _letters(text)
            self._spoken = end
            current = [e for off, e in self._emotions.get(self._reply, []) if off < end or off == start]
            return emotion_voices.get(current[-1], base_voice) if current else base_voice

        async def process_frame(self, frame, direction):
            if isinstance(frame, EmotionFrame):
                self._emotions.setdefault(frame.response, []).append((frame.offset, frame.emotion))
                for old in [k for k in self._emotions if k < frame.response - 2]:
                    del self._emotions[old]
            elif isinstance(frame, LLMFullResponseStartFrame) and RESP_KEY in frame.metadata:
                self._reply, self._spoken = frame.metadata[RESP_KEY], 0
            if isinstance(frame, TTSSpeakFrame):
                self._speaking_line = True
                try:
                    await super().process_frame(frame, direction)
                finally:
                    self._speaking_line = False
                return
            await super().process_frame(frame, direction)

        async def run_tts(self, text: str, context_id: str):
            params = {"input": text, "model": self._settings.model, "voice": self.voice_for(text)}
            params["response_format"] = "pcm"  # raw 16-bit mono at 24 kHz
            if self._settings.speed and self._settings.speed != 1.0:
                params["speed"] = self._settings.speed
            try:
                async with self._client.audio.speech.with_streaming_response.create(**params) as resp:
                    if resp.status_code != 200:
                        yield ErrorFrame(error=f"TTS error {resp.status_code}: {await resp.text()}")
                        return
                    await self.start_tts_usage_metrics(text)
                    async for chunk in resp.iter_bytes(self.chunk_size):
                        if chunk:
                            await self.stop_ttfb_metrics()
                            yield TTSAudioRawFrame(chunk, self.sample_rate, 1, context_id=context_id)
            except BadRequestError as e:
                yield ErrorFrame(error=f"TTS error: {e}")

    return OpenRouterTTSService(
        api_key=_api_key(r, "openrouter"),
        base_url=r.get("url") or OPENROUTER_URL,
        settings=OpenRouterTTSService.Settings(model=r["model"], voice=base_voice, speed=speed),
    )
