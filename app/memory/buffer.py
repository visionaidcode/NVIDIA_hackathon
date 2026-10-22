"""Rolling in-memory window of screen frames and transcript lines. Nothing here touches disk;
the saved meeting transcript is app/memory/transcript.py."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Frame:
    ts: float          # time the frame was last seen on screen
    jpeg: bytes
    width: int
    height: int
    first_ts: float = 0.0  # time this picture first appeared (same as ts unless unchanged since)


@dataclass(frozen=True)
class TranscriptLine:
    start: float
    end: float
    text: str
    speaker: str = ""  # name read from the meeting screen; empty if not known


@dataclass(frozen=True)
class Snapshot:
    now: float
    frames: list[Frame]
    transcript: list[TranscriptLine]


class RollingBuffer:
    def __init__(self, window_seconds: float = 30.0, clock: Callable[[], float] = time.time):
        self.window = window_seconds
        self.clock = clock
        self._frames: deque[Frame] = deque()
        self._lines: deque[TranscriptLine] = deque()
        self._lock = threading.Lock()

    def add_frame(self, frame: Frame, unchanged: bool = False) -> None:
        """Add a frame. If `unchanged`, the screen looks the same as the last frame,
        so only refresh its timestamp instead of storing a duplicate."""
        with self._lock:
            if unchanged and self._frames:
                self._frames[-1].ts = frame.ts
            else:
                if not frame.first_ts:
                    frame.first_ts = frame.ts
                self._frames.append(frame)
            self._prune()

    def add_line(self, line: TranscriptLine) -> None:
        if not line.text.strip():
            return
        with self._lock:
            self._lines.append(line)
            self._prune()

    def snapshot(self) -> Snapshot:
        with self._lock:
            self._prune()
            return Snapshot(self.clock(), list(self._frames), list(self._lines))

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
            self._lines.clear()

    def _prune(self) -> None:
        cutoff = self.clock() - self.window
        while self._frames and self._frames[0].ts < cutoff:
            self._frames.popleft()
        while self._lines and self._lines[0].end < cutoff:
            self._lines.popleft()
