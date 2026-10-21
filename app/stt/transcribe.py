"""Speech segment -> Omni -> transcript line in the rolling buffer."""

from __future__ import annotations

import asyncio
import re
import time

from app.audio.vad import Segment
from app.audio.wav import encode_wav
from app.eval.latency import LatencyLog
from app.llm.client import ModelClient
from app.memory.buffer import RollingBuffer, TranscriptLine

# Things a model sometimes says instead of a transcript when there is no clear speech.
_NON_SPEECH = re.compile(r"^\W*([\[(]?(no speech|no output|silence|inaudible|music|blank audio)[\])]?\W*)?$", re.I)


def clean_transcript(text: str) -> str:
    text = text.strip().strip('"').strip()
    return "" if _NON_SPEECH.match(text) else text


class Transcriber:
    def __init__(self, client: ModelClient, buffer: RollingBuffer, log: LatencyLog | None = None):
        self.client = client
        self.buffer = buffer
        self.log = log
        self.queue: asyncio.Queue[Segment] = asyncio.Queue()
        self.last_error: Exception | None = None

    async def run(self) -> None:
        while True:
            seg = await self.queue.get()
            # Skip work that would already have fallen out of the window.
            if seg.end < time.time() - self.buffer.window:
                continue
            t0 = time.perf_counter()
            try:
                text = clean_transcript(await self.client.transcribe(encode_wav(seg.audio)))
                self.last_error = None
            except Exception as e:  # noqa: BLE001 - one bad chunk must not stop transcription
                self.last_error = e
                if self.log:
                    self.log.write("stt_error", error=type(e).__name__)
                continue
            if self.log:
                self.log.write("stt", audio_s=round(seg.duration, 2),
                               latency_s=round(time.perf_counter() - t0, 3), empty=not text)
            if text:
                self.buffer.add_line(TranscriptLine(seg.start, seg.end, text))
