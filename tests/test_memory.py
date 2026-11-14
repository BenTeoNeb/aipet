"""Memory tests (no network: the model is faked)."""

import asyncio

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from aipet import character, memory
from aipet.memory import Memory, MemoryKeeper, condensation, parse_card, store, tools

CFG = {"memory": {}, "engines": {}}


def update(**kw):
    base = {
        "memory": {"participants": [], "topics": [], "summary": ""},
        "people": [],
        "stories": [],
        "pet_summary": "",
    }
    base.update(kw)
    return base


def person(name, **kw):
    p = {"name": name, "age": "", "role": "enfant", "likes": [], "dislikes": [], "facts": [], "remove": []}
    p.update(kw)
    return p


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path, "Flocon")


def card(mem, name="lea"):
    return parse_card((mem.root / "famille" / f"{name}.md").read_text())


def test_empty_remove_erases_nothing(mem):
    mem.apply(update(people=[person("Léa", likes=["les dinosaures"], facts=["a peur des orages"])]), memory.now())
    mem.apply(update(people=[person("Léa", remove=["", "le"])]), memory.now())
    _, sec = card(mem)
    assert sec["Aime"] == ["les dinosaures"] and sec["À savoir"] == ["a peur des orages"]


def test_remove_exact_ignoring_articles(mem):
    mem.apply(update(people=[person("Léa", likes=["les dinosaures", "les chats"])]), memory.now())
    mem.apply(update(people=[person("Léa", remove=["dinosaures"])]), memory.now())
    assert card(mem)[1]["Aime"] == ["les chats"]


def test_duplicates_with_articles(mem):
    mem.apply(update(people=[person("Léa", likes=["les dinosaures"])]), memory.now())
    mem.apply(update(people=[person("Léa", likes=["dinosaures", "Les Dinosaures"])]), memory.now())
    assert card(mem)[1]["Aime"] == ["les dinosaures"]


def test_history_one_line_per_day(mem):
    for topics in (["dinos"], ["foot"], ["dinos"]):
        mem.apply(
            update(memory={"participants": ["léa"], "topics": topics, "summary": "x"}, people=[person("Léa")]),
            memory.now(),
        )
    hist = card(mem)[1]["Historique"]
    assert len(hist) == 1 and hist[0].endswith("dinos, foot")


def test_history_even_when_nothing_new_was_learned(mem):
    """Mila and Noam came back on 12 and 13/11 but their cards only showed 11/11: the history was
    written only for people the model listed with new information."""
    mem.apply(update(people=[person("Mila")]), memory.now())
    later = memory.now().replace(day=memory.now().day % 27 + 1)
    labels = mem.apply(
        update(memory={"participants": ["Mila", "Inconnu", "un enfant"], "topics": ["devinette"], "summary": "x"}),
        later,
    )
    assert card(mem, "mila")[1]["Historique"] == [f"{later:%Y-%m-%d} : devinette"]
    assert labels == ["souvenirs/" + f"{later:%Y-%m-%d}.md", "famille/mila.md"]
    assert sorted(p.name for p in (mem.root / "famille").glob("*.md")) == ["mila.md"]  # no card created


def test_search_stopwords_and_word_prefix(mem):
    (mem.root / "souvenirs" / "2026-11-01.md").write_text("## 10h00\nLe chat de mamie s'appelle Moustache.\n")
    (mem.root / "souvenirs" / "2026-11-02.md").write_text("## 10h00\nOn a visité un château fort.\n")
    assert [h["file"] for h in mem.search("le chat")] == ["souvenirs/2026-11-01.md"]
    assert mem.search("les est que") == []
    assert mem.search("moustaches") and mem.search("chats")


def test_search_kind_maps_to_directory(mem):
    (mem.root / "souvenirs" / "2026-11-01.md").write_text("## 10h00\nUn dragon bleu.\n")
    (mem.root / "histoires" / "le-dragon.md").write_text("# Le dragon\n\nUn dragon rouge.\n")
    assert [h["file"] for h in mem.search("dragon", kind="stories")] == ["histoires/le-dragon.md"]
    assert [h["file"] for h in mem.search("dragon", kind="memories")] == ["souvenirs/2026-11-01.md"]


