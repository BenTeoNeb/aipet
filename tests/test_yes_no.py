"""« Ni oui ni non »: refereed by code, records and difficulty per child (no network)."""

import asyncio
from datetime import datetime

import pytest

from aipet.conversation import Conversation
from aipet.games import yes_no
from aipet.games.common import Host
from aipet.games.yes_no import TARGET, YesNoGame, forbidden_word
from aipet.memory import Memory
from aipet.rhythm import TZ


class Who:
    def __init__(self, name):
        self.current, self.certainty, self.present = name, "sure", []


class Keeper(Host):
    def __init__(self, name):
        self.who = Who(name)


@pytest.fixture
def game(tmp_path, monkeypatch):
    calls, counter = [], {"n": 0}

    def fake_generate(cfg, player, level, difficulty, exclude, n):
        calls.append({"player": player, "difficulty": difficulty, "exclude": list(exclude)})
        out = []
        for _ in range(n):
            counter["n"] += 1
            out.append(f"Question piège {counter['n']} ?")
        return out

    monkeypatch.setattr(yes_no, "generate_questions", fake_generate)
    monkeypatch.setattr(yes_no, "now", lambda: datetime(2026, 11, 12, 17, 0, tzinfo=TZ))
    g = YesNoGame({}, Memory(tmp_path).root, Keeper("Mila"))
    return g, calls


@pytest.mark.parametrize(
    "text,word",
    [("Oui !", "oui"), ("ouais bien sûr", "ouais"), ("Non non", "non"), ("nan", "nan"), ("OUIII", "ouiii"),
     ("Peut-être bien", None), ("bien sûr que je l'aime", None), ("je dirais nonante", None),
     ("C'est un pingouin", None)],
)  # fmt: skip
def test_forbidden_words(text, word):
    assert forbidden_word(text) == word


def test_round_lost_then_record_and_revanche(game):
    g, calls = game
    start = asyncio.run(g.start())
    assert start["record"] == 0 and start["first_question"] == "Question piège 1 ?" and start["target"] == TARGET
    v1 = g.on_user_message("J'adore les bonbons")
    assert v1.startswith("ni oui ni non : réponse tenue (1 d'affilée") and "« Question piège 2 ? »" in v1
    g.on_user_message("Peut-être")
    lost = g.on_user_message("Oui !")
    assert "PERDU" in lost and "« oui »" in lost and "après 2 réponses tenues" in lost and g.round is None
    assert g.on_user_message("non") is None  # no game: nothing refereed
    asyncio.run(g.start())
    assert calls[1]["exclude"] == ["Question piège 1 ?", "Question piège 2 ?", "Question piège 3 ?"]
    for _ in range(4):
        g.on_user_message("bof")
    lost = g.on_user_message("nan")
    assert "NOUVEAU RECORD !" in lost and g.history("Mila")["best"] == 4


def test_win_raises_difficulty_and_quick_loss_lowers_it(game):
    g, calls = game
    asyncio.run(g.start())
    for i in range(TARGET - 1):
        assert "tenue" in g.on_user_message(f"réponse {i}")
    win = g.on_user_message("bien sûr")
    assert "GAGNÉ" in win and "niveau 2" in win and g.history("Mila")["difficulty"] == 2
    asyncio.run(g.start())
    assert calls[-1]["difficulty"] == 2
    g.on_user_message("oui")  # lost at once: back down
    assert g.history("Mila")["difficulty"] == 1


def test_stop_keeps_the_result_and_days(game):
    g, _ = game
    asyncio.run(g.start())
    for _ in range(3):
        g.on_user_message("hmm")
    g.stop()
    h = g.history("Mila")
    assert h["games_played"] == 1 and h["best"] == 3 and h["days_in_a_row"] == 1 and g.round is None
    g.stop()  # nothing in progress: no-op


def test_keeper_adds_the_verdict_to_the_label(tmp_path):
    from pipecat.processors.aggregators.llm_context import LLMContext

    class FakeGame:
        def on_user_message(self, text):
            return f"verdict pour « {text} »"

        def stop(self):
            self.stopped = True

    k = Conversation(Memory(tmp_path), {"memory": {}}, LLMContext(), "BASE")
    fake = FakeGame()
    k.games = [fake]
    k.context.add_message({"role": "user", "content": "Peut-être"})
    k.tag_last_user_message()
    assert "verdict pour « Peut-être »⟧ Peut-être" in k.context.get_messages()[-1]["content"]
    k.tag_last_user_message()  # already labelled: not refereed twice
    assert k.context.get_messages()[-1]["content"].count("verdict") == 1
    k.end_activity("test")
    assert fake.stopped


