"""The meeting transcript: every line with its speaker, kept in memory and saved to a text file.

Recording is started and stopped by the user (Ctrl+Alt+M). Each line is written as soon as
it is transcribed, so the file is complete even if the app is closed mid-meeting.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.memory.buffer import TranscriptLine

UNKNOWN_SPEAKER = "Speaker"


def format_line(line: TranscriptLine) -> str:
    return f"[{time.strftime('%H:%M:%S', time.localtime(line.start))}] " \
           f"{line.speaker or UNKNOWN_SPEAKER}: {line.text}"


class MeetingTranscript:
    def __init__(self, folder: str | Path, clock=time.time):
        self.folder = Path(folder)
        self.clock = clock
        self.path: Path | None = None
        self.lines: list[TranscriptLine] = []
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self.path is not None

    def start(self) -> Path:
        """Begin a new transcript file, e.g. Meeting 2026-10-22 10-40.txt."""
        started = time.localtime(self.clock())
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / time.strftime("Meeting %Y-%m-%d %H-%M.txt", started)
        n = 2
        while path.exists():
            path = self.folder / time.strftime(f"Meeting %Y-%m-%d %H-%M ({n}).txt", started)
            n += 1
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

    def stop(self) -> Path | None:
        with self._lock:
            path, self.path = self.path, None
        if path:
            with path.open("a", encoding="utf-8") as f:
                f.write(time.strftime("\nEnded %H:%M\n", time.localtime(self.clock())))
        return path

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
