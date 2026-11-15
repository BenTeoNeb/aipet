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


def test_forget_reaches_game_records(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    games = mem.root / "jeux"
    games.mkdir()
    (games / "devine.json").write_text(
        json.dumps({"players": {"Mila": {"games": [{"answer": "le chien de mamie Rose", "found": True}]}}})
    )
    (mem.root / "histoires" / "univers.json").write_text(
        json.dumps({"characters": {"piko": {"name": "Piko", "traits": ["a peur du chien de mamie Rose", "rigolo"]}}})
    )
    mem.forget("le chien de mamie Rose")
    left = (games / "devine.json").read_text() + (mem.root / "histoires" / "univers.json").read_text()
    assert "mamie" not in left and "rigolo" in left and '"found": true' in left


def test_search_never_finds_pending_forget_notes(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    mem.forget("le cadeau de maman")
    assert "cadeau" in (mem.root / store.NOTES_FILE).read_text()  # quoted for the condensation…
    assert mem.search("cadeau maman") == []  # …but never found by the pet
    assert mem.search("cadeau maman", kind="../") == []  # an unknown kind cannot widen the search


def test_a_one_word_forget_about_someone_reaches_their_memories(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    (mem.root / "famille" / "lea.md").write_text("---\nprenom: Léa\n---\n# Léa\n\n## Faits\n- le divorce\n")
    (mem.root / "souvenirs" / "2026-11-10.md").write_text(
        "# Mardi\n\n## 18h00\nLéa parle du divorce. Noam a vu un divorce au cinéma. Léa aime le piano.\n"
    )
    mem.forget("divorce", who="Léa")
    day = (mem.root / "souvenirs" / "2026-11-10.md").read_text()
    assert "Léa parle du divorce" not in day
    assert "Noam a vu un divorce au cinéma." in day and "Léa aime le piano." in day  # other sentences kept
    assert "divorce" not in (mem.root / "famille" / "lea.md").read_text()
    assert store.ForgetRequest.parse("divorce", "Léa").log_words == ["divorce", "lea"]


def test_any_parent_correction_leaves_no_old_version_in_git(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    card = mem.root / "famille" / "noam.md"
    card.write_text("---\nprenom: Noam\n---\n# Noam\n\n## Faits\n- confidence : a peur du noir\n")
    (mem.root / store.SUMMARY_FILE).write_text("# Résumé\n\nNoam fait des cauchemars.\n")
    mem._commit("card")
    parents.edit(mem, "famille/noam.md", {"section": "Faits", "index": 0,
                                          "expected": "confidence : a peur du noir", "text": "aime les veilleuses"})  # fmt: skip
    assert "peur" not in git_log(mem)
    parents.edit(mem, store.SUMMARY_FILE, {"text": "# Résumé\n\nNoam aime les veilleuses."})
    assert "cauchemars" not in git_log(mem)


def test_a_parent_correction_during_a_condensation_wins(tmp_path, monkeypatch):
    mem = Memory(tmp_path, "Flocon")
    card = mem.root / "famille" / "noam.md"
    card.write_text("---\nprenom: Noam\n---\n# Noam\n\n## Faits\n- a peur du noir\n")

    def model_answers_after_the_parent(cfg, prompt):
        parents.edit(mem, "famille/noam.md", {"section": "Faits", "index": 0, "expected": "a peur du noir", "text": ""})
        parents.edit(mem, store.SUMMARY_FILE, {"text": "# Résumé\n\nNoam aime le foot."})
        return update(
            people=[{"name": "Noam", "likes": [], "dislikes": [], "facts": ["a peur du noir"], "remove": []}],
            pet_summary="Noam a peur du noir.",
        )

    monkeypatch.setattr(condensation, "_call_llm", model_answers_after_the_parent)
    turns = [{"role": "user", "text": "J'ai peur du noir."}]
    asyncio.run(condensation.condense(mem, {"memory": {}}, turns, memory_now()))
    assert "peur" not in card.read_text()
    assert mem.summary_text() == "# Résumé\n\nNoam aime le foot.\n"


def test_a_card_deleted_by_a_parent_is_not_recreated(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    (mem.root / "famille" / "noam.md").write_text("---\nprenom: Noam\n---\n# Noam\n\n## Aime\n- le foot\n")
    assert mem.delete("famille/noam.md")
    person = {"name": "Noam", "likes": ["le foot"], "dislikes": [], "facts": [], "remove": []}
    mem.apply(update(people=[person]), memory_now(), allowed_new_names={"Noam"})
    assert not (mem.root / "famille" / "noam.md").exists()


def test_parent_edit_checks_the_real_path(tmp_path):
    mem = Memory(tmp_path, "Flocon")
    (mem.root / store.LIFE_FILE).write_text(store.render_life({store.LIFE_JOKES: ["avec Mila : les chaussettes"]}))
    parents.edit(mem, "famille/../MA_VIE.md", {"section": store.LIFE_JOKES, "index": 0,
                                               "expected": "avec Mila : les chaussettes", "text": ""})  # fmt: skip
    assert "prenom:" not in (mem.root / store.LIFE_FILE).read_text()  # still the life format, not a card


def test_forget_reaches_the_conversation_in_progress(tmp_path):
    from pipecat.processors.aggregators.llm_context import LLMContext

    from aipet.conversation import Conversation
    from aipet.memory.tools import register_tools

    mem = Memory(tmp_path, "Flocon")
    ctx = LLMContext()
    k = Conversation(mem, {"memory": {}}, ctx, "BASE")
    ctx.set_messages([
        {"role": "user", "content": "Le cadeau de maman est un vélo. J'aime les crêpes."},
        {"role": "assistant", "content": "Super !", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "forget", "arguments": '{"what": "cadeau maman"}'}}]},
    ])  # fmt: skip

    class Llm:
        def register_function(self, name, fn):
            setattr(self, name, fn)

    llm = Llm()
    register_tools(llm, mem, on_forget=k.forget_in_context)

    class Params:
        arguments = {"what": "le cadeau de maman"}
        result = None

        async def result_callback(self, res, properties=None):
            self.result = res

    asyncio.run(llm.forget(Params()))
    text = json.dumps(ctx.get_messages(), ensure_ascii=False)
    assert "cadeau" not in text and "crêpes" in text


def test_pipecat_debug_lines_stay_out_of_the_log_file():
    from loguru import logger

    from aipet.session_log import _file_filter

    def rec(name, level):
        return {"name": name, "level": logger.level(level)}

    assert not _file_filter(rec("pipecat.services.openai.base_llm", "DEBUG"))
    assert _file_filter(rec("pipecat.services.llm_service", "WARNING"))
    assert _file_filter(rec("aipet.turns", "DEBUG"))
