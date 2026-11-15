"""Emotion tags: never spoken, however the text is split."""

import asyncio
import random

import pytest
from pipecat.frames.frames import (
    AggregatedTextFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.utils.text.base_text_aggregator import TextType

from aipet import emotions
from aipet.emotions import EmotionEmitter, EmotionFrame, EmotionTagFilter, TagStripper, normalize


def strip(chunks):
    s = TagStripper()
    out = []
    for c in chunks:
        out += s.feed(c)
    out += s.flush()
    text = "".join(v for k, v in out if k == "text")
    return text, [v for k, v in out if k == "emotion"]


def test_tag_at_start_of_sentence():
    assert strip(["[joie] Bravo !"]) == ("Bravo !", ["joie"])


def test_no_tag_nothing_changes():
    assert strip(["Bonjour, ", "ça va ?"]) == ("Bonjour, ça va ?", [])


def test_several_tags_and_clean_spacing():
    text, emos = strip(["[surprise] Oh ! [curieux] Tu as vu ça ? Et [rire]là, hi hi."])
    assert text == "Oh ! Tu as vu ça ? Et là, hi hi."
    assert emos == ["surprise", "curieux", "rire"]


SAMPLE = "[joie] Miaou, bonjour ! [curieux] Tu veux une histoire ? [calin] Ronron."


def test_every_split_in_two():
    for i in range(len(SAMPLE) + 1):
        assert strip([SAMPLE[:i], SAMPLE[i:]]) == (
            "Miaou, bonjour ! Tu veux une histoire ? Ronron.",
            ["joie", "curieux", "calin"],
        ), i


def test_char_by_char_and_random_splits():
    expected = ("Miaou, bonjour ! Tu veux une histoire ? Ronron.", ["joie", "curieux", "calin"])
    assert strip(list(SAMPLE)) == expected
    rnd = random.Random(7)
    for _ in range(300):
        cuts = sorted(rnd.sample(range(len(SAMPLE)), rnd.randint(0, 12)))
        parts = [SAMPLE[a:b] for a, b in zip([0, *cuts], [*cuts, len(SAMPLE)], strict=True)]
        assert strip(parts) == expected, parts


def test_accents_capitals_and_variants():
    assert normalize("Câlin") == "calin"
    assert normalize("JOYEUX") == "joie"
    assert normalize("malicieux") == "malice"
    assert normalize("colere") is None
    assert strip(["[Câlin] Ronron."]) == ("Ronron.", ["calin"])


def test_unknown_tag_removed_without_emotion():
    assert strip(["[soupir] Bon."]) == ("Bon.", [])


def test_brackets_that_are_not_tags():
    assert strip(["Regarde [1, 2] et [ ça."]) == ("Regarde [1, 2] et [ ça.", [])
    assert strip(["a [", "] b"]) == ("a [] b", [])
    long = "[" + "x" * 40 + "]"
    assert strip([long]) == (long, [])
    assert strip(["[[joie] Oui"]) == ("[ Oui", ["joie"])


def test_unfinished_tag_at_end_of_reply_not_spoken():
    assert strip(["Fini. [jo"]) == ("Fini. ", [])


def test_normal_text_is_never_held():
    s = TagStripper()
    assert s.feed("Bonjour toi") == [("text", "Bonjour toi")]
    assert s.held == ""
    assert s.feed(" et [") == [("text", " et ")]
    assert len(s.held) == 1
    # At worst, "[" + MAX_TAG letters are held before deciding.
    out = s.feed("x" * (emotions.MAX_TAG + 1))
    assert out and s.held == ""


def test_instructions_list_exactly_the_tags():
    txt = emotions.tag_instructions()
    for k in emotions.EMOTIONS:
        assert f"[{k}]" in txt
    assert all(v in emotions.EMOTIONS for v in emotions.ALIASES.values())


# ---------------- processors ----------------
def run_filter(frames):
    f = EmotionTagFilter()
    pushed = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    f.push_frame = push

    async def run():
        for fr in frames:
            await f.process_frame(fr, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return pushed


def summary(pushed):
    out = []
    for p in pushed:
        if isinstance(p, EmotionFrame):
            out.append(("emo", p.emotion))
        elif isinstance(p, LLMTextFrame):
            out.append(("txt", p.text))
        else:
            out.append((type(p).__name__,))
    return out


def test_filter_frame_without_tag_passes_unchanged():
    fr = LLMTextFrame("Bonjour")
    assert run_filter([fr]) == [fr]


def test_filter_tag_split_across_frames_and_emotion_offset():
    pushed = run_filter(
        [
            LLMFullResponseStartFrame(),
            LLMTextFrame("[jo"),
            LLMTextFrame("ie] Coucou"),
            LLMTextFrame(" toi ! ["),
            LLMTextFrame("curieux]"),
            LLMTextFrame(" "),
            LLMTextFrame("Ça va ?"),
            LLMFullResponseEndFrame(),
        ]
    )
    assert summary(pushed) == [
        ("LLMFullResponseStartFrame",),
        ("emo", "joie"),
        ("txt", "Coucou"),
        ("txt", " toi ! "),
        ("emo", "curieux"),
        ("txt", "Ça va ?"),
        ("LLMFullResponseEndFrame",),
    ]
    emos = [p for p in pushed if isinstance(p, EmotionFrame)]
    assert [(e.response, e.offset) for e in emos] == [(1, 0), (1, len("Coucoutoi!"))]
    assert pushed[0].metadata[emotions.RESP_KEY] == 1
    assert all("[" not in p.text for p in pushed if isinstance(p, LLMTextFrame))


def test_filter_copies_frame_attributes():
    src = LLMTextFrame("[joie] Oui")
    src.skip_tts = True
    src.append_to_context = False
    out = [p for p in run_filter([src]) if isinstance(p, LLMTextFrame)]
    assert out[0].text == "Oui" and out[0].skip_tts is True and out[0].append_to_context is False
    assert out[0].includes_inter_frame_spaces is True


def test_filter_final_tag():
    assert summary(run_filter([LLMTextFrame("Bonne nuit. [calin]"), LLMFullResponseEndFrame()])) == [
        ("txt", "Bonne nuit. "),
        ("emo", "calin"),
        ("LLMFullResponseEndFrame",),
    ]


# ---------------- in a real Pipecat pipeline ----------------
from pipecat.frames.frames import TTSAudioRawFrame, TTSTextFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.services.tts_service import TTSService  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402


def test_interruption_forgets_the_tag_in_progress():
    async def run():
        down, _ = await run_test(
            EmotionTagFilter(),
            frames_to_send=[
                LLMTextFrame("Oh [surpri"),
                SleepFrame(0.05),
                InterruptionFrame(),
                SleepFrame(0.05),
                LLMTextFrame("se] Salut"),
                LLMTextFrame("[rire]"),
                SleepFrame(0.05),
                InterruptionFrame(),
                SleepFrame(0.05),
                LLMTextFrame("Fin"),
            ],
        )
        return down

    got = [x for x in summary(asyncio.run(run())) if x[0] in ("txt", "emo")]
    # "[surpri" is forgotten on interruption, so "se]" is no longer a tag.
    assert got == [("txt", "Oh "), ("txt", "se] Salut"), ("emo", "rire"), ("txt", "Fin")]


def test_emitter_forgets_emotions_of_an_interrupted_reply():
    async def run():
        down, _ = await run_test(
            EmotionEmitter(),
            frames_to_send=[
                EmotionFrame(emotion="joie", response=1, offset=0),
                EmotionFrame(emotion="rire", response=1, offset=40),
                start(1),
                sentence("Coucou !"),
                SleepFrame(0.05),
                InterruptionFrame(),
                SleepFrame(0.05),
                start(2),
                sentence("Une phrase bien assez longue pour dépasser quarante lettres, voilà."),
                LLMFullResponseEndFrame(),
            ],
        )
        return down

    down = asyncio.run(run())
    assert [f.data["emotion"] for f in down if isinstance(f, RTVIServerMessageFrame)] == ["joie"]


class FakeTTS(TTSService):
    """Fake TTS: one small audio chunk per sentence."""

    def __init__(self):
        super().__init__(sample_rate=16000, push_text_frames=True, push_stop_frames=True)
        self.spoken = []

    def can_generate_metrics(self):
        return False

    async def run_tts(self, text, context_id):
        self.spoken.append(text)
        yield TTSAudioRawFrame(audio=b"\0" * 320, sample_rate=16000, num_channels=1, context_id=context_id)


def test_full_pipeline_tts_never_gets_a_tag_and_emotion_follows_its_sentence():
    tts = FakeTTS()
    seen = []

    async def on_emotion(e):
        seen.append(e)

    async def run():
        down, _ = await run_test(
            Pipeline([EmotionTagFilter(), tts, EmotionEmitter(on_emotion=on_emotion)]),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame("[jo"),
                LLMTextFrame("ie] Bonjour toi !"),
                LLMTextFrame(" [cur"),
                LLMTextFrame("ieux] Tu veux"),
                LLMTextFrame(" jouer ?"),
                LLMFullResponseEndFrame(),
                SleepFrame(0.2),
                InterruptionFrame(),
                SleepFrame(0.05),
                LLMFullResponseStartFrame(),
                LLMTextFrame("Et voilà. [calin] Ronron."),
                LLMFullResponseEndFrame(),
                SleepFrame(0.3),
            ],
        )
        return down

    down = asyncio.run(run())
    assert tts.spoken == ["Bonjour toi !", "Tu veux jouer ?", "Et voilà.", "Ronron."]
    assert seen == ["joie", "curieux", "calin"]
    # Output order: emotion, its sentence's audio, next emotion, next sentence's audio.
    order = []
    for f in down:
        if isinstance(f, RTVIServerMessageFrame):
            order.append(f.data["emotion"])
        elif isinstance(f, TTSAudioRawFrame):
            order.append("audio")
    assert order == ["joie", "audio", "curieux", "audio", "audio", "calin", "audio"], order
    spoken_ctx = "".join(f.text for f in down if isinstance(f, TTSTextFrame))
    assert "[" not in spoken_ctx and "joie" not in spoken_ctx


def run_emitter(frames, on_emotion=None):
    e = EmotionEmitter(on_emotion=on_emotion)
    pushed = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    e.push_frame = push

    async def run():
        for fr in frames:
            await e.process_frame(fr, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return pushed


def sentence(text):
    return AggregatedTextFrame(text, TextType.SENTENCE)


def start(n):
    f = LLMFullResponseStartFrame()
    f.metadata[emotions.RESP_KEY] = n
    return f


def shown(pushed):
    return [
        f.data["emotion"] if isinstance(f, RTVIServerMessageFrame) else getattr(f, "text", type(f).__name__)
        for f in pushed
        if not isinstance(f, LLMFullResponseStartFrame)
    ]


def test_emitter_waits_for_the_matching_sentence():
    seen = []

    async def on_emotion(e):
        seen.append(e)
        raise RuntimeError("screen unplugged")  # must not break the pipeline

    pushed = run_emitter(
        [
            # The model wrote everything ahead: the three emotions arrive before any audio.
            EmotionFrame(emotion="joie", response=1, offset=0),
            EmotionFrame(emotion="curieux", response=1, offset=11),
            EmotionFrame(emotion="calin", response=1, offset=24),
            start(1),
            sentence("Bonjour toi !"),
            sentence("Tu veux jouer ?"),
            LLMFullResponseEndFrame(),
        ],
        on_emotion,
    )
    assert shown(pushed) == ["joie", "Bonjour toi !", "curieux", "Tu veux jouer ?", "calin", "LLMFullResponseEndFrame"]
    assert seen == ["joie", "curieux", "calin"]
    assert pushed[1].data == {"type": "emotion", "emotion": "joie"}
    assert not any(isinstance(f, EmotionFrame) for f in pushed)


def test_emitter_does_not_mix_two_replies():
    # Reply 2 (after a tool call) already written while reply 1 is still being spoken.
    pushed = run_emitter(
        [
            EmotionFrame(emotion="pensif", response=1, offset=0),
            start(1),
            EmotionFrame(emotion="joie", response=2, offset=0),
            sentence("Attends, je cherche."),
            LLMFullResponseEndFrame(),
            start(2),
            sentence("Trouvé !"),
            LLMFullResponseEndFrame(),
        ]
    )
    assert shown(pushed) == [
        "pensif",
        "Attends, je cherche.",
        "LLMFullResponseEndFrame",
        "joie",
        "Trouvé !",
        "LLMFullResponseEndFrame",
    ]


def test_system_prompt_contains_tags_and_guardrails():
    from aipet import config, prompts

    p = prompts.build_system_prompt(config.load_config())
    assert "[joie]" in p and "Garde-fous" in p and "ne prétends pas être vivant" in p
    assert "TOI qui la termines" in p  # the pet's own goodbyes end the conversation too


def test_control_tags_never_spoken():
    s = TagStripper()
    out = []
    for c in ["[parle:Mi", "la] [joie] Coucou ! ", "[silence]"]:
        out += s.feed(c)
    assert "".join(v for k, v in out if k == "text") == "Coucou ! "
    assert [v for k, v in out if k == "tag"] == ["parle:Mila", "silence"]
    assert [v for k, v in out if k == "emotion"] == ["joie"]


def test_a_first_name_with_a_space_is_not_spoken():
    s = TagStripper()
    out = []
    for c in ["[parle:Marie ", "Lou] Coucou [parle:Zoé] ! [1, 2]"]:
        out += s.feed(c)
    assert "".join(v for k, v in out if k == "text") == "Coucou ! [1, 2]"
    assert [v for k, v in out if k == "tag"] == ["parle:Marie Lou", "parle:Zoé"]


def test_filter_forwards_control_tags():
    received = []

    async def on_tag(t):
        received.append(t)

    async def run():
        f = EmotionTagFilter(on_tag=on_tag)
        pushed = []

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        f.push_frame = push
        await EmotionTagFilter.process_frame(f, LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await EmotionTagFilter.process_frame(f, LLMTextFrame("[silence]"), FrameDirection.DOWNSTREAM)
        return pushed

    import pipecat.processors.frame_processor as fp

    orig = fp.FrameProcessor.process_frame

    async def noop(self, frame, direction):
        return None

    fp.FrameProcessor.process_frame = noop
    try:
        pushed = asyncio.run(run())
    finally:
        fp.FrameProcessor.process_frame = orig
    assert received == ["silence"]
    assert not [x for x in pushed if isinstance(x, LLMTextFrame)]


@pytest.mark.parametrize("tag", ["attends", "silence"])
def test_filter_mutes_the_rest_of_the_reply_after_a_muting_tag(tag):
    received = []

    async def on_tag(t):
        received.append(t)

    async def run():
        f = EmotionTagFilter(on_tag=on_tag)
        pushed = []

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        f.push_frame = push

        def go(fr):
            return EmotionTagFilter.process_frame(f, fr, FrameDirection.DOWNSTREAM)

        await go(LLMFullResponseStartFrame())
        await go(LLMTextFrame(f"[pensif] [{tag}] Oups, "))
        await go(LLMTextFrame("je parle quand même."))
        await go(LLMFullResponseEndFrame())
        muted = [x.text for x in pushed if isinstance(x, LLMTextFrame)]
        await go(LLMFullResponseStartFrame())  # next reply: speaking again
        await go(LLMTextFrame("[joie] Coucou !"))
        return muted, [x.text for x in pushed if isinstance(x, LLMTextFrame)]

    import pipecat.processors.frame_processor as fp

    orig = fp.FrameProcessor.process_frame

    async def noop(self, frame, direction):
        return None

    fp.FrameProcessor.process_frame = noop
    try:
        muted, after = asyncio.run(run())
    finally:
        fp.FrameProcessor.process_frame = orig
    assert received == [tag] and muted == []
    assert "".join(after) == "Coucou !"


def test_goodbye_tag_is_never_spoken():
    s = TagStripper()
    out = []
    for c in ["[parle:Mila] [joie] [f", "in] À bientôt, Mila !"]:
        out += s.feed(c)
    assert "".join(v for k, v in out if k == "text").strip() == "À bientôt, Mila !"
    assert [v for k, v in out if k == "tag"] == ["parle:Mila", "fin"]
