"""Questions about a finished recording (after Ctrl+Alt+M stops it), answered from the meeting folder.

1. Find: the 1-minute parts of the meeting that answer the question, by meaning and keywords
   (app/rag/search.py). The user does not say a time.
2. Answer: Nemotron gets, in one call,
   - all the summaries (one every 2.5 minutes), for the whole meeting at a glance;
   - the transcript: all of it for a short meeting, else the lines of the found parts;
   - every screenshot shown in the found parts, each with the exact time it was on screen
     (at most 8, Nemotron's limit on Nebius: near-identical ones are left out first, and
     their notes are still sent as text);
   - the question, and the last few questions and answers for follow-ups.
   It answers in up to five short sentences, spoken as they arrive.
Questions while recording are not changed (app/qa/ask.py).
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable, Sequence

import numpy as np

from app.eval.latency import LatencyLog
from app.llm.client import ModelClient, image_block
from app.memory.buffer import Frame, Snapshot, TranscriptLine
from app.memory.transcript import UNKNOWN_SPEAKER
from app.qa.ask import LANGUAGE_RULE, AnswerResult, answer_question, question_text
from app.rag.index import overlaps
from app.rag.search import TOP_PARTS, find_parts
from app.rag.store import MeetingStore, Part, ScreenRecord, clock

MAX_SENTENCES = 5
TRANSCRIPT_CHARS = 12000  # about 20 minutes of talk: a meeting this short is sent whole
PAD_SECONDS = 30          # transcript kept on each side of a found part
# Screenshots are saved at 1280 px and sent at 960: on a real 7-minute recording, 8 screenshots
# gave the first words in 2.0 s instead of 2.7 s, with the same answers (small text like
# "127.0.0.1:5000" and "144.0" still read correctly).
SEND_IMAGE_SIDE = 960

REVIEW_PROMPT = """\
You help a blind person understand a meeting that was recorded. You receive summaries of the \
whole meeting, the transcript with speaker names (all of it, or the minutes that match the \
question), the screenshots shown in those minutes with the exact time each was on screen, \
and the user's question. Times are clock times, with the time since the recording started \
in brackets.
Rules:
- Use only what is in the screenshots, the transcript, the notes and the summaries. Never \
invent numbers, names or text. The exact words of the transcript and the screenshots win \
over a summary.
- The user cannot see the screen: when they ask about a page, read out its important \
content (titles, items, numbers, names, dates), not just what it is.
- Say who said something, and when, if it helps. "Speaker" means the name was not visible.
- If something is not legible or not in the material, say so.
- Answer in at most five short sentences of plain language. No markdown, lists or emoji. \
Your answer is read aloud.
- Earlier questions and answers may come before the current question; use them to \
understand follow-ups.
""" + LANGUAGE_RULE


def _since(ts: float, started: float) -> str:
    s = max(0, round(ts - started))
    return f"{s // 60}:{s % 60:02d}"


def _when(a: float, b: float, started: float) -> str:
    return f"{clock(a)} to {clock(b)} ({_since(a, started)} to {_since(b, started)})"


def _size(lines: Sequence[TranscriptLine]) -> int:
    return sum(len(l.text) + len(l.speaker) + 20 for l in lines)


def format_transcript(lines: Sequence[TranscriptLine], started: float) -> str:
    return "\n".join(f"[{clock(l.start)} ({_since(l.start, started)})] {l.speaker or UNKNOWN_SPEAKER}: {l.text}"
                     for l in lines) or "(no speech captured)"


def spread(n: int, k: int) -> list[int]:
    """k indexes spread evenly over range(n)."""
    if n <= k:
        return list(range(n))
    return [round(i * (n - 1) / (k - 1)) for i in range(k)] if k > 1 else [n - 1]


def transcript_for(lines: Sequence[TranscriptLine], parts: Sequence[Part],
                   max_chars: int = TRANSCRIPT_CHARS, pad: float = PAD_SECONDS) -> list[TranscriptLine]:
    """The whole transcript if it fits, else the lines in and around the found parts."""
    if _size(lines) <= max_chars:
        return list(lines)
    near = [l for l in lines if any(overlaps(l.start, l.end + 0.01, p.start - pad, p.end + pad) for p in parts)]
    out, used = [], 0
    for l in near:
        used += _size([l])
        if used > max_chars:
            break
        out.append(l)
    return out


def screens_in(screens: Sequence[ScreenRecord], parts: Sequence[Part]) -> list[ScreenRecord]:
    """Every screen that was showing during any of the parts, in time order."""
    return [s for s in screens if any(overlaps(s.first_ts, s.last_ts + 1, p.start, p.end) for p in parts)]


def _fingerprint(jpeg: bytes) -> np.ndarray | None:
    from PIL import Image

    from app.screen.capture import fingerprint

    try:
        return fingerprint(Image.open(io.BytesIO(jpeg)))
    except Exception:  # noqa: BLE001 - an unreadable file is just never "the same"
        return None


def fit_screens(shots: list[tuple[ScreenRecord, bytes]], max_images: int) -> list[tuple[ScreenRecord, bytes]]:
    """At most max_images screenshots: while there are too many, leave out the one that looks
    most like the screenshot before it (a small scroll, a typed letter), preferring screens
    without a note. Time order is kept."""
    if len(shots) <= max_images:
        return shots
    kept = list(shots)
    prints = [_fingerprint(jpeg) for _, jpeg in kept]
    while len(kept) > max_images:
        def cost(i: int) -> float:
            a, b = prints[i - 1], prints[i]
            diff = float(np.abs(a - b).mean()) if a is not None and b is not None else 255.0
            return diff + (1000.0 if kept[i][0].note else 0.0)
        drop = min(range(1, len(kept)), key=cost)
        del kept[drop], prints[drop]
    return kept


def shrink(jpeg: bytes, side: int = SEND_IMAGE_SIDE) -> bytes:
    """The screenshot at most `side` pixels wide or high, for a quicker answer."""
    from PIL import Image

    from app.screen.capture import encode_frame

    try:
        img = Image.open(io.BytesIO(jpeg))
        return jpeg if max(img.size) <= side else encode_frame(img, side, 70)[0]
    except Exception:  # noqa: BLE001 - send it as it is
        return jpeg


def build_recording_messages(store: MeetingStore, lines: Sequence[TranscriptLine],
                             shots: Sequence[tuple[ScreenRecord, bytes]], others: Sequence[ScreenRecord],
                             question: str, history: Sequence[tuple[str, str]] = ()) -> list[dict]:
    started = store.started
    summaries = "\n".join(f"{_when(s.start, s.end, started)}: {s.text}" for s in store.summaries if s.text)
    content: list[dict] = [
        {"type": "text", "text": f"Recording started at {clock(started)}.\n\n"
                                 f"Summaries of the whole meeting:\n{summaries or '(no summaries)'}"},
        {"type": "text", "text": f"Transcript:\n{format_transcript(lines, started)}"},
    ]
    if others:
        content.append({"type": "text", "text": "Other screens in these minutes (notes only):\n" + "\n".join(
            f"{_when(s.first_ts, s.last_ts, started)}: {s.note}" for s in others)})
    for record, jpeg in shots:
        note = f" Note: {record.note}" if record.note else ""
        content.append({"type": "text", "text": f"Screenshot {clock(record.first_ts)}, on screen "
                                                f"{_when(record.first_ts, record.last_ts, started)}.{note}"})
        content.append(image_block(jpeg))
    if not shots:
        content.append({"type": "text", "text": "(no screenshots for these minutes)"})
    content.append({"type": "text", "text": question_text(question, lines)})
    earlier: list[dict] = []
    for q, a in history:
        earlier += [{"role": "user", "content": f"Question: {q.strip()}"},
                    {"role": "assistant", "content": a}]
    return [{"role": "system", "content": REVIEW_PROMPT}, *earlier, {"role": "user", "content": content}]


async def answer_from_recording(client: ModelClient, store: MeetingStore, question: str, *,
                                speak: Callable[[str, bool], None] | None = None,
                                max_images: int = 8, log: LatencyLog | None = None,
                                question_end: float | None = None,
                                history: Sequence[tuple[str, str]] = ()) -> AnswerResult:
    """Find the parts (step 1), then answer from them (step 2), spoken as it streams."""
    t0 = question_end if question_end is not None else time.perf_counter()
    all_lines, screens = store.snapshot()
    parts = store.parts
    found, how = await find_parts(client, parts, question)
    if not found:  # nothing matched (or nothing indexed): minutes spread over the meeting
        with_text = [i for i, p in enumerate(parts) if p.text] or list(range(len(parts)))
        found = [with_text[i] for i in spread(len(with_text), TOP_PARTS)]
    chosen = [parts[i] for i in sorted(found)]

    lines = transcript_for(all_lines, chosen)
    shown = screens_in(screens, chosen)
    shots = [(s, jpeg) for s in shown if (jpeg := store.jpeg(s))]
    shots = [(s, shrink(jpeg)) for s, jpeg in fit_screens(shots, max_images)]
    kept = {s.number for s, _ in shots}
    others = [s for s in shown if s.number not in kept and s.note]
    if log:
        log.write("review_find", latency_s=round(time.perf_counter() - t0, 3), search=how,
                  parts=len(parts), found=len(chosen), screens=len(shown), images=len(shots),
                  transcript_lines=len(lines), summaries=sum(1 for s in store.summaries if s.text))

    messages = build_recording_messages(store, lines, shots, others, question, history)
    frames = [Frame(s.last_ts, jpeg, 0, 0, s.first_ts) for s, jpeg in shots]
    return await answer_question(client, Snapshot(time.time(), frames, lines), question, speak=speak,
                                 max_frames=max(1, len(frames)), log=log, question_end=t0,
                                 messages=messages, max_sentences=MAX_SENTENCES,
                                 log_event="answer_recording")
