"""Screen history of the whole recording, in memory only, plus Nemotron page notes.

While a meeting transcript is recorded (Ctrl+Alt+M), every DIFFERENT screen is kept as a
numbered shot (screenshot 1, 2, 3...). A screen that stays the same only moves its
"last seen" time. When a screen has been shown for NOTE_MIN_SECONDS, Nemotron writes a
one-line note about it ("Slide: Yesterday's status, 3 done, 2 pending"), which also goes
into the saved transcript file. After recording stops, questions are answered from these
shots and notes (app/qa/review.py). Starting a new recording or closing the app clears it.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

NOTE_MIN_SECONDS = 3.0  # a screen shown this long gets a Nemotron note; quicker ones (scrolling) do not
MAX_SHOTS = 600         # about 60 MB of JPEGs; beyond it, quick unnoted screens are dropped first


@dataclass
class Shot:
    number: int        # 1, 2, 3... in recording order
    first_ts: float    # when this screen first appeared
    last_ts: float     # when it was last seen unchanged
    jpeg: bytes
    width: int
    height: int
    note: str = ""     # Nemotron's one-line description, empty until written
    tried: bool = False

    @property
    def seconds(self) -> float:
        return self.last_ts - self.first_ts


class ScreenHistory:
    def __init__(self, max_shots: int = MAX_SHOTS):
        self.max_shots = max_shots
        self.started = 0.0
        self._shots: list[Shot] = []
        self._count = 0
        self._lock = threading.Lock()
        # Called (on the capture thread) for each new screen and each time the newest is seen
        # again, so the meeting folder keeps every screen (app/rag/store.py).
        self.on_new: Callable[[Shot], None] | None = None
        self.on_seen: Callable[[Shot], None] | None = None

    def clear(self, started: float = 0.0) -> None:
        with self._lock:
            self._shots = []
            self._count = 0
            self.started = started

    def add(self, ts: float, jpeg: bytes, width: int, height: int, unchanged: bool = False) -> None:
        with self._lock:
            if unchanged and self._shots:
                shot = self._shots[-1]
                shot.last_ts = ts
                listener = self.on_seen
            elif not jpeg:
                return
            else:
                self._count += 1
                shot = Shot(self._count, ts, ts, jpeg, width, height)
                self._shots.append(shot)
                if len(self._shots) > self.max_shots:
                    self._drop_one()
                listener = self.on_new
        if listener:
            listener(shot)

    def get(self, number: int) -> Shot | None:
        with self._lock:
            return next((s for s in self._shots if s.number == number), None)

    def _drop_one(self) -> None:
        """Drop the oldest quick, unnoted screen (scrolling, typing); else the oldest one."""
        for i, s in enumerate(self._shots[:-1]):
            if not s.note and s.seconds < NOTE_MIN_SECONDS:
                del self._shots[i]
                return
        del self._shots[0]

    def shots(self) -> list[Shot]:
        with self._lock:
            return list(self._shots)

    def __len__(self) -> int:
        with self._lock:
            return len(self._shots)

    def next_to_note(self, min_seconds: float = NOTE_MIN_SECONDS) -> Shot | None:
        with self._lock:
            for s in self._shots:
                if not s.tried and s.seconds >= min_seconds:
                    return s
        return None


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean_note(raw: str, max_chars: int = 300) -> str:
    text = re.sub(r"\s+", " ", _THINK.sub("", raw or "")).strip().strip('"')
    return text[:max_chars].rstrip()


class PageNoter:
    """Asks Nemotron for a one-line note on each screen that stays up NOTE_MIN_SECONDS, one at a time."""

    def __init__(self, client, history: ScreenHistory, on_note: Callable[[Shot], None] | None = None,
                 min_seconds: float = NOTE_MIN_SECONDS):
        self.client = client
        self.history = history
        self.on_note = on_note
        self.min_seconds = min_seconds
        self.last_error: Exception | None = None

    async def run(self) -> None:
        """Runs while recording; cancelled when recording stops."""
        while True:
            shot = self.history.next_to_note(self.min_seconds)
            if shot is None:
                await asyncio.sleep(1.0)
                continue
            await self._note(shot)

    async def finish(self, timeout: float = 30.0) -> None:
        """After recording stops: note the screens still waiting, within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while (shot := self.history.next_to_note(self.min_seconds)) and time.monotonic() < deadline:
            try:
                await asyncio.wait_for(self._note(shot), max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break

    def pending(self) -> int:
        return sum(1 for s in self.history.shots() if not s.tried and s.seconds >= self.min_seconds)

    async def _note(self, shot: Shot) -> None:
        shot.tried = True
        try:
            shot.note = clean_note(await self.client.describe_page(shot.jpeg))
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - a missing note must not stop the recording
            self.last_error = e
            return
        if shot.note and self.on_note:
            self.on_note(shot)
