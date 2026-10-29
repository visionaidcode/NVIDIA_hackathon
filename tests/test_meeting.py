import asyncio
import time

import numpy as np

from app.audio.vad import Segment
from app.config import Settings
from app.llm.client import StubClient
from app.memory.buffer import Frame, RollingBuffer, Snapshot, TranscriptLine
from app.memory.transcript import MeetingTranscript, format_line
from app.qa.ask import build_messages
from app.stt.speaker import SpeakerNamer, clean_name, frame_during
from app.stt.transcribe import Transcriber


def test_transcript_file_has_speakers_and_is_written_line_by_line(tmp_path):
    now = [time.mktime((2026, 10, 22, 10, 40, 0, 0, 0, -1))]
    t = MeetingTranscript(tmp_path, clock=lambda: now[0])
    assert not t.active
    path = t.start()
    assert (path.parent.name, path.name) == ("Meeting 22-10-2026 10-40", "transcript.txt") and t.active
    t.add(TranscriptLine(t.clock(), t.clock() + 2, "the button moves", "Priya"))
    assert "] Priya: the button moves" in path.read_text(encoding="utf-8")  # saved before stop
    t.add(TranscriptLine(t.clock(), t.clock() + 2, "ok", ""))
    (path.parent / "screens").mkdir()  # moves with the folder
    now[0] += 35 * 60
    stopped = t.stop()
    assert stopped.parent.name == "Meeting 22-10-2026 10-40 to 11-15" and not t.active
    assert not path.parent.exists() and (stopped.parent / "screens").is_dir()
    text = stopped.read_text(encoding="utf-8")
    assert "Speaker: ok" in text and "Ended 11:15" in text
    t.add(TranscriptLine(0, 1, "after stop"))
    assert "after stop" not in stopped.read_text(encoding="utf-8")
    now[0] -= 35 * 60
    assert t.start().parent.name == "Meeting 22-10-2026 10-40"
    t.add_summary(t.clock(), t.clock() + 150, "Priya showed the sales slide.")
    assert "] Summary (10:40:00 to 10:42:30): Priya showed the sales slide." in t.path.read_text(encoding="utf-8")
    now[0] += 35 * 60
    assert t.stop().parent.name == "Meeting 22-10-2026 10-40 to 11-15 (2)"  # same times: not overwritten


def test_recent_keeps_newest_lines_within_limit(tmp_path):
    t = MeetingTranscript(tmp_path)
    t.start()
    for i in range(10):
        t.add(TranscriptLine(i, i + 1, f"line {i} " + "x" * 50, "A"))
    recent = t.recent(max_chars=200)
    assert recent[-1].text.startswith("line 9") and len(recent) < 10
    assert [line.start for line in recent] == sorted(line.start for line in recent)


def test_clean_name():
    assert clean_name("Priya Sharma") == "Priya Sharma"
    assert clean_name('<think>tile is blue</think>\n"Priya."') == "Priya"
    assert clean_name("Speaker: John") == "John"
    assert clean_name("Unknown") == ""
    assert clean_name("") == ""
    assert clean_name("I cannot tell from this screenshot who is speaking right now, sorry") == ""


def test_frame_during_picks_screen_shown_while_speaking():
    a = Frame(10, b"a", 1, 1, first_ts=1)    # on screen 1..10
    b = Frame(30, b"b", 1, 1, first_ts=20)   # on screen 20..30
    assert frame_during([a, b], 5, 9) is a
    assert frame_during([a, b], 21, 25) is b
    assert frame_during([a, b], 50, 55) is None
    assert frame_during([Frame(10, b"", 0, 0)], 5, 9) is None


def test_speaker_namer_reuses_name_for_unchanged_screen():
    buf = RollingBuffer(1e9)
    client = StubClient(speaker="Priya", delay=0)
    calls = []
    real = client.identify_speaker

    async def counting(jpeg):
        calls.append(jpeg)
        return await real(jpeg)
    client.identify_speaker = counting
    now = time.time()
    buf.add_frame(Frame(now, b"jpeg", 1, 1))
    namer = SpeakerNamer(client, buf)
    assert asyncio.run(namer.name_for(now - 1, now)) == "Priya"
    assert asyncio.run(namer.name_for(now - 1, now)) == "Priya"
    assert len(calls) == 1


def test_transcriber_names_and_saves_each_line(tmp_path):
    buf = RollingBuffer(1e9)
    now = time.time()
    buf.add_frame(Frame(now, b"jpeg", 1, 1))
    meeting = MeetingTranscript(tmp_path)
    meeting.start()
    client = StubClient(transcript="we ship on Friday", speaker="Sam", delay=0)
    tr = Transcriber(client, buf, name_speaker=SpeakerNamer(client, buf).name_for, on_line=meeting.add)

    async def go():
        task = asyncio.create_task(tr.run())
        tr.queue.put_nowait(Segment(now - 2, now, np.zeros(1600, dtype=np.float32)))
        await asyncio.wait_for(tr.queue.join(), 5)
        task.cancel()
    asyncio.run(go())
    assert meeting.lines == [TranscriptLine(now - 2, now, "we ship on Friday", "Sam")]
    assert buf.snapshot().transcript[0].speaker == "Sam"
    assert "Sam: we ship on Friday" in meeting.path.read_text(encoding="utf-8")


def test_prompt_shows_who_said_what():
    snap = Snapshot(1000.0, [], [TranscriptLine(600, 700, "budget is fine", "Priya"),
                                 TranscriptLine(990, 995, "next slide", "")])
    text = build_messages(snap, "Who talked about the budget?")[1]["content"][0]["text"]
    assert "[5 min ago] Priya: budget is fine" in text
    assert "[5 s ago] Speaker: next slide" in text
    assert format_line(TranscriptLine(0, 1, "hi", "")).endswith("Speaker: hi")


def test_questions_need_a_running_transcript(tmp_path):
    from app.main import App

    spoken = []

    class Speaker:
        name = "test"
        def speak(self, text, interrupt=False):
            spoken.append(text)
        def stop(self):
            pass

    app = App(Settings(transcript_dir=str(tmp_path), latency_log=""), StubClient(delay=0), Speaker())
    app.loop = asyncio.new_event_loop()
    try:
        app.ask("What is on screen?")
    finally:
        app.loop.close()
    assert "not being transcribed" in spoken[-1] and "control alt M" in spoken[-1]
    assert "Not transcribing" in app.status_text()


def test_own_voice_is_not_transcribed():
    from app.speech.out import PrintSpeaker, SelfSpeechGuard

    now = [100.0]
    guard = SelfSpeechGuard(PrintSpeaker(), clock=lambda: now[0])
    guard.speak("x" * 24)                    # about 2 s of speech: 100..102
    guard.speak("y" * 12)                    # queued after it: 102..103
    assert guard.overlaps(101, 101.5) and guard.overlaps(102.5, 103)
    assert guard.overlaps(103.5, 106)        # within the margin after speech ends
    assert not guard.overlaps(105, 108) and not guard.overlaps(90, 99)
    now[0] = 100.5
    guard.speak("z" * 12, interrupt=True)    # cuts the queue: 100.5..101.5
    assert not guard.overlaps(103, 106)
    now[0] = 101.0
    guard.stop()
    assert not guard.overlaps(102.1, 104)
