import asyncio

from app.llm.client import StubClient, audio_block
from app.memory.buffer import Frame, Snapshot, TranscriptLine
from app.qa.ask import build_messages, answer_question, select_frames


def frames(n, now=100.0):
    return [Frame(now - (n - i) * 2, f"f{i}".encode(), 1, 1) for i in range(n)]


def test_select_frames_spreads_and_keeps_latest():
    fs = frames(10)
    picked = select_frames(fs, 3)
    assert picked[0] is fs[0] and picked[-1] is fs[-1] and len(picked) == 3
    assert select_frames(fs, 1) == [fs[-1]]
    assert select_frames(fs[:2], 3) == fs[:2]


def test_build_messages_has_rules_transcript_images_and_question():
    snap = Snapshot(100.0, frames(2), [TranscriptLine(90, 95, "look at the home page")])
    msgs = build_messages(snap, "What is shown?", max_frames=3)
    assert "Never invent" in msgs[0]["content"]
    content = msgs[1]["content"]
    assert "[5 s ago] Speaker: look at the home page" in content[0]["text"]
    assert sum(1 for c in content if c["type"] == "image_url") == 2
    assert content[-1]["text"] == "Question: What is shown?"


def test_build_messages_says_when_nothing_captured():
    msgs = build_messages(Snapshot(100.0, [], []), "Anything?")
    texts = " ".join(c.get("text", "") for c in msgs[1]["content"])
    assert "no speech captured" in texts and "no screenshots captured" in texts


def test_answer_streams_first_sentence_with_interrupt_and_stops_at_two():
    client = StubClient(answer="One. Two. Three.", delay=0)
    spoken = []
    result = asyncio.run(answer_question(client, Snapshot(100.0, frames(1), []), "Q?",
                                         speak=lambda t, i: spoken.append((t, i))))
    assert spoken == [("One.", True), ("Two.", False)]
    assert result.text == "One. Two."
    assert result.first_sentence_s is not None


def test_audio_block_formats():
    assert audio_block(b"x", "input_audio")["input_audio"]["format"] == "wav"
    assert audio_block(b"x", "audio_url")["audio_url"]["url"].startswith("data:audio/wav;base64,")


def test_build_messages_puts_earlier_turns_before_the_question():
    snap = Snapshot(100.0, frames(1), [])
    history = [("What happened in the last 30 seconds?", "Last 30 sec: a WhatsApp chat with Ravi was scrolled.")]
    msgs = build_messages(snap, "What did they scroll?", history=history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Question: What happened in the last 30 seconds?"
    assert msgs[2]["content"].startswith("Last 30 sec:")
    assert msgs[-1]["content"][-1]["text"] == "Question: What did they scroll?"


def test_build_messages_without_history_is_unchanged():
    msgs = build_messages(Snapshot(100.0, frames(1), []), "Q?")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_apply_fixes_whole_words_any_case():
    from app.llm.client import apply_fixes

    fixes = (("Vision 18", "VisionAid team"), ("Vision18", "VisionAid team"))
    assert apply_fixes("developed by the vision 18.", fixes) == "developed by the VisionAid team."
    assert apply_fixes("Vision18 says hi", fixes) == "VisionAid team says hi"
    assert apply_fixes("Vision 180 people", fixes) == "Vision 180 people"


def test_meeting_language_is_told_next_to_the_question():
    from app.qa.ask import meeting_language, question_text

    tamil = [TranscriptLine(0, 1, "இப்ப நம்ம பார்க்கப் போறது for loop")]
    assert meeting_language(tamil) == "Tamil"
    assert question_text("What happened?", tamil) == "Question: What happened?\nAnswer in Tamil."
    assert meeting_language([TranscriptLine(0, 1, "for loop prints zero to four")]) is None
    assert meeting_language([]) is None


def test_short_key_word_result_is_checked_again_without_key_words():
    import httpx

    from app.config import Settings
    from app.llm.client import OpenAICompatClient, keyterm_suspect

    assert keyterm_suspect("VisionAid.", ("VisionAid",)) and keyterm_suspect("NVIDIA NVIDIA", ("NVIDIA",))
    assert not keyterm_suspect("Welcome, developed by the VisionAid team today.", ("VisionAid",))
    assert not keyterm_suspect("Print I.", ("VisionAid",))

    sent = []

    def handler(req):
        body = req.read()
        sent.append(b"keyterms" in body)
        return httpx.Response(200, json={"transcript": "VisionAid." if b"keyterms" in body else "Print I."})

    s = Settings(provider="nebius", base_url="https://x/v1", api_key="k", model="m", stt_api="sarvam",
                 stt_base_url="https://api.sarvam.ai", stt_api_key="sk", stt_model="saaras:v4",
                 stt_keyterms=("VisionAid",))
    c = OpenAICompatClient(s)
    c._sarvam = httpx.AsyncClient(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler))
    assert asyncio.run(c.transcribe(b"wav")) == "Print I." and sent == [True, False]
