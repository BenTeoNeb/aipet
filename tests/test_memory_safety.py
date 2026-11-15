"""What the memory must never do: bring back something a child asked to forget, let two writers
interleave, show the model files it should not read, or erase a corrupt file's content."""

import asyncio
import json
import subprocess

from aipet import parents
from aipet.fileio import load_json, lock_for
from aipet.games.common import JsonStore
from aipet.memory import Memory, condensation, store
from aipet.memory import now as memory_now


def update(**kw):
    base = {"memory": {"participants": [], "topics": [], "summary": ""}, "people": [], "stories": [], "pet_summary": ""}
    base.update(kw)
    return base


def git_log(mem):
    return subprocess.run(["git", "log", "-p", "--all"], cwd=mem.root, capture_output=True, text=True).stdout


def test_forget_during_a_condensation_wins(tmp_path, monkeypatch):
    mem = Memory(tmp_path, "Flocon")
    turns = [
        {"role": "user", "text": "Le cadeau de maman est un vélo rouge."},
        {"role": "user", "text": "J'aime les crêpes."},
    ]

    def model_written_before_the_forget(cfg, prompt):
        mem.forget("le cadeau de maman")  # said while the model was condensing
        return update(
            memory={"participants": [], "topics": ["cadeau"], "summary": "Le cadeau de maman est un vélo. Crêpes."},
            pet_summary="Mila aime les crêpes. Le cadeau de maman est un vélo.",
        )

    monkeypatch.setattr(condensation, "_call_llm", model_written_before_the_forget)
    asyncio.run(condensation.condense(mem, {"memory": {}}, turns, memory_now()))
    on_disk = "".join(p.read_text() for p in mem.files() if not p.name.startswith("a_trier"))
    assert "cadeau de maman" not in on_disk and "vélo" not in on_disk
    assert "crêpes" in on_disk  # the rest is kept
    assert "vélo" not in git_log(mem)


def test_forget_reaches_the_pets_life_stories_and_card_history(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    (mem.root / store.LIFE_FILE).write_text(
        store.render_life({store.LIFE_JOKES: ["avec Mila : le divorce des parents", "avec Noam : les chaussettes"]})
    )
    (mem.root / "histoires" / "foret.md").write_text("# La forêt\n\nOn parle du divorce des parents. Puis un ours.\n")
    (mem.root / "famille" / "mila.md").write_text(
        "---\nprenom: Mila\n---\n# Mila\n\n## Historique\n- 2026-11-10 : foot, divorce des parents\n- 2026-11-11 : piano\n"
    )
    mem.forget("le divorce des parents", who="Mila")
    everything = "".join(p.read_text() for p in mem.files() if not p.name.startswith("a_trier"))
    assert "divorce" not in everything
    assert "chaussettes" in everything and "Puis un ours." in everything and "piano" in everything


def test_read_memory_only_reads_memory_files(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    mem.note("DEMANDE D'OUBLI → le secret")
    (mem.root / "presence.json").write_text("{}")
    (mem.root / "famille" / "binaire.md").write_bytes(b"\xff\xfe\x00")
    for path in (".git/config", "a_trier.md", "presence.json", "../outside.md", "famille/binaire.md"):
        assert mem.read(path) is None, path
    assert mem.read(store.SUMMARY_FILE).startswith("# Ce que Flocon sait")


def test_one_memory_and_one_lock_per_folder(tmp_path):
    assert Memory.shared(tmp_path, "Flocon") is Memory.shared(tmp_path / ".", "Flocon")
    assert Memory(tmp_path)._files_lock is Memory(tmp_path)._files_lock is lock_for(tmp_path)
    assert JsonStore(tmp_path / "jeux" / "quiz.json", {}).lock is lock_for(tmp_path / "jeux" / "quiz.json")


def test_a_corrupt_file_is_set_aside_not_erased(tmp_path):
    f = tmp_path / "quiz.json"
    f.write_text('{"players": {"Mila": ')  # cut mid-write
    store_ = JsonStore(f, {"players": {}})
    assert store_.load() == {"players": {}}
    kept = list(tmp_path.glob("quiz.json.corrupt-*"))
    assert len(kept) == 1 and "Mila" in kept[0].read_text()
    store_.save({"players": {"Noam": {}}})
    assert load_json(f, {}) == {"players": {"Noam": {}}}


def test_a_parent_deletion_leaves_no_trace_in_git(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    card = mem.root / "famille" / "mila.md"
    card.write_text("---\nprenom: Mila\n---\n# Mila\n\n## À savoir\n- confidence : a peur du noir\n- a un chat\n")
    mem._commit("card")
    parents.edit(mem, "famille/mila.md", {"section": "À savoir", "index": 0, "expected": "confidence : a peur du noir"})
    log = git_log(mem)
    assert "peur du noir" not in log and "Correction par un parent" in log


def test_notes_survive_a_failed_condensation(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    mem.note("Léa a un lapin")
    assert "lapin" in mem.take_notes()
    mem.note("Tom a un chien")  # the condensation failed: next time both are there
    notes = mem.take_notes()
    assert "lapin" in notes and "chien" in notes
    mem.notes_done()
    assert mem.take_notes() is None
    assert json.loads(json.dumps(notes))  # plain text, nothing lost


def test_a_parent_can_delete_memory_files_but_not_git(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    (mem.root / "histoires" / "foret.md").write_text("# La forêt\n")
    for bad in (".git/config", ".gitignore", store.SUMMARY_FILE, "../x.md", "nope.md"):
        assert not mem.delete(bad), bad
    assert (mem.root / ".git" / "config").exists()
    assert mem.delete("histoires/foret.md") and not (mem.root / "histoires" / "foret.md").exists()
