"""The meeting transcript: every line with its speaker, kept in memory and saved to a text file.

Recording is started and stopped by the user (Ctrl+Alt+M). Each meeting gets its own folder,
named by its date and start time, e.g. "Documents/Saarthi/Meeting 22-10-2026 10-40", and the
end time is added when the recording stops: "Meeting 22-10-2026 10-40 to 11-15". transcript.txt
is in it (the screenshots and meeting.json for questions after the recording go next to it,
app/rag/store.py). Each line is written as soon as it is transcribed, so the file is complete
even if the app is closed mid-meeting.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.memory.buffer import TranscriptLine

UNKNOWN_SPEAKER = "Speaker"
TRANSCRIPT_FILE = "transcript.txt"
FOLDER_NAME = "Meeting %d-%m-%Y %H-%M"  # day-month-year and start time ("/" is not allowed in names)
END_NAME = " to %H-%M"


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def format_line(line: TranscriptLine) -> str:
    return f"[{time.strftime('%H:%M:%S', time.localtime(line.start))}] " \
           f"{line.speaker or UNKNOWN_SPEAKER}: {line.text}"


def _free_folder(parent: Path, name: str) -> Path:
    """parent/name, or "name (2)", "name (3)" ... if that folder already exists."""
    folder, n = parent / name, 2
    while folder.exists():
        folder, n = parent / f"{name} ({n})", n + 1
    return folder


class MeetingTranscript:
    def __init__(self, folder: str | Path, clock=time.time):
        self.folder = Path(folder)
        self.clock = clock
        self.path: Path | None = None
        self._started = time.localtime(clock())
        self.lines: list[TranscriptLine] = []
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self.path is not None

    def start(self) -> Path:
        """Begin a new meeting folder and its transcript file,
        e.g. "Meeting 22-10-2026 10-40/transcript.txt"."""
        started = time.localtime(self.clock())
        self._started = started
        meeting_dir = _free_folder(self.folder, time.strftime(FOLDER_NAME, started))
        meeting_dir.mkdir(parents=True)
        path = meeting_dir / TRANSCRIPT_FILE
        path.write_text(time.strftime("Meeting transcript, started %Y-%m-%d %H:%M\n\n", started),
                        encoding="utf-8")
        with self._lock:
            self.lines = []
            self.path = path
        return path

    def add(self, line: TranscriptLine) -> None:
        with self._lock:
            if not self.path or not line.text.strip():
                return
            self.lines.append(line)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(format_line(line) + "\n")

    def add_note(self, ts: float, note: str) -> None:
        """A Nemotron note on a new screen, e.g. "[10:02:05] Screen: Slide: Yesterday's status ...".
        Written to the file only; `lines` stays what was said."""
        with self._lock:
            if not self.path or not note.strip():
                return
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S', time.localtime(ts))}] Screen: {note}\n")

    def add_summary(self, start: float, end: float, text: str) -> None:
        """A summary of a stretch of the meeting (app/rag/index.py), e.g.
        "[10:42:30] Summary (10:40:00 to 10:42:30): Priya showed the Q3 sales slide ..."."""
        with self._lock:
            if not self.path or not text.strip():
                return
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"[{_clock(end)}] Summary ({_clock(start)} to {_clock(end)}): {text}\n")

    def stop(self) -> Path | None:
        """End the transcript and add the end time to the folder name,
        e.g. "Meeting 22-10-2026 10-40 to 11-15". Returns the transcript's (new) path."""
        with self._lock:
            path, self.path = self.path, None
        if not path:
            return None
        ended = time.localtime(self.clock())
        with path.open("a", encoding="utf-8") as f:
            f.write(time.strftime("\nEnded %H:%M\n", ended))
        name = time.strftime(FOLDER_NAME, self._started) + time.strftime(END_NAME, ended)
        try:
            folder = path.parent.rename(_free_folder(self.folder, name))
        except OSError:  # e.g. a file in it is open elsewhere: keep the start-time name
            return path
        return folder / TRANSCRIPT_FILE

    def recent(self, max_chars: int = 12000) -> list[TranscriptLine]:
        """The newest lines that fit in max_chars, oldest first, for the question prompt."""
        with self._lock:
            out: list[TranscriptLine] = []
            used = 0
            for line in reversed(self.lines):
                used += len(line.text) + len(line.speaker) + 4
                if used > max_chars and out:
                    break
                out.append(line)
            return out[::-1]
