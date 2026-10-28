import asyncio

from app.llm.client import StubClient
from app.memory.buffer import TranscriptLine
from app.memory.history import PageNoter, ScreenHistory, Shot, clean_note
from app.qa.review import (answer_from_recording, build_review_messages, expand_picks, group_shots,
                           parse_picks, transcript_for)


def history_with(spans):
    """spans = [(first_ts, last_ts), ...] -> a history with one screen per span."""
    h = ScreenHistory()
    h.clear(100.0)
    for a, b in spans:
        h.add(a, b"jpeg", 10, 10)
        if b > a:
            h.add(b, b"", 0, 0, unchanged=True)
    return h


def test_unchanged_screen_only_moves_its_time():
    h = history_with([(100, 160)])
    (s,) = h.shots()
    assert (s.number, s.first_ts, s.last_ts, s.seconds) == (1, 100, 160, 60)


def test_new_recording_clears_history_and_numbers():
    h = history_with([(100, 101), (102, 103)])
    h.clear(200.0)
    h.add(200, b"x", 1, 1)
    assert [s.number for s in h.shots()] == [1] and h.started == 200.0


def test_cap_drops_quick_unnoted_screens_first():
    h = ScreenHistory(max_shots=3)
    h.add(0, b"a", 1, 1); h.add(10, b"", 0, 0, unchanged=True)   # long screen, kept
    h.add(11, b"b", 1, 1)                                          # quick, dropped first
    h.add(12, b"c", 1, 1); h.add(13, b"d", 1, 1)
    assert [s.number for s in h.shots()] == [1, 3, 4]


def test_noter_notes_only_screens_shown_long_enough():
    h = history_with([(100, 110), (111, 112)])
    notes = []
    asyncio.run(PageNoter(StubClient(delay=0), h, on_note=notes.append).finish(5))
    first, second = h.shots()
    assert first.note.startswith("Slide:") and not second.note and notes == [first]


def test_clean_note_drops_think_and_extra_space():
    assert clean_note("<think>hmm</think>  Slide:\n  Launch plan ") == "Slide: Launch plan"


def test_groups_merge_quick_unnoted_screens():
    shots = [Shot(1, 0, 5, b"", 1, 1, note="Slide A"), Shot(2, 6, 6, b"", 1, 1),
             Shot(3, 7, 7, b"", 1, 1), Shot(4, 8, 20, b"", 1, 1, note="Code")]
    assert [len(g.shots) for g in group_shots(shots)] == [1, 2, 1]


def test_parse_picks_keeps_valid_unique_numbers():
    assert parse_picks("<think>2?</think> 3, 1, 3, 9", 4) == [3, 1]


def test_expand_picks_time_order_and_fallback():
    shots = [Shot(i, i * 10, i * 10 + 5, b"", 1, 1, note=f"n{i}") for i in range(1, 7)]
    groups = group_shots(shots)
    assert [s.number for s in expand_picks(groups, [5, 2], 4)] == [2, 5]
    assert len(expand_picks(groups, [], 3)) == 3  # nothing picked -> spread over the recording


def test_transcript_for_keeps_lines_near_chosen_screens_when_long():
    lines = [TranscriptLine(t, t + 1, "x" * 50) for t in range(0, 3000, 10)]
    near = transcript_for(lines, [Shot(1, 1000, 1010, b"", 1, 1)], max_chars=2000, pad=60)
    assert near and all(930 <= l.start <= 1070 for l in near)
    assert transcript_for(lines[:3], [], max_chars=2000) == lines[:3]


def test_review_messages_have_notes_transcript_screenshots_and_question():
    shots = [Shot(1, 100, 130, b"img", 1, 1, note="Slide: Yesterday's status")]
    msgs = build_review_messages(group_shots(shots), shots, [TranscriptLine(101, 103, "we left off here", "Priya")],
                                 "What was on that page?", 100.0)
    text = " ".join(c.get("text", "") for c in msgs[-1]["content"])
    assert "Yesterday's status" in text and "Priya: we left off here" in text
    assert "(from 0:00 to 0:30)" in text and text.endswith("Question: What was on that page?")
    assert sum(1 for c in msgs[-1]["content"] if c["type"] == "image_url") == 1


def test_answer_from_recording_allows_five_sentences():
    shots = [Shot(1, 100, 130, b"img", 1, 1, note="Slide")]
    client = StubClient(answer="One. Two. Three. Four. Five. Six.", delay=0)
    result = asyncio.run(answer_from_recording(client, shots, [], "Q?", 100.0))
    assert result.text == "One. Two. Three. Four. Five."
