"""Everything about one recorded meeting, saved in its folder for questions after the recording.

    Documents/Saarthi/Meeting 29-10-2026 10-40 to 11-15/
        transcript.txt     spoken lines, screen notes and summaries (app/memory/transcript.py)
        screens/10-40-12.jpg   every different screen, named by the time it first appeared
        meeting.json       lines, screens, 1-minute parts with their embeddings, summaries

Plain files, no database: a 2-hour meeting is about 120 parts, which load and search in
well under a second. Deleting the folder deletes the meeting.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.memory.buffer import TranscriptLine
from app.memory.history import Shot

MEETING_FILE = "meeting.json"
SCREENS_DIR = "screens"


@dataclass
class ScreenRecord:
    number: int        # 1, 2, 3... in recording order, same as Shot.number
    first_ts: float    # when this screen first appeared
    last_ts: float     # when it was last seen unchanged
    file: str = ""     # "10-40-12.jpg" in screens/, empty if screenshots are not saved
    note: str = ""     # Nemotron's one-line note, empty if the screen was up only briefly


@dataclass
class Part:
    """One minute of the meeting: what was said and what the screen notes say, as searchable text."""
    start: float
    end: float
    text: str = ""
    embedding: list[float] | None = None


@dataclass
class Summary:
    start: float
    end: float
    text: str


def clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


class MeetingStore:
    def __init__(self, folder: str | Path, started: float, save_screenshots: bool = True):
        self.folder = Path(folder)
        self.started = started
        self.ended = 0.0
        self.save_screenshots = save_screenshots
        self.lines: list[TranscriptLine] = []
        self.screens: list[ScreenRecord] = []
        self.parts: list[Part] = []
        self.summaries: list[Summary] = []
        self._by_number: dict[int, ScreenRecord] = {}
        self._lock = threading.Lock()
        self.last_error: Exception | None = None
        # Screen number -> JPEG still in memory, for screenshots that were not saved to disk.
        self.memory: Callable[[int], bytes | None] | None = None

    @property
    def name(self) -> str:
        return self.folder.name

    # --- while recording (capture thread and event loop) ------------------

    def add_line(self, line: TranscriptLine) -> None:
        if line.text.strip():
            with self._lock:
                self.lines.append(line)

    def add_screen(self, shot: Shot) -> None:
        """A new screen: save its JPEG as screens/<time>.jpg and remember it."""
        record = ScreenRecord(shot.number, shot.first_ts, shot.last_ts)
        if self.save_screenshots and shot.jpeg:
            try:
                record.file = self._write_jpeg(shot)
            except OSError as e:  # a full disk must not stop the recording
                self.last_error = e
        with self._lock:
            self.screens.append(record)
            self._by_number[record.number] = record

    def screen_seen(self, shot: Shot) -> None:
        with self._lock:
            if record := self._by_number.get(shot.number):
                record.last_ts = shot.last_ts

    def set_note(self, number: int, note: str) -> None:
        with self._lock:
            if record := self._by_number.get(number):
                record.note = note

    def _write_jpeg(self, shot: Shot) -> str:
        folder = self.folder / SCREENS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        base = time.strftime("%H-%M-%S", time.localtime(shot.first_ts))
        name, n = f"{base}.jpg", 2
        while (folder / name).exists():  # two screens in the same second
            name, n = f"{base}_{n}.jpg", n + 1
        (folder / name).write_bytes(shot.jpeg)
        return name

    # --- reading ------------------------------------------------------------

    def snapshot(self) -> tuple[list[TranscriptLine], list[ScreenRecord]]:
        with self._lock:
            return list(self.lines), list(self.screens)

    def jpeg(self, record: ScreenRecord) -> bytes | None:
        """The screenshot from screens/, else from memory (SAVE_SCREENSHOTS=off), else None."""
        if record.file:
            try:
                return (self.folder / SCREENS_DIR / record.file).read_bytes()
            except OSError:
                pass
        return self.memory(record.number) if self.memory else None

    # --- meeting.json -------------------------------------------------------

    def save(self) -> None:
        """Write meeting.json (to a temporary file first, so a crash never leaves half a file)."""
        with self._lock:
            data = {
                "started": self.started, "ended": self.ended,
                "lines": [asdict(l) for l in self.lines],
                "screens": [asdict(s) for s in self.screens],
                "parts": [{**asdict(p), "embedding": _round(p.embedding)} for p in self.parts],
                "summaries": [asdict(s) for s in self.summaries],
            }
        path = self.folder / MEETING_FILE
        tmp = path.with_suffix(".tmp")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            self.last_error = e

    @classmethod
    def load(cls, folder: str | Path) -> MeetingStore:
        folder = Path(folder)
        data = json.loads((folder / MEETING_FILE).read_text(encoding="utf-8"))
        store = cls(folder, data["started"], save_screenshots=True)
        store.ended = data.get("ended", 0.0)
        store.lines = [TranscriptLine(**l) for l in data.get("lines", [])]
        store.screens = [ScreenRecord(**s) for s in data.get("screens", [])]
        store.parts = [Part(**p) for p in data.get("parts", [])]
        store.summaries = [Summary(**s) for s in data.get("summaries", [])]
        store._by_number = {s.number: s for s in store.screens}
        return store

    @classmethod
    def load_latest(cls, transcript_dir: str | Path) -> MeetingStore | None:
        """The most recent meeting in the transcripts folder that has a meeting.json, or None."""
        for folder in list_meetings(transcript_dir):
            try:
                return cls.load(folder)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None


def list_meetings(transcript_dir: str | Path) -> list[Path]:
    """The meeting folders that can be asked about (they have a meeting.json), newest first."""
    try:
        found = [(p.stat().st_mtime, p.parent) for p in Path(transcript_dir).glob(f"*/{MEETING_FILE}")]
    except OSError:
        return []
    return [folder for _, folder in sorted(found, reverse=True)]


_NAME = re.compile(r"Meeting (\d\d)-(\d\d)-(\d{4}) (\d\d)-(\d\d)(?: to (\d\d)-(\d\d))?(.*)")
_MONTHS = "January February March April May June July August September October November December".split()


def meeting_label(folder: str | Path) -> str:
    """A name that reads well aloud: "Meeting 29-10-2026 14-58 to 15-18" -> "29 October 2026, 14:58 to 15:18".
    Other folder names are shown as they are."""
    name = Path(folder).name
    m = _NAME.fullmatch(name)
    if not m or not 1 <= int(m[2]) <= 12:
        return name
    label = f"{int(m[1])} {_MONTHS[int(m[2]) - 1]} {m[3]}, {m[4]}:{m[5]}"
    if m[6]:
        label += f" to {m[6]}:{m[7]}"
    return label + m[8]


def _round(v: list[float] | None) -> list[float] | None:
    return [round(x, 5) for x in v] if v else None
