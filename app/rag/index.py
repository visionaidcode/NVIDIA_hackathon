"""Builds the searchable meeting while it is recorded, so questions after it are answered at once.

- Parts: the meeting cut into 1-minute pieces. Each holds what was said (with speaker names)
  and the notes on the screens shown, and gets an embedding so a question can find it by
  meaning (app/rag/search.py).
- Summaries: every SUMMARY_MINUTES (2.5 by default) Nemotron writes a short summary of that
  stretch, keeping names, numbers, dates, decisions and tasks. They go into transcript.txt
  and meeting.json, and every question after the recording gets all of them.

Lines are transcribed and screens noted a few seconds late, so a piece is built only once it
is SETTLE_SECONDS old; when recording stops, `update(final=True)` finishes the rest.
A part whose text changes later (a late line) is embedded again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from app.memory.buffer import TranscriptLine
from app.memory.history import clean_note
from app.memory.transcript import UNKNOWN_SPEAKER
from app.rag.store import MeetingStore, Part, ScreenRecord, Summary, clock

PART_SECONDS = 60.0
SETTLE_SECONDS = 20.0
EMBED_BATCH = 32


def overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


def window_text(lines: list[TranscriptLine], screens: list[ScreenRecord], start: float, end: float) -> str:
    """What was said in [start, end), then the notes on the screens shown in it."""
    said = [f"{l.speaker or UNKNOWN_SPEAKER}: {l.text}" for l in lines if start <= l.start < end]
    shown = [f"Screen: {s.note}" for s in screens if s.note and overlaps(s.first_ts, s.last_ts + 1, start, end)]
    return "\n".join(said + shown)


class MeetingIndexer:
    def __init__(self, client, store: MeetingStore, summary_seconds: float = 150.0,
                 on_summary: Callable[[Summary], None] | None = None, clock=time.time):
        self.client = client
        self.store = store
        self.summary_seconds = summary_seconds
        self.on_summary = on_summary
        self.clock = clock
        self.can_embed = bool(getattr(client, "embed_model", ""))
        self.last_error: Exception | None = None

    async def run(self, every: float = 5.0) -> None:
        """Runs while recording; cancelled when recording stops."""
        while True:
            await asyncio.sleep(every)
            await self.update()

    async def update(self, final: bool = False) -> None:
        now = self.clock()
        ready = now if final else now - SETTLE_SECONDS
        lines, screens = self.store.snapshot()
        changed = self._update_parts(lines, screens, ready, final)
        changed = await self._embed_parts() or changed
        changed = await self._summarize(lines, screens, ready, final) or changed
        if changed:
            self.store.save()

    # --- parts --------------------------------------------------------------

    def _update_parts(self, lines, screens, ready: float, final: bool) -> bool:
        start, parts, changed = self.store.started, self.store.parts, False
        i = 0
        while True:
            a = start + i * PART_SECONDS
            b = a + PART_SECONDS
            if a >= ready or (b > ready and not final):
                break
            text = window_text(lines, screens, a, min(b, ready) if final else b)
            if i < len(parts):
                if parts[i].text != text:
                    parts[i].text, parts[i].embedding, changed = text, None, True
            else:
                parts.append(Part(a, b, text))
                changed = True
            i += 1
        return changed

    async def _embed_parts(self) -> bool:
        if not self.can_embed:
            return False
        todo = [p for p in self.store.parts if p.text and p.embedding is None]
        for k in range(0, len(todo), EMBED_BATCH):
            batch = todo[k:k + EMBED_BATCH]
            try:
                vectors = await self.client.embed([p.text for p in batch])
            except Exception as e:  # noqa: BLE001 - keyword search still works; retried next time
                self.last_error = e
                return k > 0
            for p, v in zip(batch, vectors):
                p.embedding = v
            self.last_error = None
        return bool(todo)

    # --- summaries ----------------------------------------------------------

    async def _summarize(self, lines, screens, ready: float, final: bool) -> bool:
        changed = False
        summaries = self.store.summaries
        a = summaries[-1].end if summaries else self.store.started
        while a < ready:
            b = a + self.summary_seconds
            if b > ready:
                if not final:
                    break
                b = ready
            text = window_text(lines, screens, a, b)
            if text:
                try:
                    summary = clean_note(await self.client.summarize(
                        f"Meeting from {clock(a)} to {clock(b)}:\n{text}"), max_chars=1000)
                except Exception as e:  # noqa: BLE001 - tried again on the next update
                    self.last_error = e
                    return changed
                summary = Summary(a, b, summary)
                summaries.append(summary)
                if self.on_summary:
                    self.on_summary(summary)
            else:  # nothing said or noted: no summary, move on
                summaries.append(Summary(a, b, ""))
            changed = True
            a = b
        return changed
