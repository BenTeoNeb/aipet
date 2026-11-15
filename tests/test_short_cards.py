"""Long cards keep everything (the record); short cards, capped and rewritten by a model, are what
the prompt shows (no network: the model is faked)."""

import asyncio
from datetime import date, timedelta

from aipet import parents
from aipet.memory import Memory, condensation, short_cards
from aipet.memory import now as memory_now
from aipet.memory.cards import add_history

CARD = "---\nprenom: Mila\nage: 8\n---\n# Mila\n\n## Aime\n" + "".join(f"- goût {i}\n" for i in range(10))


def make(tmp_path, card=CARD):
    mem = Memory(tmp_path, "Flocon")
    p = mem.root / "famille" / "mila.md"
    p.write_text(card)
    return mem, p


def fake_model(monkeypatch, text="Adore le dessin et les chats. confidence : a peur de l'orage."):
    calls = []

    def model(cfg, system, prompt):
        calls.append(prompt)
        return text

    monkeypatch.setattr(short_cards, "_call_llm", model)
    return calls


def update(people):
    return {"memory": {"participants": [], "topics": [], "summary": ""}, "people": people, "stories": [],
            "pet_summary": ""}  # fmt: skip


def test_without_a_short_card_the_prompt_shows_a_trimmed_long_card(tmp_path):
    mem, _ = make(tmp_path)
    block = mem.context_block()
    assert "goût 9" in block and "goût 6" in block and "goût 5" not in block
    assert "et 6 de plus dans la fiche complète" in block
    assert mem.search("goût 2")  # the long card is still searchable


def test_a_condensation_writes_the_short_card_the_prompt_then_uses(tmp_path, monkeypatch):
    mem, p = make(tmp_path)
    calls = fake_model(monkeypatch, "x" * 2000)
    monkeypatch.setattr(condensation, "_call_llm", lambda cfg, prompt: update([]))
    turns = [{"role": "user", "text": "Coucou"}]
    asyncio.run(condensation.condense(mem, {"memory": {}}, turns, memory_now()))
    assert len(calls) == 1 and "goût 0" in calls[0]  # the model saw the whole long card
    short = short_cards.short_path(p)
    body = short.read_text().split("---", 2)[2].strip()
    assert len(body) == short_cards.SHORT_CARD_MAX  # capped
    assert "goût" not in mem.context_block()  # the prompt shows the short card, not the long one
    assert "goût" not in [h["excerpt"] for h in mem.search("xxxx")]  # short cards are not searched twice


def test_new_items_show_until_the_next_rewrite(tmp_path, monkeypatch):
    mem, p = make(tmp_path)
    fake_model(monkeypatch)
    short_cards.redo(mem, {}, p)
    person = {"name": "Mila", "likes": ["la trompette"], "dislikes": [], "facts": [], "remove": []}
    mem.apply(update([person]), memory_now(), allowed_new_names={"Mila"})
    block = mem.context_block()
    assert "Adore le dessin" in block and "Nouveau depuis :\n- Aime : la trompette" in block
    assert not short_cards.needs_redo(p, memory_now().date())  # one item, written today: not yet
    assert short_cards.needs_redo(p, memory_now().date() + timedelta(days=short_cards.REDO_AFTER_DAYS))


def test_a_rewrite_is_due_after_enough_new_items_or_a_removal(tmp_path, monkeypatch):
    mem, p = make(tmp_path)
    fake_model(monkeypatch)
    short_cards.redo(mem, {}, p)
    today = memory_now().date()
    p.write_text(CARD + "".join(f"- nouveau {i}\n" for i in range(short_cards.REDO_AFTER_NEW_ITEMS)))
    assert short_cards.needs_redo(p, today)
    short_cards.redo(mem, {}, p)
    p.write_text(CARD.replace("- goût 3\n", ""))  # something removed: the short card may still say it
    assert short_cards.needs_redo(p, today)


def test_forget_and_parent_corrections_drop_the_short_card(tmp_path, monkeypatch):
    mem, p = make(tmp_path)
    fake_model(monkeypatch)
    short = short_cards.short_path(p)
    short_cards.redo(mem, {}, p)
    mem.forget("orage", who="Mila")  # only in the short card here
    assert not short.exists() and "orage" not in mem.context_block()
    short_cards.redo(mem, {}, p)
    parents.edit(mem, "famille/mila.md", {"section": "Aime", "index": 0, "expected": "goût 0", "text": ""})
    assert not short.exists()
    short_cards.redo(mem, {}, p)
    assert mem.delete("famille/mila.md") and not short.exists()


def test_a_rewrite_is_dropped_if_the_long_card_changed_meanwhile(tmp_path, monkeypatch):
    mem, p = make(tmp_path)

    def model(cfg, system, prompt):
        p.write_text(CARD.replace("goût 9", "corrigé"))  # a parent's correction during the call
        return "Ancien texte"

    monkeypatch.setattr(short_cards, "_call_llm", model)
    assert not short_cards.redo(mem, {}, p)
    assert not short_cards.short_path(p).exists()


def test_the_long_card_keeps_its_whole_history():
    hist: list[str] = []
    start = date(2026, 1, 1)
    for i in range(40):
        hist = add_history(hist, (start + timedelta(days=i)).isoformat(), ["jeu"], keep=None)
    assert len(hist) == 40