def test_forget_precise(mem):
    mem.apply(update(people=[person("Léa", facts=["le cadeau de maman est un vélo", "a un chat"])]), memory.now())
    (mem.root / "souvenirs" / "2026-11-03.md").write_text(
        "# J\n\n## 10h00\nLe cadeau de maman est un vélo. On a ri.\n\n## 11h00\nDevinettes.\n"
    )
    res = mem.forget("cadeau de maman")
    assert res["removed_items"] == 2
    assert card(mem)[1]["À savoir"] == ["a un chat"]
    day = (mem.root / "souvenirs" / "2026-11-03.md").read_text()
    assert "vélo" not in day and "On a ri" in day and "Devinettes" in day and "## 10h00" in day


def test_forget_does_not_destroy_too_much(mem):
    mem.apply(
        update(people=[person("Emma", likes=["les chats"]), person("Léa", facts=["copine d'Emma"])]), memory.now()
    )
    (mem.root / "histoires" / "le-chat-botte.md").write_text("# Le chat botté\n\nIl a des bottes.\n")
    assert mem.forget("Emma").get("refused")  # one word, nobody named: too vague
    assert mem.forget("Alexandrine").get("refused")  # even a single long word
    assert (mem.root / "famille" / "emma.md").exists() and (mem.root / "histoires" / "le-chat-botte.md").exists()
    mem.forget("le chat botté a des bottes")  # a story is never deleted
    assert (mem.root / "histoires" / "le-chat-botte.md").exists()


def test_forget_for_one_person(mem):
    mem.apply(
        update(people=[person("Léa", likes=["le foot", "les dinosaures"]), person("Tom", likes=["les dinosaures"])]),
        memory.now(),
    )
    res = mem.forget("le foot", who="Léa")  # one word is enough when we know who
    assert res["removed_items"] == 1 and card(mem, "lea")[1]["Aime"] == ["les dinosaures"]
    mem.forget("dinosaures", who="Léa")  # only Léa, not Tom
    assert card(mem, "lea")[1]["Aime"] == [] and card(mem, "tom")[1]["Aime"] == ["les dinosaures"]
    assert mem.forget("foot", who="Zoé").get("refused")


def test_forget_words_in_any_order(mem):
    mem.apply(update(people=[person("Léa", facts=["le cadeau de maman est un vélo"])]), memory.now())
    assert mem.forget("cadeau pour maman", who="Léa")["removed_items"] == 1


def test_search_card_keeps_the_name(mem):
    mem.apply(
        update(people=[person("Léa", likes=["les dinosaures"]), person("Tom", likes=["les dinosaures"])]), memory.now()
    )
    assert mem.search("Léa dinosaures")[0]["file"] == "famille/lea.md"


def test_forget_nothing_found_is_honest(mem):
    res = mem.forget("le secret du gâteau d'anniversaire")
    assert res["removed_items"] == 0 and "DEMANDE D'OUBLI" in (mem.root / "a_trier.md").read_text()


def test_search_by_date_and_not_by_path(mem):
    (mem.root / "souvenirs" / "2026-10-01.md").write_text("## 10h00\nOn a joué aux devinettes.\n")
    (mem.root / "souvenirs" / "2026-11-01.md").write_text("## 10h00\nUn dragon bleu.\n")
    (mem.root / "histoires" / "la-princesse.md").write_text("# La princesse\n\nElle danse.\n")
    assert [h["file"] for h in mem.search("2026-10-01")] == ["souvenirs/2026-10-01.md"]
    assert [h["file"] for h in mem.search("histoire dragon")] == ["souvenirs/2026-11-01.md"]
    assert mem.search("devinettes 2026-11-01") == []


