"""The atlas (every reachable state, how far a win is) agrees with the searches it shadows: on the
bank's small adventures along random walks, on a lucky guess that strands the hero, and off the
atlas after a destroy."""

import json
import random
from pathlib import Path

import pytest

from aipet.games.adventure_engine import World, _moves, solve
from aipet.games.adventure_patterns import act, adventure, person, place, thing
from storyworld.atlas import Atlas

BANK = sorted(Path("aipet/data/adventures").glob("*.json"))
SMALL = 2_500  # states: past this, the shadow in play compares (a bigger atlas takes seconds)


def _agrees(w: World, atlas: Atlas, st) -> None:
    d = atlas.distance(st)
    found = solve(w, st, first_path=True)
    assert found["complete"] or found.get("path")
    if found.get("path"):
        assert 0 <= d <= len(found["path"]), (st.place, d, len(found["path"]))
    else:
        assert d in (-1, 0)


@pytest.mark.parametrize("path", BANK, ids=lambda p: p.stem)
def test_the_atlas_agrees_with_the_search_on_random_walks(path):
    rec = json.loads(path.read_text())
    w = World(rec.get("graph", rec))
    atlas = Atlas(w, max_states=SMALL)
    if not atlas.complete:
        pytest.skip("big adventure")
    assert atlas.distance(w.start()) > 0
    rng = random.Random(path.stem)
    for _ in range(4):
        st = w.start()
        for _ in range(12):
            _agrees(w, atlas, st)
            moves = list(_moves(w, st, reduce=False, fair=False))
            if st.ending or not moves:
                break
            st = rng.choice(moves)[1]


def test_a_lucky_guess_that_strands_the_hero_is_no_win():
    g = adventure(
        [place("a", ("b", "est"), ("d", "nord")), place("b", ("a", "ouest")), place("d", ("a", "sud")), place("c")],
        [thing("dessin", "d", "scenery", description="Un peigne, puis rouge et bleu."),
         thing("coffre", "c", "container", code={"press": ["rouge", "bleu"]}), thing("rouge", "c", "scenery"),
         thing("bleu", "c", "scenery"), thing("tresor", "coffre")],
        [person("sphinx", "b", answer={"question": "J'ai des dents mais je ne mords pas ?", "accept": ["peigne"],
                                       "effects": ["goto:c"]})],
        [act("fin", [("use", "tresor")], ["end:gagne"], ["has:tresor"])],
        puzzles=[{"goal": "sphinx", "action": "sphinx", "needs": [], "clue_in": "dessin"},
                 {"goal": "coffre", "action": "coffre", "needs": [], "clue_in": "dessin"}],
    )  # fmt: skip
    w = World(g)
    atlas = Atlas(w)
    st = w.start()
    assert atlas.distance(st) > 0
    w.act(st, {"verb": "go", "to": "b"})
    w.act(st, {"verb": "answer", "object": "peigne", "target": "sphinx"})  # guessed: sent to c, the clue behind
    assert st.place == "c" and atlas.distance(st) == -1
    _agrees(w, atlas, st)


def test_off_the_atlas_after_a_destroy_and_the_shadow_keeps_what_differs():
    g = adventure(
        [place("a", ("b", "est")), place("b", ("a", "ouest"))],
        [thing("cle1", "a"), thing("cle2", "a")],
        [person("roi", "b")],
        [act("fin1", [("give", "cle1", "roi")], ["end:gagne"], ["has:cle1"]),
         act("fin2", [("give", "cle2", "roi")], ["end:gagne"], ["has:cle2"])],
    )  # fmt: skip
    w = World(g)
    w.atlas = Atlas(w)
    st = w.start()
    w.act(st, {"verb": "take", "object": "cle1"})
    assert w.atlas.distance(st) == 2  # go b, give
    w.act(st, {"verb": "destroy", "object": "cle1"})
    assert w.atlas.distance(st) is None  # the solver never destroys: the search answers
    w.atlas.compare(st, "test", [("go", "b")], True)  # a way shorter than the atlas's: kept
    w.atlas.compare(w.start(), "test", [("take", "cle1")] * 3, True)
    assert [d["where"] for d in w.atlas.disagreements] == []  # off the atlas, then agreeing
    w.atlas.compare(w.start(), "test", [], True)  # « no way » where the atlas has one
    assert w.atlas.disagreements == [{"where": "test", "place": "a", "atlas": 3, "search": 0}]
