"""One folder per pet: a new pet never sees another pet's memory; data from before is moved once."""

from aipet import migrate, paths, prompts
from aipet.memory import Memory, store


def test_each_pet_has_its_own_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("PET_HOME", str(tmp_path))
    for name in ("MEMORY_DIR", "STATE", "LOGS"):
        monkeypatch.delenv(f"PET_{name}", raising=False)
        monkeypatch.delenv(f"FLOCON_{name}", raising=False)
    a, b = paths.memory_dir({}, "flocon"), paths.memory_dir({}, "plume")
    assert a == tmp_path / "pets" / "flocon" / "memory" and b == tmp_path / "pets" / "plume" / "memory"
    assert paths.state_dir("plume") == tmp_path / "pets" / "plume" / "state"
    assert paths.logs_dir("plume") == tmp_path / "pets" / "plume" / "logs"
    assert paths.cache_dir() == tmp_path / "cache"  # household
    assert paths.memory_dir({"memory": {"dir": str(tmp_path / "x" / "{pet}")}}, "plume") == tmp_path / "x" / "plume"


def test_a_new_pet_does_not_know_what_the_children_told_another_pet(tmp_path):
    flocon = Memory(tmp_path / "pets" / "flocon" / "memory", "Flocon")
    (flocon.root / store.DIR_FAMILY / "mila.md").write_text(
        "---\nprenom: Mila\nage: 7\n---\n# Mila\n\n## À savoir\n- confidence : a peur des orages\n"
    )
    plume = Memory(tmp_path / "pets" / "plume" / "memory", "Plume")
    assert plume.known_names() == []
    assert "Mila" not in plume.context_block()
    assert plume.search("Mila") == [] and flocon.search("Mila")
    assert plume.read("../../flocon/memory/famille/mila.md") is None  # no way out of its own folder
    assert "Ma vie de Plume" in plume.context_block() and "Flocon" not in plume.context_block()


def test_children_are_never_taken_from_the_configuration():
    cfg = {"character": "flocon", "family": {"children": [{"name": "Léa", "age": 7}]}}
    assert "Léa" not in prompts.build_system_prompt(cfg)


def test_legacy_summary_file_is_renamed(tmp_path):
    (tmp_path / "FLOCON.md").write_text("# Ce que Flocon sait\n\nMila aime le vert.\n")
    mem = Memory(tmp_path, "Flocon")
    assert not (tmp_path / "FLOCON.md").exists()
    assert "Mila aime le vert" in (tmp_path / store.SUMMARY_FILE).read_text()
    assert "Mila aime le vert" in mem.context_block()


def test_legacy_data_moves_to_flocons_folder_once(tmp_path):
    legacy, home = tmp_path / ".flocon", tmp_path / ".aipet"
    for d in ("memoire/famille", "logs/sessions", "state", "cache"):
        (legacy / d).mkdir(parents=True)
    (legacy / "memoire" / "famille" / "mila.md").write_text("Mila")
    (legacy / "gemini_key").write_text("k")
    done = migrate.migrate_legacy(legacy, home)
    assert len(done) == 4
    assert (home / "pets" / "flocon" / "memory" / "famille" / "mila.md").read_text() == "Mila"
    assert (home / "pets" / "flocon" / "logs" / "sessions").is_dir() and (home / "cache").is_dir()
    assert not (legacy / "memoire").exists()  # moved, not copied: one copy of the children's data
    assert (legacy / "gemini_key").exists() and (legacy / "MOVED_TO_AIPET.txt").exists()
    assert migrate.migrate_legacy(legacy, home) == []  # once


def test_migration_never_overwrites(tmp_path):
    legacy, home = tmp_path / ".flocon", tmp_path / ".aipet"
    (legacy / "memoire").mkdir(parents=True)
    (legacy / "memoire" / "old.md").write_text("old")
    (home / "pets" / "flocon" / "memory").mkdir(parents=True)
    assert migrate.migrate_legacy(legacy, home) == []
    assert (legacy / "memoire" / "old.md").exists()
