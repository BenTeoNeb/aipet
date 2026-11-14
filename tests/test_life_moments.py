"""« Ma vie » moments: the code suggests, once per conversation and at a calm moment, that the pet
asks a child to choose something for it or to feed its collection (no network)."""

from pipecat.processors.aggregators.llm_context import LLMContext

from aipet import character
from aipet.conversation import Conversation
from aipet.memory import Memory
from aipet.memory.life_moments import life_question, still_undecided
from aipet.memory.store import LIFE_CHOSEN, LIFE_WORDS

FLOCON = character.load("flocon")


def test_already_chosen_is_no_longer_asked():
    chosen = ["Couleur préférée : vert pomme (choisi par Mila, le 2026-11-14)", "nom de la pelote : Bobine"]
    left = still_undecided(FLOCON.undecided, chosen)
    assert "ta couleur préférée" not in left and "le nom de ta pelote rouge" not in left
    assert left[0] == "ta chanson préférée" and "ton jeu préféré" in left  # « préféré » alone isn't enough


def test_choices_and_collection_take_turns():
    ask = life_question("Mila", list(FLOCON.undecided), FLOCON.collection_ask, {})
    assert "demande à Mila de choisir pour toi ta couleur préférée" in ask
    one_choice = {LIFE_CHOSEN: ["couleur préférée : bleu"], LIFE_WORDS: []}
    assert "t'apprendre un mot" in life_question("Mila", list(FLOCON.undecided), FLOCON.collection_ask, one_choice)
    everything = {LIFE_CHOSEN: [f"{u} : x" for u in FLOCON.undecided], LIFE_WORDS: ["arc-en-ciel"] * 9}
    assert "t'apprendre un mot" in life_question("Mila", list(FLOCON.undecided), FLOCON.collection_ask, everything)
    assert life_question("Mila", [], "", {}) == ""


def keeper(tmp_path):
    k = Conversation(Memory(tmp_path, "Flocon"), {"memory": {}}, LLMContext(), "BASE")
    k.who.current, k.who.certainty = "Mila", "sure"
    return k


def say(k, text):
    k.context.add_message({"role": "user", "content": text})
    k.tag_last_user_message()
    return k.context.get_messages()[-1]["content"]


def test_once_per_conversation_after_a_few_messages(tmp_path):
    k = keeper(tmp_path)
    labels = [say(k, f"message {i}") for i in range(6)]
    assert ["ma vie" in label for label in labels] == [False, False, True, False, False, False]
    assert "demande à Mila de choisir pour toi ta couleur préférée" in labels[2]
    k.reset_context()  # a new conversation: once again
    assert ["ma vie" in say(k, f"encore {i}") for i in range(3)] == [False, False, True]


def test_not_during_an_activity_nor_for_an_unsure_child(tmp_path):
    k = keeper(tmp_path)

    class Busy:
        active = "story"

    k.skills = Busy()
    assert not any("ma vie" in say(k, f"m{i}") for i in range(4))
    Busy.active = None
    k.who.certainty = "probable"
    assert "ma vie" not in say(k, "m5")
    k.who.certainty = "sure"
    assert "ma vie" in say(k, "m6")  # calm again: now