def test_apply_writes_nothing_on_error(mem, monkeypatch):
    u = update(memory={"participants": ["Léa"], "topics": ["x"], "summary": "résumé"}, people=[person("Léa")])
    monkeypatch.setattr(store, "render_card", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        mem.apply(u, memory.now())
    assert not list((mem.root / "souvenirs").glob("*.md"))


def test_stories_written_and_finished(mem):
    mem.apply(update(stories=[{"title": "Le dragon", "state": "Il vole.", "finished": False}]), memory.now())
    assert "Il vole." in (mem.root / "histoires" / "le-dragon.md").read_text()
    mem.apply(update(stories=[{"title": "Le dragon", "state": "Fin.", "finished": True}]), memory.now())
    assert not (mem.root / "histoires" / "le-dragon.md").exists()


def test_history_edited_by_hand(mem):
    (mem.root / "famille" / "lea.md").write_text(
        f"---\nprenom: Léa\n---\n# Léa\n\n## Historique\n- {memory.now():%Y-%m-%d} sans deux-points\n"
    )
    mem.apply(
        update(memory={"participants": ["Léa"], "topics": ["foot"], "summary": "x"}, people=[person("Léa")]),
        memory.now(),
    )
    assert card(mem)[1]["Historique"][-1].endswith("foot")


def keeper_with(mem, n_user=3, keep=4):
    ctx = LLMContext()
    k = MemoryKeeper(mem, CFG, ctx, "base", keep=keep, max_turns=99)
    for i in range(n_user):
        ctx.add_message({"role": "user", "content": f"message {i}"})
        k.tag_last_user_message()
        ctx.add_message({"role": "assistant", "content": f"réponse {i}"})
    return k, ctx


def test_notes_taken_during_condensation_are_not_lost(mem, monkeypatch):
    mem.note("Léa a un lapin")

    def slow(cfg, prompt):
        assert "lapin" in prompt
        mem.note("Tom a un chien")  # arrives during the model call
        return update()

    monkeypatch.setattr(condensation, "_call_llm", slow)
    k, _ = keeper_with(mem)
    asyncio.run(k.checkpoint("test"))
    left = (mem.root / "a_trier.md").read_text()
    assert "chien" in left and "lapin" not in left


def test_no_double_condensation(mem, monkeypatch):
    calls = []

    def count(cfg, prompt):
        calls.append(1)
        return update()

    monkeypatch.setattr(condensation, "_call_llm", count)
    k, _ = keeper_with(mem)

    async def both():
        await asyncio.gather(k.checkpoint("a"), k.checkpoint("b"))

    asyncio.run(both())
    assert len(calls) == 1


def test_failure_keeps_the_chunk_and_does_not_duplicate(mem, monkeypatch):
    def boom(cfg, prompt):
        raise RuntimeError("network")

    monkeypatch.setattr(condensation, "_call_llm", boom)
    k, _ = keeper_with(mem)
    asyncio.run(k.checkpoint("x"))
    assert k.done == 0 and not list((mem.root / "conversations").glob("*"))
    assert k.last["error"] and k.last["reason"] == "x"


def test_trim_starts_on_a_user_message(mem, monkeypatch):
    monkeypatch.setattr(condensation, "_call_llm", lambda c, p: update())
    k, ctx = keeper_with(mem, n_user=10, keep=5)
    asyncio.run(k.checkpoint("x"))
    msgs = ctx.get_messages()
    assert msgs[0]["role"] == "user" and len(msgs) <= 6 and k.done == len(msgs)


def test_reset_during_condensation(mem, monkeypatch):
    k, ctx = keeper_with(mem)

    def during(cfg, prompt):
        k.reset_context()
        return update()

    monkeypatch.setattr(condensation, "_call_llm", during)
    asyncio.run(k.checkpoint("x"))
    assert k.done == 0 and ctx.get_messages() == []
    ctx.add_message({"role": "user", "content": "nouveau"})
    assert memory.turns_from_messages(ctx.get_messages()[k.done :])


def test_label_with_date_and_speaker(mem):
    k, ctx = keeper_with(mem, n_user=1)
    k.who.set("léa", "sure", ["Tom"])
    ctx.add_message({"role": "user", "content": "coucou"})
    k.tag_last_user_message()
    tag = ctx.get_messages()[-1]["content"]
    assert tag.startswith("⟦") and "/" in tag and "Léa (sûr)" in tag and "Tom" in tag


# ---- name confirmation ------------------------------------------------------
def test_known_name_accepted(mem):
    mem.apply(update(people=[person("Mila"), person("Noam")]), memory.now())
    k, _ = keeper_with(mem, n_user=1)
    assert k.who.from_tag("mila") == "known" and k.who.current == "Mila"


def test_similar_name_stays_to_confirm(mem):
    mem.apply(update(people=[person("Mila"), person("Noam")]), memory.now())
    k, _ = keeper_with(mem, n_user=1)
    k.who.set("Mila", "sure")
    assert k.who.from_tag("Lila", pet_just_said="Alors, la suite de l'histoire ?") == "to_confirm"
    assert k.who.current == "Mila"  # the speaker does not change
    assert "à confirmer : Lila (ressemble à Mila)" in k.who.tag()


def test_new_name_confirmed_after_the_question(mem):
    k, _ = keeper_with(mem, n_user=1)
    assert k.who.from_tag("Zoé") == "to_confirm"
    assert k.who.from_tag("Zoé", pet_just_said="Tu t'appelles bien Zoé ?") == "confirmed"
    assert k.who.current == "Zoé" and "à confirmer" not in k.who.tag()
    assert k.who.from_tag("zoe") == "known"  # confirmed once, recognized afterwards


def test_speaker_tag_uses_the_pets_last_sentence(mem):
    k, ctx = keeper_with(mem, n_user=0)
    ctx.add_message({"role": "user", "content": "je m'appelle Zoé"})
    ctx.add_message({"role": "assistant", "content": "Oh ! Tu t'appelles bien Zoé ?"})
    ctx.add_message({"role": "user", "content": "oui"})
    asyncio.run(k.on_tag("parle:Zoé"))
    assert k.who.current == "Zoé"


def test_name_heard_in_the_sentence(mem):
    mem.apply(update(people=[person("Mila"), person("Noam")]), memory.now())
    k, _ = keeper_with(mem, n_user=1)
    k.who.set("Mila", "sure")
    assert k.who.from_speech("à moi à moi papa. Bonjour, je m'appelle Noam.") == "known"
    assert k.who.current == "Noam"
    assert k.who.from_speech("Bonjour, je m'appelle Lila.") == "to_confirm"
    assert k.who.current == "Noam" and "Lila (ressemble à Mila)" in k.who.tag()
    assert k.who.from_speech("Non, c'était pas Lila, c'est Mila.") == "known"
    assert k.who.current == "Mila" and "Lila" not in k.who.tag()


def test_yes_confirms_the_asked_name(mem):
    k, _ = keeper_with(mem, n_user=1)
    k.who.from_speech("Bonjour Flocon, moi c'est Zoé.")
    assert "à confirmer : Zoé" in k.who.tag()
    assert k.who.from_speech("Oui !", "Bonjour Zoé ! Dis-moi, Zoé, tu as quel âge ?") is None  # not the name question
    assert k.who.from_speech("Oui !", "Bonjour Zoé ! Tu t'appelles Zoé, c'est bien ça ?") == "confirmed"
    assert k.who.current == "Zoé"


def test_labelling_detects_the_name_before_the_model(mem):
    mem.apply(update(people=[person("Noam")]), memory.now())
    k, ctx = keeper_with(mem, n_user=0)
    ctx.add_message({"role": "user", "content": "Bonjour, je m'appelle Noam."})
    k.tag_last_user_message()
    assert "parle : Noam" in ctx.get_messages()[-1]["content"]


def test_tool_names_and_prompts_are_consistent():
    """The French instructions must mention the real (English) tool names."""
    assert memory.MEMORY_TOOLS == {"search_memory", "read_memory", "forget"}
    for name in ("search_memory", "read_memory", "forget"):
        assert f"`{name}`" in tools.MEMORY_INSTRUCTIONS
    for key in ("memory", "people", "stories", "pet_summary", "remove", "finished"):
        assert key in condensation.condensation_instructions(character.load("flocon"))


# ---- Flocon's own life (MA_VIE.md) ------------------------------------------------------
def life(**kw):
    base = {"chosen_traits": [], "words": [], "adventures": [], "inside_jokes": []}
    base.update(kw)
    return update(pet_life=base)


def test_life_file_created_and_in_prompt(mem):
    assert (mem.root / store.LIFE_FILE).exists()
    assert "## Ma vie de Flocon" in mem.context_block() and store.LIFE_EMPTY in mem.context_block()


def test_life_traits_words_adventures_jokes(mem):
    when = memory.now()
    mem.apply(
        life(
            chosen_traits=[{"trait": "couleur préférée", "value": "vert pomme", "chosen_by": "Mila"}],
            words=[{"word": "trampoline", "taught_by": "Noam"}],
            adventures=["Ma pelote rouge a roulé sous le canapé imaginaire."],
            inside_jokes=[{"with": "Noam", "joke": "le hélicochat"}],
        ),
        when,
    )
    # A new choice for the same trait replaces the old one; a known word is not added twice.
    mem.apply(
        life(
            chosen_traits=[{"trait": "Couleur préférée", "value": "bleu nuit", "chosen_by": "Noam"}],
            words=[{"word": "Trampoline", "taught_by": "Mila"}, {"word": "gâteau", "taught_by": ""}],
        ),
        when,
    )
    txt = (mem.root / store.LIFE_FILE).read_text()
    sec = store.parse_life(txt)
    assert len(sec[store.LIFE_CHOSEN]) == 1 and "bleu nuit" in sec[store.LIFE_CHOSEN][0]
    assert "choisi par Noam" in sec[store.LIFE_CHOSEN][0]
    assert [w.split(" (")[0] for w in sec[store.LIFE_WORDS]] == ["trampoline", "gâteau"]
    assert "pelote rouge" in sec[store.LIFE_ADVENTURES][0]
    assert sec[store.LIFE_JOKES] == ["avec Noam : le hélicochat"]
    assert "bleu nuit" in mem.context_block()


def test_life_caps_adventures_and_prompt_words(mem):
    when = memory.now()
    for i in range(store.MAX_ADVENTURES + 5):
        mem.apply(life(adventures=[f"aventure {i}"], words=[{"word": f"mot{i}x", "taught_by": ""}]), when)
    sec = store.parse_life((mem.root / store.LIFE_FILE).read_text())
    assert len(sec[store.LIFE_ADVENTURES]) == store.MAX_ADVENTURES
    assert sec[store.LIFE_ADVENTURES][-1].endswith(f"aventure {store.MAX_ADVENTURES + 4}")
    block = mem.context_block()
    assert "mot0x" not in block or store.MAX_ADVENTURES + 5 <= store.WORDS_IN_PROMPT
    assert f"mot{store.MAX_ADVENTURES + 4}x" in block


def test_empty_life_update_writes_nothing(mem):
    before = (mem.root / store.LIFE_FILE).read_text()
    labels = mem.apply(life(), memory.now())
    assert store.LIFE_FILE not in labels and (mem.root / store.LIFE_FILE).read_text() == before


def test_birthday_line():
    from datetime import date

    from aipet.memory.keeper import birthday_line

    assert birthday_line(None, date(2026, 11, 11)) == ""
    assert "tout premier jour" in birthday_line("2026-11-11", date(2026, 11, 11))
    assert "tu as 1 an !" in birthday_line("2026-11-11", date(2027, 11, 11))
    assert "tu as 3 ans" in birthday_line("2026-11-11", date(2029, 11, 11))
    assert "11 novembre" in birthday_line("2026-11-11", date(2027, 3, 2))


# ---- age and school ----------------------------------------------------------------------
def test_school_level_and_topics_on_card(mem):
    when = memory.now()
    mem.apply(
        update(people=[person("Mila", age="7 ans", school_class="ce 1", school_topics=["les tables de 2"])]), when
    )
    mem.apply(update(people=[person("Mila", school_topics=["Les tables de 2", "lire l'heure"])]), when)
    meta, sec = card(mem, "mila")
    assert meta["classe"] == "CE1" and meta["classe_note_le"] == f"{when:%Y-%m-%d}"
    assert [t.split(" : ", 1)[1] for t in sec[store.SCHOOL]] == ["les tables de 2", "lire l'heure"]
    block = mem.context_block()
    assert "Aujourd'hui : environ 7 ans, en CE1." in block
    assert mem.school_levels(when.date()) == ["CE1"]


def test_school_level_guessed_from_age_when_unknown(mem):
    mem.apply(update(people=[person("Noam", age="9 ans")]), memory.now())
    assert "sans doute en CM1 (classe à confirmer)" in mem.context_block()
    assert mem.school_levels(memory.now().date()) == ["CM1"]


def test_school_advances_at_rentree():
    from datetime import date

    from aipet import school

    assert school.current_level("CE1", date(2026, 11, 11), date(2027, 8, 31)) == "CE1"
    assert school.current_level("CE1", date(2026, 11, 11), date(2027, 9, 1)) == "CE2"
    assert school.current_level("CM2", date(2026, 11, 11), date(2028, 9, 1)) == "5e"
    assert school.current_age(7, date(2026, 11, 11), date(2027, 11, 10)) == 7
    assert school.current_age(7, date(2026, 11, 11), date(2027, 11, 11)) == 8
    assert [school.normalize_level(t) for t in ("je suis en ce 1", "grande section", "6ème", "rien")] == [
        "CE1", "GS", "6e", None,
    ]  # fmt: skip


def test_landmarks_only_for_known_levels():
    from aipet import school

    txt = school.landmarks_for(["CM1", "CP", "CP"])
    assert txt.index("CP :") < txt.index("CM1 :") and txt.count("CP :") == 1 and "CE2" not in txt
    assert school.landmarks_for([]) == ""
    assert all(info.get("reperes") for info in school.landmarks().values())


# ---- time, presence and energy in the keeper ---------------------------------------------
def make_keeper(mem, monkeypatch, when):
    from datetime import datetime as dt

    from pipecat.processors.aggregators.llm_context import LLMContext

    from aipet.memory import keeper as keeper_mod
    from aipet.rhythm import TZ, Rhythm

    clock = {"t": dt(*when, tzinfo=TZ)}
    monkeypatch.setattr(keeper_mod, "now", lambda: clock["t"])
    k = MemoryKeeper(mem, CFG, LLMContext(), "BASE")
    k.rhythm = Rhythm(holidays=[])
    return k, clock


def test_presence_snapshot_and_lines(mem, monkeypatch):
    from datetime import datetime as dt
    from datetime import timedelta

    from aipet.rhythm import TZ

    k, clock = make_keeper(mem, monkeypatch, (2026, 11, 12, 18, 0))
    assert k.presence_lines() == "C'est la toute première fois qu'on te parle."
    k.who.set("Mila", "sure", [])
    k.context.add_message({"role": "user", "content": "Coucou Flocon"})
    k.tag_last_user_message()
    clock["t"] = dt(2026, 11, 13, 17, 0, tzinfo=TZ)
    asyncio.run(k.on_wake())  # new session: snapshot of « last time »
    lines = k.presence_lines()
    assert "hier soir" in lines and "Mila : hier soir" in lines
    assert "presence.json" in (mem.root / ".gitignore").read_text()
    clock["t"] += timedelta(minutes=5)
    assert "Mila : hier soir" in k.presence_lines()  # stays the session snapshot


def test_sleep_reason_night_with_bedtime_grace(mem, monkeypatch):
    from datetime import datetime as dt

    from aipet.rhythm import TZ

    k, clock = make_keeper(mem, monkeypatch, (2026, 11, 12, 20, 25))  # Thursday, bedtime 20:30
    k.last_activity = clock["t"]
    assert k.sleep_reason() is None
    assert "bientôt l'heure de dormir (20h30)" in k.turn_hint()
    clock["t"] = dt(2026, 11, 12, 20, 33, tzinfo=TZ)  # just after bedtime, still talking: grace
    k.last_activity = clock["t"]
    assert k.sleep_reason() is None and "dis bonne nuit" in k.turn_hint()
    clock["t"] = dt(2026, 11, 12, 20, 45, tzinfo=TZ)  # grace over
    assert k.sleep_reason() == "night"
    clock["t"] = dt(2026, 11, 13, 3, 0, tzinfo=TZ)
    k.last_activity = clock["t"]
    assert k.sleep_reason() == "night"


def test_energy_hints_and_nap(mem, monkeypatch):
    from aipet.energy import Energy

    k, _ = make_keeper(mem, monkeypatch, (2026, 11, 12, 16, 0))

    class FakeEnergy(Energy):
        def __init__(self, state):
            super().__init__()
            self._s = state

        def state(self):
            return self._s

        @property
        def asleep(self):
            return self._s == "asleep"

    k.energy = FakeEnergy("tired")
    assert "tu commences à être fatigué" in k.turn_hint()
    k.energy = FakeEnergy("very_tired")
    assert "dis au revoir joyeusement" in k.turn_hint() and "reste avec lui" in k.turn_hint()
    k.energy = FakeEnergy("asleep")
    assert k.sleep_reason() == "nap"
    k.context.add_message({"role": "user", "content": "Coucou"})
    k.energy = FakeEnergy("very_tired")
    k.tag_last_user_message()
    assert "très fatigué" in k.context.get_messages()[-1]["content"].split("⟧")[0]


# ---- condensation hardening (review) -----------------------------------------------------
def test_rude_words_and_mocking_jokes_never_enter_the_pets_life(mem):
    mem.apply(
        life(
            words=[{"word": "crétin", "taught_by": "Noam"}, {"word": "trampoline", "taught_by": "Noam"}],
            inside_jokes=[{"with": "Noam", "joke": "Mila est un bébé"}, {"with": "Noam", "joke": "le hélicochat"}],
        ),
        memory.now(),
    )
    sec = store.parse_life((mem.root / store.LIFE_FILE).read_text())
    assert [w.split(" (")[0] for w in sec[store.LIFE_WORDS]] == ["trampoline"]
    assert sec[store.LIFE_JOKES] == ["avec Noam : le hélicochat"]


def test_cards_only_for_confirmed_names(mem):
    when = memory.now()
    mem.apply(update(people=[person("Lila", likes=["le vert"]), person("un enfant", likes=["x"])]), when,
              allowed_new_names={"Mila"})  # fmt: skip
    assert not (mem.root / "famille" / "lila.md").exists() and not list((mem.root / "famille").glob("un*"))
    mem.apply(update(people=[person("Mila", likes=["le vert"])]), when, allowed_new_names={"Mila"})
    assert (mem.root / "famille" / "mila.md").exists()
    mem.apply(update(people=[person("Mila", likes=["les licornes"])]), when, allowed_new_names=set())
    assert "les licornes" in (mem.root / "famille" / "mila.md").read_text()  # existing cards still updated


def test_forget_scrubs_transcripts_summary_and_git_history(mem):
    import subprocess

    when = memory.now()
    mem.save_transcript(when, [{"role": "user", "text": "Le cadeau de maman est un vélo rouge"},
                               {"role": "assistant", "text": "Chut, c'est un secret !"}])  # fmt: skip
    (mem.root / store.SUMMARY_FILE).write_text(
        "# Ce que Flocon sait\n\nMila aime le vert. Le cadeau de maman est un vélo.\n"
    )
    mem._commit("before")
    res = mem.forget("le cadeau de maman")
    assert res["removed_items"] >= 2
    conv = "".join(p.read_text() for p in (mem.root / "conversations").glob("*.jsonl"))
    assert "cadeau" not in conv and "secret" in conv
    summary = (mem.root / store.SUMMARY_FILE).read_text()
    assert "cadeau" not in summary and "Mila aime le vert." in summary
    history = subprocess.run(["git", "log", "-p", "--all"], cwd=mem.root, capture_output=True, text=True).stdout
    assert "vélo" not in history  # older versions are gone


def test_session_logs_are_scrubbed(tmp_path, monkeypatch):
    from aipet import session_log

    monkeypatch.setattr(session_log, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(session_log, "LOG_DIR", tmp_path)
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "s.jsonl").write_text('{"text": "Le cadeau de Maman"}\n{"text": "bonjour"}\n')
    (tmp_path / "aipet.log").write_text("transcript: le cadeau de maman est un vélo\nok\n")
    assert session_log.scrub(["cadeau", "maman"]) == 2
    assert "cadeau" not in (tmp_path / "sessions" / "s.jsonl").read_text() + (tmp_path / "aipet.log").read_text()


def test_condensation_backs_off_then_gives_up_and_moves_on(mem, monkeypatch):
    from aipet.memory import keeper as keeper_mod

    def boom(cfg, prompt):
        raise RuntimeError("model down")

    monkeypatch.setattr(condensation, "_call_llm", boom)
    k, ctx = keeper_with(mem)
    asyncio.run(k.checkpoint("turns"))
    assert k._failures == 1 and k._retry_after is not None
    calls = []
    monkeypatch.setattr(k, "checkpoint", lambda reason: calls.append(reason))
    k.trigger("turns")
    assert calls == []  # backing off: not retried at every message
    monkeypatch.undo()
    monkeypatch.setattr(condensation, "_call_llm", boom)
    for _ in range(keeper_mod.MAX_CONDENSE_FAILURES - 1):
        asyncio.run(k.checkpoint("manual"))
    assert k.done == len(ctx.get_messages()) or k.done > 0  # gave up: context can shrink
    assert list((mem.root / "conversations").glob("*.jsonl"))  # raw transcript kept
    assert k._failures == 0


@pytest.mark.parametrize(
    "text,yes",
    [("Non non, dodo là.", True), ("Bonne nuit Flocon", True), ("je vais me coucher", True), ("au lit !", True),
     ("je veux pas faire dodo", False), ("pas encore dormir", False), ("un doudou là", False), ("Oui.", False)],
)  # fmt: skip
def test_says_goodnight(text, yes):
    from aipet.memory.keeper import says_goodnight

    assert says_goodnight(text) is yes


def test_goodnight_ends_the_activity_then_the_pet_sleeps(mem, monkeypatch):
    from datetime import datetime as dt

    from aipet.rhythm import TZ

    k, clock = make_keeper(mem, monkeypatch, (2026, 11, 12, 20, 19))  # Thursday, bedtime 20:30
    ended = []
    k.end_skill = lambda reason: ended.append(reason)
    k.context.add_message({"role": "user", "content": "Non non, dodo là."})
    k.tag_last_user_message()
    label = k.context.get_messages()[-1]["content"]
    assert ended == ["goodnight"] and "dis-lui bonne nuit" in label and "sans rien proposer" in label
    assert k.sleep_reason() is None  # this turn: Flocon says good night
    k.context.add_message({"role": "assistant", "content": "Bonne nuit Mila !"})
    k.context.add_message({"role": "user", "content": "Flocon ?"})
    k.tag_last_user_message()
    assert k.sleep_reason() == "night"  # from the next message, asleep until morning
    clock["t"] = dt(2026, 11, 13, 7, 31, tzinfo=TZ)
    assert k.sleep_reason() is None


def test_goodnight_in_the_daytime_does_not_put_the_pet_to_sleep(mem, monkeypatch):
    k, _ = make_keeper(mem, monkeypatch, (2026, 11, 12, 15, 0))
    k.context.add_message({"role": "user", "content": "bonne nuit"})
    k.tag_last_user_message()
    k.context.add_message({"role": "user", "content": "coucou"})
    k.tag_last_user_message()
    assert k.sleep_reason() is None and k.asleep_until is None
