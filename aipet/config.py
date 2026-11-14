"""Configuration: paths, environment (.env) and config.toml."""

import tomllib
from pathlib import Path

from loguru import logger

from aipet import character, engines
from aipet.paths import CONFIG_PATH, ROOT  # noqa: F401  (ROOT: re-exported)

PACKAGE_DIR = Path(__file__).parent


def load_config() -> dict:
    """config.toml (the household: engines, rhythm, family, rules…) plus the selected pet.

    The pet's character pack (`character = "flocon"`, or PET_CHARACTER) is loaded into
    cfg["pet"]; its voice and wake word fill [engines.tts] and [wake] where config.toml is silent.
    """
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return with_pet(cfg)


def with_pet(cfg: dict) -> dict:
    pet = character.of(cfg)
    engines_cfg = cfg.setdefault("engines", {})
    tts = engines_cfg.get("tts", {})
    engines_cfg["tts"] = {**pack_voice(pet, tts.get("provider")), **tts}
    cfg["wake"] = {**pet.wake, **cfg.get("wake", {})}
    for key in ("model",):  # a pack's .onnx wake model lives in the pack folder
        v = cfg["wake"].get(key, "")
        if str(v).endswith(".onnx") and not Path(v).is_absolute() and (pet.root / v).exists():
            cfg["wake"][key] = str(pet.root / v)
    return cfg


def config_snapshot(cfg: dict) -> dict:
    """What helps understand a session afterwards (without the full system prompt)."""
    return {
        "pet": character.of(cfg).id,
        "engines": engines.describe(cfg),
        "listening": cfg.get("listening", {}),
        "memory": cfg.get("memory", {}),
        "wake": cfg.get("wake", {}),
    }


def pack_voice(pet, provider: str | None) -> dict:
    """The pack's voice settings for this TTS provider.

    [voice] is written for one provider ([voice].provider, default the household's); a pack can add
    [voice.providers.<name>] for others. Without one, the provider's default voice is used.
    """
    base = {k: v for k, v in pet.voice.items() if k not in ("provider", "providers")}
    written_for = pet.voice.get("provider")
    if not written_for or written_for == provider:
        return base
    other = pet.voice.get("providers", {}).get(provider)
    if other is None:
        logger.warning(f"{pet.name}'s voice is for {written_for}, not {provider}: default voice used")
        return {}
    return dict(other)
