"""Who is speaking: read the active speaker's name from the meeting screen.

Teams, Zoom and Meet highlight the tile of whoever is talking and often show their name
in captions. The screenshot taken while a segment was spoken is sent to the answer model,
which returns that name. Unchanged screens reuse the last answer, so a meeting where the
picture does not change costs one call, not one per sentence.
"""

from __future__ import annotations

import re

from app.memory.buffer import Frame, RollingBuffer

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_UNKNOWN = {"unknown", "none", "no one", "nobody", "n/a", "unclear", "not visible"}


def clean_name(raw: str) -> str:
    """First line of the model's reply, without quotes or labels; empty if it did not know."""
    text = _THINK.sub("", raw or "").strip()
    text = text.splitlines()[0] if text else ""
    text = re.sub(r"^(name|speaker|active speaker)\s*:\s*", "", text, flags=re.I)
    text = text.strip(" \t\"'*.`")
    if not text or text.lower() in _UNKNOWN or len(text) > 40:
        return ""
    return text


def frame_during(frames: list[Frame], start: float, end: float) -> Frame | None:
    """The screen as it was while [start, end] was spoken: the last picture that had
    appeared by the end of the segment and was still showing at its start."""
    best = None
    for f in frames:
        if f.jpeg and (f.first_ts or f.ts) <= end and f.ts >= start - 2:
            best = f
    return best


class SpeakerNamer:
    def __init__(self, client, buffer: RollingBuffer):
        self.client = client
        self.buffer = buffer
        self._last: tuple[float, str] | None = None  # (when the picture first appeared, name)
        self.last_error: Exception | None = None

    async def name_for(self, start: float, end: float) -> str:
        frame = frame_during(self.buffer.snapshot().frames, start, end)
        if frame is None:
            return ""
        key = frame.first_ts or frame.ts
        if self._last and self._last[0] == key:
            return self._last[1]
        try:
            name = clean_name(await self.client.identify_speaker(frame.jpeg))
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - a missing name must not lose the line
            self.last_error = e
            return ""
        self._last = (key, name)
        return name
