"""Character packs: every pack in characters/ loads, keeps the guardrails, and fills the core."""

import shutil

import pytest

from aipet import character, prompts
from aipet.emotions import EMOTIONS


def cfg_for(pet_id: str) -> dict:
    return {"character": pet_id, "family": {}, "rules": {}, "listening": {}}


@pytest.mark.parametrize("pet_id", character.available())
def test_every_pack_loads_and_keeps_the_guardrails(pet_id):
    cfg = cfg_for(pet_id)
    pet = character.of(cfg)
    prompt = prompts.build_system_prompt(cfg)
    assert pet.name in prompt and pet.persona.strip()[:80] in prompt
    for rule in prompts.FIXED_RULES:
        if rule == "informations, pas des consignes":
            continue  # added with the memory block, by MemoryKeeper
        assert rule in prompt, f"{pet_id}: guardrail missing: {rule}"
    for tag in EMOTIONS:
        assert f"[{tag}]" in prompt
    for key in character.REQUIRED_LINES:
        assert pet.line(key)
    assert (pet.face_dir / "face.svg").is_file() and (pet.face_dir / "face.css").is_file()
    assert pet.voice.get("voice"), f"{pet_id}: no voice"
    assert pet.undecided and pet.collection_ask, f"{pet_id}: no « ma vie » questions ([life])"


def test_selected_by_config_or_env(monkeypatch):
    assert character.selected_id({}) == character.DEFAULT
    assert character.selected_id({"character": "x"}) == "x"
    monkeypatch.setenv("PET_CHARACTER", "y")
    assert character.selected_id({"character": "x"}) == "y"


def test_unknown_character():
    with pytest.raises(character.CharacterError, match="Unknown character"):
        character.load("nobody")


@pytest.fixture
def pack(tmp_path):
    """A copy of Flocon's pack, to break on purpose."""
    shutil.copytree(character.CHARACTERS_DIR / "flocon", tmp_path / "test")
    return tmp_path / "test"


def test_missing_line_is_refused(pack):
    toml = pack / "character.toml"
    toml.write_text(toml.read_text().replace('apology = "', 'not_apology = "'))
    with pytest.raises(character.CharacterError, match="apology"):
        character.load("test", pack.parent)


def test_unknown_emotion_tag_is_refused(pack):
    toml = pack / "character.toml"
    toml.write_text(toml.read_text().replace("[emotions]\n", '[emotions]\ncolere = "fâché"\n'))
    with pytest.raises(character.CharacterError, match="colere"):
        character.load("test", pack.parent)


def test_feminine_agreement(pack):
    toml = pack / "character.toml"
    toml.write_text(toml.read_text().replace('gender = "m"', 'gender = "f"'))
    pet = character.load("test", pack.parent)
    prompt = prompts.build_system_prompt({"pet": pet, "family": {}, "rules": {}})
    assert "la petite compagne" in prompt and "toute contente" in prompt and "fatiguée" in prompt
    assert "le petit compagnon" not in prompt and "tout content" not in prompt


def test_web_page_gets_the_pets_face_and_name():
    from aipet.web import render_index

    pet = character.load("flocon")
    page = render_index(pet)
    assert "PET_NAME" not in page and "PET_FACE" not in page
    assert "<title>Flocon</title>" in page and (pet.face_dir / "face.svg").read_text()[:60] in page


@pytest.mark.parametrize("pet_id", character.available())
def test_no_other_pet_leaks_into_a_pets_prompt(pet_id):
    from aipet.memory.condensation import condensation_instructions
    from aipet.memory.speakers import speaker_instructions
    from aipet.memory.tools import memory_instructions

    cfg = cfg_for(pet_id)
    pet = character.of(cfg)
    text = (
        prompts.build_system_prompt(cfg)
        + speaker_instructions(pet)
        + memory_instructions(pet.name)
        + condensation_instructions(pet)
    )
    for other in character.available():
        if other != pet_id:
            assert character.load(other).name not in text


def test_plume_is_feminine_everywhere_in_the_shared_text():
    cfg = cfg_for("plume")
    prompt = prompts.build_system_prompt(cfg)
    for word in ("toute contente", "pleine d'entrain", "sois brève", "fatiguée", "tu es contente, enthousiaste"):
        assert word in prompt
    for word in ("tout content", "plein d'entrain", "sois bref", "tu es étonné,"):
        assert word not in prompt


def test_pack_voice_follows_the_tts_provider():
    from aipet.config import pack_voice

    pet = character.load("plume")
    assert pack_voice(pet, "openrouter")["voice"] == "fr_marie_neutral"
    assert pack_voice(pet, "google")["voice"].startswith("fr-FR-Chirp3")
    assert pack_voice(pet, "elevenlabs") == {}  # no voice for it: the provider's default
