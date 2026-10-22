"""Speech segment -> speech model -> transcript line, named from the screen, to the buffer and
the saved meeting transcript."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

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
    def __init__(self, client: ModelClient, buffer: RollingBuffer, log: LatencyLog | None = None,
                 name_speaker: Callable[[float, float], Awaitable[str]] | None = None,
                 on_line: Callable[[TranscriptLine], None] | None = None):
        self.client = client
        self.buffer = buffer
        self.log = log
        self.name_speaker = name_speaker
        self.on_line = on_line
        self.queue: asyncio.Queue[Segment] = asyncio.Queue()
        self.last_error: Exception | None = None

    async def run(self) -> None:
        while True:
            seg = await self.queue.get()
            try:
                await self._handle(seg)
            finally:
                self.queue.task_done()

    async def _handle(self, seg: Segment) -> None:
        # Every segment is transcribed, even late ones: the meeting transcript is saved.
        t0 = time.perf_counter()
        try:
            text = clean_transcript(await self.client.transcribe(encode_wav(seg.audio)))
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - one bad chunk must not stop transcription
            self.last_error = e
            if self.log:
                self.log.write("stt_error", error=type(e).__name__)
            return
        if self.log:
            self.log.write("stt", audio_s=round(seg.duration, 2),
                           latency_s=round(time.perf_counter() - t0, 3), empty=not text)
        if not text:
            return
        speaker = await self.name_speaker(seg.start, seg.end) if self.name_speaker else ""
        line = TranscriptLine(seg.start, seg.end, text, speaker)
        self.buffer.add_line(line)
        if self.on_line:
            self.on_line(line)
