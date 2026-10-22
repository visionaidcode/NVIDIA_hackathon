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