# ---- « devine à quoi je pense »: question count kept by code ------------------------------
def test_guess_counts_questions_warns_and_stops(tmp_path, monkeypatch):
    from aipet.games import guess
    from aipet.games.guess import GuessGame

    monkeypatch.setattr(guess, "now", lambda: datetime(2026, 11, 12, 17, 0, tzinfo=TZ))
    g = GuessGame(Memory(tmp_path).root, Keeper("Noam"), max_questions=8)
    assert g.on_user_message("oui") is None  # no round yet
    start = g.start()
    assert start["max_questions"] == 8 and start["history"] == {"games_played": 0}
    hints = [g.on_user_message("oui") for _ in range(8)]
    assert hints[0] == "devine : question 1 sur 8"
    assert "plus que 5" in hints[2] and "DERNIÈRE" in hints[7] and "langue au chat" in hints[7]
    assert "appelle guess_end" in g.on_user_message("c'était un pingouin")  # the reveal, not a question
    assert g.on_user_message("hihi") is None  # nothing more to count
    end = g.end(found=False, answer="un pingouin")
    assert end["questions"] == 8 and end["score"] == "Toi 0 – Noam 1"
    g.start()
    for _ in range(3):
        g.on_user_message("non")
    g.host.last_pet_line = lambda: "Je crois que c'est une girafe !"
    g.on_user_message("oui")
    g.end(found=True, answer="une girafe")
    h = g.history("Noam")
    assert h["pet_found"] == 1 and h["child_won"] == 1 and h["fewest_questions_to_find"] == 4
    assert h["recent_answers"] == ["un pingouin", "une girafe"]
    g.start()
    g.stop()
    assert g.round is None and g.history("Noam")["games_played"] == 2  # stopped rounds are not recorded


def test_guess_records_the_real_answer_even_if_ended_too_early(tmp_path, monkeypatch):
    from aipet.games import guess
    from aipet.games.guess import GuessGame

    monkeypatch.setattr(guess, "now", lambda: datetime(2026, 11, 12, 17, 0, tzinfo=TZ))
    g = GuessGame(Memory(tmp_path).root, Keeper("Mila"), max_questions=3)
    g.start()
    for _ in range(3):
        g.on_user_message("non")
    early = g.end(found=False, answer="un dauphin")  # Flocon's own guess, before the reveal
    assert "Attends" in early["note"] and g.round is not None
    hint = g.on_user_message("Un ornithorynque !")
    assert "enregistrée" in hint and g.round is None
    assert g.history("Mila")["recent_answers"] == ["Un ornithorynque !"]
    # Normal order: reveal first, then guess_end (answer taken from the reveal if missing).
    g.start()
    for _ in range(3):
        g.on_user_message("non")
    assert "appelle guess_end" in g.on_user_message("Une licorne")
    g.end(found=False, answer="")
    assert g.history("Mila")["recent_answers"][-1] == "Une licorne"


def test_guess_giving_up_early_never_records_a_made_up_answer(tmp_path, monkeypatch):
    """Seen in a real session: the pet gave up after 2 questions and recorded « un fennec », an answer
    the child never gave (it was a cat)."""
    from aipet.games import guess
    from aipet.games.guess import GuessGame

    monkeypatch.setattr(guess, "now", lambda: datetime(2026, 11, 13, 19, 0, tzinfo=TZ))
    g = GuessGame(Memory(tmp_path).root, Keeper("Mila"))
    g.start()
    g.on_user_message("L'histoire que j'ai citée.")
    g.on_user_message("Petit.")
    early = g.end(found=False, answer="un fennec")
    assert "Attends" in early["note"] and g.history("Mila")["games_played"] == 0
    assert "enregistrée" in g.on_user_message("J'ai pensé au chat.")
    assert g.history("Mila")["recent_answers"] == ["J'ai pensé au chat."]
    # Giving up early right after the child said it: recorded as given.
    g.start()
    g.on_user_message("non")
    g.on_user_message("C'était un hérisson")
    g.end(found=False, answer="un hérisson")
    assert g.round is None and g.history("Mila")["recent_answers"][-1] == "un hérisson"


def test_guess_win_needs_a_proposal_and_a_yes(tmp_path, monkeypatch):
    from aipet.games import guess
    from aipet.games.guess import GuessGame, confirms_guess

    assert confirms_guess("un dauphin", "Je crois que… c'est un dauphin !", "Oui !")
    assert confirms_guess("une souris", "Est-ce que c'est une souris ?", "Bravo, t'as trouvé")
    assert not confirms_guess("petit chat", "Est-ce que cet animal vit dans la forêt ?", "à trouver")
    assert not confirms_guess("un chat", "Est-ce que c'est un chat ?", "Non.")
    assert not confirms_guess("un chat", "C'est un petit animal ?", "oui")  # vague words do not count

    class Talking(Keeper):
        said = ""

        def last_pet_line(self):
            return self.said

    monkeypatch.setattr(guess, "now", lambda: datetime(2026, 11, 12, 17, 0, tzinfo=TZ))
    k = Talking("Mila")
    g = GuessGame(Memory(tmp_path).root, k)
    g.start()
    k.said = "Est-ce que cet animal vit dans la forêt ?"
    g.on_user_message("à trouver")  # misheard: not a confirmation
    refused = g.end(found=True, answer="petit chat")
    assert "error" in refused and g.round is not None and g.history("Mila")["games_played"] == 0
    k.said = "Je sens que… c'est un chat !"
    g.on_user_message("Ouiii")
    assert g.end(found=True, answer="un chat")["score"] == "Toi 1 – Mila 0"
