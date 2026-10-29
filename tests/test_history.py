import asyncio

from app.llm.client import StubClient
from app.memory.history import PageNoter, ScreenHistory, clean_note


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


def test_listeners_hear_new_and_seen_screens():
    h = ScreenHistory()
    new, seen = [], []
    h.on_new, h.on_seen = new.append, seen.append
    h.add(1, b"a", 1, 1)
    h.add(2, b"", 0, 0, unchanged=True)
    h.add(3, b"b", 1, 1)
    assert [s.number for s in new] == [1, 2] and [(s.number, s.last_ts) for s in seen] == [(1, 2)]
    assert h.get(2).jpeg == b"b" and h.get(9) is None
