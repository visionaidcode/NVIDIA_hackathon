import asyncio
import io
import json
import time

from PIL import Image, ImageDraw

from app.config import Settings
from app.llm.client import StubClient
from app.memory.buffer import TranscriptLine
from app.memory.history import Shot
from app.qa.review import (answer_from_recording, build_recording_messages, fit_screens, screens_in,
                           transcript_for)
from app.rag.index import PART_SECONDS, MeetingIndexer, window_text
from app.rag.search import keyword_scores, rank_parts
from app.rag.store import MeetingStore, Part, ScreenRecord, list_meetings, meeting_label

T0 = time.mktime((2026, 10, 29, 10, 40, 0, 0, 0, -1))  # 10:40:00 local time


def jpeg(text: str, shade: int = 255) -> bytes:
    img = Image.new("RGB", (320, 180), (shade, shade, shade))
    ImageDraw.Draw(img).text((10, 10), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def one_hour_meeting(tmp_path) -> MeetingStore:
    """60 minutes; minute 25 is about Q3 sales on a slide, minute 50 about the launch date."""
    store = MeetingStore(tmp_path / "Meeting", T0)
    for m in range(60):
        t = T0 + m * 60
        text = {25: "Q3 sales are 12 lakh, up from 9 lakh", 50: "the launch moves to Friday 7 November"}.get(
            m, f"we discussed routine item {m} and the weekly status " + "in detail " * 25)
        store.add_line(TranscriptLine(t + 5, t + 9, text, "Priya" if m == 25 else "Ravi"))
        shot = Shot(m + 1, t + 2, t + 58, jpeg(f"slide {m}", 255 - m * 3), 320, 180)
        store.add_screen(shot)
        store.screen_seen(shot)
        store.set_note(m + 1, "Slide: Q3 sales by region" if m == 25 else f"Slide {m}: agenda")
    return store


def test_screens_are_saved_named_by_time_and_meeting_json_round_trips(tmp_path):
    store = MeetingStore(tmp_path / "Meeting", T0)
    store.add_screen(Shot(1, T0 + 12, T0 + 12, b"one", 1, 1))
    store.add_screen(Shot(2, T0 + 12.5, T0 + 13, b"two", 1, 1))  # same second
    store.screen_seen(Shot(2, T0 + 12.5, T0 + 40, b"", 1, 1))
    store.set_note(2, "Slide: budget")
    store.add_line(TranscriptLine(T0 + 1, T0 + 3, "hello", "Priya"))
    store.parts.append(Part(T0, T0 + 60, "Priya: hello", [0.123456789, 1.0]))
    store.save()
    assert sorted(p.name for p in (store.folder / "screens").iterdir()) == ["10-40-12.jpg", "10-40-12_2.jpg"]

    loaded = MeetingStore.load_latest(tmp_path)
    assert loaded.started == T0 and loaded.lines == store.lines
    second = loaded.screens[1]
    assert (second.file, second.last_ts, second.note) == ("10-40-12_2.jpg", T0 + 40, "Slide: budget")
    assert loaded.jpeg(second) == b"two" and loaded.parts[0].embedding == [0.12346, 1.0]
    json.loads((store.folder / "meeting.json").read_text(encoding="utf-8"))  # plain JSON


def test_screenshots_off_keeps_them_in_memory_only(tmp_path):
    store = MeetingStore(tmp_path / "Meeting", T0, save_screenshots=False)
    store.memory = {1: b"mem"}.get
    store.add_screen(Shot(1, T0, T0, b"mem", 1, 1))
    assert not (store.folder / "screens").exists() and store.jpeg(store.screens[0]) == b"mem"


def test_indexer_builds_minute_parts_and_summaries_and_embeds(tmp_path):
    store = MeetingStore(tmp_path / "Meeting", T0)
    store.add_line(TranscriptLine(T0 + 10, T0 + 12, "budget is fine", "Priya"))
    store.add_line(TranscriptLine(T0 + 70, T0 + 72, "next slide", "Ravi"))
    store.add_screen(Shot(1, T0, T0 + 100, b"", 1, 1))
    store.set_note(1, "Slide: Budget 2026")
    now = [T0 + 100]
    written = []
    ix = MeetingIndexer(StubClient(delay=0), store, summary_seconds=150, on_summary=written.append,
                        clock=lambda: now[0])

    asyncio.run(ix.update())  # 80 s settled: only the first minute is complete
    assert len(store.parts) == 1 and store.parts[0].embedding
    assert store.parts[0].text == "Priya: budget is fine\nScreen: Slide: Budget 2026"
    assert store.summaries == []

    asyncio.run(ix.update(final=True))  # stop: the last part and summary are finished
    assert [p.start - T0 for p in store.parts] == [0, PART_SECONDS]
    assert "Ravi: next slide" in store.parts[1].text and store.parts[1].embedding
    assert len(store.summaries) == 1 and store.summaries[0].text.startswith("Summary:")
    assert written == store.summaries
    assert MeetingStore.load(store.folder).summaries == store.summaries


def test_late_line_re_embeds_its_part(tmp_path):
    store = MeetingStore(tmp_path / "Meeting", T0)
    store.add_line(TranscriptLine(T0 + 10, T0 + 12, "first", "A"))
    ix = MeetingIndexer(StubClient(delay=0), store, clock=lambda: T0 + 200)
    asyncio.run(ix.update())
    before = store.parts[0].embedding
    store.add_line(TranscriptLine(T0 + 20, T0 + 22, "late words", "A"))
    asyncio.run(ix.update())
    assert "late words" in store.parts[0].text and store.parts[0].embedding != before


def test_no_embedding_model_still_indexes_for_keywords(tmp_path):
    client = StubClient(delay=0)
    client.embed_model = ""
    store = MeetingStore(tmp_path / "Meeting", T0)
    store.add_line(TranscriptLine(T0 + 10, T0 + 12, "hello", "A"))
    asyncio.run(MeetingIndexer(client, store, clock=lambda: T0 + 200).update())
    assert store.parts[0].text and store.parts[0].embedding is None


def test_window_text_includes_screens_shown_during_it():
    screens = [ScreenRecord(1, 0, 500, note="Slide: long"), ScreenRecord(2, 600, 610, note="later")]
    assert window_text([], screens, 120, 180) == "Screen: Slide: long"


def test_search_finds_the_minute_by_words_and_meaning():
    parts = [Part(i * 60, i * 60 + 60, f"Ravi: routine item {i}") for i in range(30)]
    parts[17].text = "Priya: Q3 sales are 12 lakh\nScreen: Slide: Q3 sales by region"
    assert rank_parts(parts, "What were the Q3 sales?")[0] == 17
    vec = [[1.0, 0.0]] * 30
    vec[4] = [0.0, 1.0]
    for p, v in zip(parts, vec):
        p.embedding = v
    assert rank_parts(parts, "no shared words at all", query_vec=[0.0, 1.0])[0] == 4
    assert keyword_scores(["a b", "c"], "c") == [0.0, keyword_scores(["a b", "c"], "c")[1]]


def test_short_meeting_sends_whole_transcript_long_one_the_found_minutes():
    lines = [TranscriptLine(t, t + 1, "x" * 50) for t in range(0, 3600, 10)]
    near = transcript_for(lines, [Part(1000, 1060)], max_chars=4000, pad=30)
    assert near and all(970 <= l.start <= 1090 for l in near)
    assert transcript_for(lines[:5], [], max_chars=4000) == lines[:5]


def test_fit_screens_keeps_eight_dropping_near_identical_first():
    shots = [(ScreenRecord(i, i, i + 1), jpeg("same page", 255)) for i in range(1, 9)]
    shots += [(ScreenRecord(9, 9, 10, note="Slide: totals"), jpeg("TOTALS 12 lakh", 40)),
              (ScreenRecord(10, 10, 11), jpeg("other page", 120))]
    kept = fit_screens(shots, 8)
    numbers = [r.number for r, _ in kept]
    assert len(kept) == 8 and 9 in numbers and 10 in numbers and numbers == sorted(numbers)


def test_recording_question_sends_summaries_transcript_and_all_found_screens(tmp_path):
    store = one_hour_meeting(tmp_path)
    client = StubClient(answer="Q3 sales were 12 lakh. Priya said so.", delay=0)
    asyncio.run(MeetingIndexer(client, store, clock=lambda: T0 + 3600).update(final=True))
    assert len([s for s in store.summaries if s.text]) == 24  # one every 2.5 minutes

    result = asyncio.run(answer_from_recording(client, store, "What were the Q3 sales?"))
    assert result.text == "Q3 sales were 12 lakh. Priya said so."
    content = client.calls[-1][-1]["content"]
    text = " ".join(c.get("text", "") for c in content)
    images = [c for c in content if c["type"] == "image_url"]
    assert text.count("Summary:") == 24                                # every summary
    assert "Priya: Q3 sales are 12 lakh" in text                       # the found minute's exact words
    assert "routine item 3 " not in text                               # long meeting: not every line
    assert "Screenshot 11:05:02, on screen 11:05:02 to 11:05:58" in text  # exact time of the screen
    assert "Note: Slide: Q3 sales by region" in text
    assert 1 <= len(images) <= 8 and text.endswith("Question: What were the Q3 sales?")


def test_recording_answer_allows_five_sentences(tmp_path):
    store = one_hour_meeting(tmp_path)
    client = StubClient(answer="One. Two. Three. Four. Five. Six.", delay=0)
    assert asyncio.run(answer_from_recording(client, store, "Q?")).text == "One. Two. Three. Four. Five."


def test_messages_list_screens_left_out_as_notes():
    store = MeetingStore("x", T0)
    msgs = build_recording_messages(store, [], [], [ScreenRecord(3, T0 + 5, T0 + 9, note="Slide: extra")], "Q?")
    text = " ".join(c.get("text", "") for c in msgs[-1]["content"])
    assert "Other screens in these minutes (notes only):" in text and "Slide: extra" in text
    assert screens_in([ScreenRecord(1, 0, 500)], [Part(100, 160)])


def test_app_answers_after_restart_from_the_last_meeting(tmp_path):
    from app.main import App

    store = one_hour_meeting(tmp_path)
    store.save()

    class Speaker:
        name = "test"
        spoken: list[str] = []
        def speak(self, text, interrupt=False):
            self.spoken.append(text)
        def stop(self):
            pass

    app = App(Settings(transcript_dir=str(tmp_path), latency_log=""), StubClient(delay=0), Speaker())
    assert app.store and app.store.name == "Meeting"
    assert "meeting of Meeting: 60 lines, 60 screens" in app.status_text()
    asyncio.run(app._answer_recording("What were the Q3 sales?", time.perf_counter()))
    assert app.last_answer and app.history[-1][0] == "What were the Q3 sales?"


def test_meetings_are_listed_newest_first_with_spoken_names(tmp_path):
    import os

    for i, name in enumerate(["Meeting 28-10-2026 09-05 to 09-40", "Meeting 29-10-2026 14-58 to 15-18"]):
        MeetingStore(tmp_path / name, T0).save()
        os.utime(tmp_path / name / "meeting.json", (T0 + i, T0 + i))
    (tmp_path / "Meeting 30-10-2026 10-00").mkdir()  # no meeting.json: nothing to ask about
    assert [f.name for f in list_meetings(tmp_path)] == ["Meeting 29-10-2026 14-58 to 15-18",
                                                         "Meeting 28-10-2026 09-05 to 09-40"]
    assert meeting_label(tmp_path / "Meeting 29-10-2026 14-58 to 15-18") == "29 October 2026, 14:58 to 15:18"
    assert meeting_label("Meeting 29-10-2026 14-58") == "29 October 2026, 14:58"
    assert meeting_label("Meeting 29-10-2026 14-58 to 15-18 (2)") == "29 October 2026, 14:58 to 15:18 (2)"
    assert meeting_label("Meeting 2026-10-29 13-21") == "Meeting 2026-10-29 13-21"  # other names as they are
    assert list_meetings(tmp_path / "missing") == []


def test_choosing_a_meeting_answers_from_it(tmp_path):
    from app.main import App

    one_hour_meeting(tmp_path / "a").save()  # "Meeting" in a/
    other = MeetingStore(tmp_path / "b" / "Meeting 28-10-2026 09-05 to 09-40", T0)
    other.add_line(TranscriptLine(T0 + 1, T0 + 4, "the office moves to Chennai", "Arun"))
    other.save()

    class Speaker:
        name = "test"
        def __init__(self):
            self.spoken = []
        def speak(self, text, interrupt=False):
            self.spoken.append(text)
        def stop(self):
            pass

    speaker = Speaker()
    app = App(Settings(transcript_dir=str(tmp_path / "a"), latency_log=""), StubClient(delay=0), speaker)
    app.history.append(("old question", "old answer"))
    assert app.select_meeting(other.folder)
    assert app.store.folder == other.folder and not app.history
    assert speaker.spoken[-1].startswith("Meeting 28 October 2026, 09:05 to 09:40 selected, 1 line.")
    assert "28 October 2026, 09:05 to 09:40" in app.status_text()
    asyncio.run(app._answer_recording("Where does the office move?", time.perf_counter()))
    assert app.history[-1][0] == "Where does the office move?"

    assert not app.select_meeting(tmp_path / "nothing here")  # unreadable: the chosen meeting stays
    assert app.store.folder == other.folder and "could not be opened" in speaker.spoken[-1]
    app.recording = True
    assert not app.select_meeting(tmp_path / "a" / "Meeting")  # not while recording
    assert "Stop the meeting transcript first" in speaker.spoken[-1]


def test_slow_meaning_search_is_waited_for_only_when_keywords_find_nothing(monkeypatch):
    from app.rag import search

    monkeypatch.setattr(search, "EMBED_TIMEOUT", 0.05)
    monkeypatch.setattr(search, "EMBED_TIMEOUT_NO_KEYWORDS", 1.0)
    client = StubClient(delay=0)
    real = client.embed

    async def slow(texts):
        await asyncio.sleep(0.2)
        return await real(texts)
    client.embed = slow
    parts = [Part(0, 60, "Ravi: driver memory is RAM times 0.2"), Part(60, 120, "Ravi: shuffle")]
    for p in parts:
        p.embedding = asyncio.run(real([p.text]))[0]
    # English words that are in the meeting: keyword search answers without waiting
    found, how = asyncio.run(search.find_parts(client, parts, "driver memory"))
    assert how == "keywords" and found[0] == 0
    # No word in common (e.g. a Tamil question): the slow meaning search is waited for
    found, how = asyncio.run(search.find_parts(client, parts, "டிரைவர் மெமரி"))
    assert how == "meaning+keywords" and found


def test_screenshots_are_sent_smaller():
    from app.qa.review import SEND_IMAGE_SIDE, shrink

    big = io.BytesIO()
    Image.new("RGB", (1280, 720), (200, 200, 200)).save(big, "JPEG")
    small = Image.open(io.BytesIO(shrink(big.getvalue())))
    assert max(small.size) == SEND_IMAGE_SIDE
    tiny = jpeg("x")
    assert shrink(tiny) == tiny and shrink(b"not a jpeg") == b"not a jpeg"
