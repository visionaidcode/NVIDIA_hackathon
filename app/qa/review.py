"""Questions about a finished recording, answered by Nemotron in two steps.

A recording can hold hundreds of screens, too many for one call. So:
1. Pick: Nemotron reads the list of screens (time + page note) and the transcript, and
   names the few screens that answer the question (text only, quick).
2. Answer: those screenshots, the transcript around them and the question go to Nemotron,
   which answers in up to five short sentences, spoken as they arrive.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass

from app.eval.latency import LatencyLog
from app.llm.client import ModelClient, image_block
from app.memory.buffer import Frame, Snapshot, TranscriptLine
from app.memory.history import Shot
from app.memory.transcript import UNKNOWN_SPEAKER
from app.qa.ask import LANGUAGE_RULE, AnswerResult, answer_question, question_text, select_frames
from app.qa.text import ThinkFilter

MAX_SENTENCES = 5
TRANSCRIPT_CHARS = 12000
PAD_SECONDS = 60  # transcript kept around each chosen screen when the whole one is too long

PICK_PROMPT = """\
You help a blind person ask about a meeting that was recorded. You get a numbered list of \
the screens shown during the recording, with their times and a short note on each, the \
transcript, and the user's question. Reply with only the numbers of the screens needed to \
answer it, at most {n}, comma separated, most useful first. For a question about the whole \
meeting, pick screens spread across it. For a question about a time ("the first 2 minutes", \
"at the start"), use the times. Reply with numbers only."""

REVIEW_PROMPT = """\
You help a blind person understand a meeting that was recorded. You receive notes on every \
screen shown, the transcript with speaker names, some full screenshots chosen for this \
question, and the user's question. Times are clock times, with the time since the recording \
started in brackets.
Rules:
- Use only what is in the screenshots, the notes and the transcript. Never invent numbers, \
names or text.
- The user cannot see the screen: when they ask about a page, read out its important \
content (titles, items, numbers, names, dates), not just what it is.
- Say who said something when it helps. "Speaker" means the name was not visible.
- If something is not legible or not shown, say so.
- Answer in at most five short sentences of plain language. No markdown, lists or emoji. \
Your answer is read aloud.
- Earlier questions and answers may come before the current question; use them to \
understand follow-ups.
""" + LANGUAGE_RULE


@dataclass
class Group:
    """One noted screen, or a run of quick screens with no note (scrolling, typing)."""
    shots: list[Shot]

    @property
    def note(self) -> str:
        return self.shots[0].note if len(self.shots) == 1 else ""


def group_shots(shots: Sequence[Shot]) -> list[Group]:
    groups: list[Group] = []
    for s in shots:
        if not s.note and groups and not groups[-1].shots[-1].note:
            groups[-1].shots.append(s)
        else:
            groups.append(Group([s]))
    return groups


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _since(ts: float, started: float) -> str:
    s = max(0, round(ts - started))
    return f"{s // 60}:{s % 60:02d}"


def _when(a: float, b: float, started: float) -> str:
    return f"{_clock(a)} to {_clock(b)} (from {_since(a, started)} to {_since(b, started)})"


def describe_groups(groups: Sequence[Group], started: float) -> str:
    lines = []
    for i, g in enumerate(groups, 1):
        a, b = g.shots[0].first_ts, g.shots[-1].last_ts
        what = g.note or (f"{len(g.shots)} quick screen changes (scrolling or typing), no note"
                          if len(g.shots) > 1 else "screen shown briefly, no note")
        lines.append(f"{i}. {_when(a, b, started)}: {what}")
    return "\n".join(lines)


def format_transcript(lines: Sequence[TranscriptLine], started: float) -> str:
    return "\n".join(f"[{_clock(l.start)} ({_since(l.start, started)})] {l.speaker or UNKNOWN_SPEAKER}: {l.text}"
                     for l in lines) or "(no speech captured)"


def parse_picks(reply: str, count: int) -> list[int]:
    """Valid 1-based screen numbers from the model's reply, in order, without repeats."""
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S | re.I)
    picks: list[int] = []
    for m in re.findall(r"\d+", reply):
        n = int(m)
        if 1 <= n <= count and n not in picks:
            picks.append(n)
    return picks


def expand_picks(groups: Sequence[Group], picks: Sequence[int], max_frames: int) -> list[Shot]:
    """Shots for the chosen groups, at most max_frames, in time order; runs give spread-out shots."""
    if not groups or max_frames <= 0:
        return []
    if not picks:  # nothing usable chosen: screens spread over the whole recording
        return [g.shots[0] for g in select_frames(list(groups), max_frames)]  # type: ignore[arg-type]
    queues = [list(select_frames(groups[p - 1].shots, max_frames)) for p in picks]  # type: ignore[arg-type]
    chosen: list[Shot] = []
    while len(chosen) < max_frames and any(queues):
        for q in queues:
            if q and len(chosen) < max_frames:
                chosen.append(q.pop(len(q) // 2))
    return sorted(chosen, key=lambda s: s.first_ts)


def transcript_for(lines: Sequence[TranscriptLine], shots: Sequence[Shot],
                   max_chars: int = TRANSCRIPT_CHARS, pad: float = PAD_SECONDS) -> list[TranscriptLine]:
    """The whole transcript if it fits, else the lines near the chosen screens."""
    def size(ls):
        return sum(len(l.text) + len(l.speaker) + 20 for l in ls)

    if size(lines) <= max_chars:
        return list(lines)
    near = [l for l in lines if any(l.end >= s.first_ts - pad and l.start <= s.last_ts + pad for s in shots)]
    out, used = [], 0
    for l in near:
        used += len(l.text) + len(l.speaker) + 20
        if used > max_chars:
            break
        out.append(l)
    return out


async def choose_shots(client: ModelClient, groups: Sequence[Group], question: str,
                       transcript: str, started: float, max_frames: int) -> list[Shot]:
    """Step 1: Nemotron names the screens that answer the question."""
    if len(groups) <= 1:
        return expand_picks(groups, [1] if groups else [], max_frames)
    messages = [{"role": "system", "content": PICK_PROMPT.format(n=max_frames)},
                {"role": "user", "content":
                    f"Screens:\n{describe_groups(groups, started)}\n\n"
                    f"Transcript:\n{transcript[:TRANSCRIPT_CHARS]}\n\nQuestion: {question.strip()}"}]
    reply, think = "", ThinkFilter()
    try:
        async with aclosing(client.stream_chat(messages, temperature=0, max_tokens=60)) as stream:
            async for delta in stream:
                reply += think.feed(delta)
        reply += think.flush()
    except Exception:  # noqa: BLE001 - fall back to screens spread over the recording
        reply = ""
    return expand_picks(groups, parse_picks(reply, len(groups))[:max_frames], max_frames)


def build_review_messages(groups: Sequence[Group], shots: Sequence[Shot], lines: Sequence[TranscriptLine],
                          question: str, started: float,
                          history: Sequence[tuple[str, str]] = ()) -> list[dict]:
    content: list[dict] = [
        {"type": "text", "text": f"Recording started at {_clock(started)}.\n\n"
                                 f"Notes on every screen shown:\n{describe_groups(groups, started)}"},
        {"type": "text", "text": f"Transcript:\n{format_transcript(lines, started)}"},
    ]
    for s in shots:
        content.append({"type": "text", "text": f"Screenshot {s.number}, on screen {_when(s.first_ts, s.last_ts, started)}:"})
        content.append(image_block(s.jpeg))
    content.append({"type": "text", "text": question_text(question, lines)})
    earlier: list[dict] = []
    for q, a in history:
        earlier += [{"role": "user", "content": f"Question: {q.strip()}"},
                    {"role": "assistant", "content": a}]
    return [{"role": "system", "content": REVIEW_PROMPT}, *earlier, {"role": "user", "content": content}]


async def answer_from_recording(client: ModelClient, shots: Sequence[Shot], lines: Sequence[TranscriptLine],
                                question: str, started: float, *,
                                speak: Callable[[str, bool], None] | None = None,
                                max_frames: int = 4, log: LatencyLog | None = None,
                                question_end: float | None = None,
                                history: Sequence[tuple[str, str]] = ()) -> AnswerResult:
    """Pick the screens (step 1), then answer from them (step 2), spoken as it streams."""
    t0 = question_end if question_end is not None else time.perf_counter()
    groups = group_shots(shots)
    overview = format_transcript(lines, started)
    if len(overview) > TRANSCRIPT_CHARS:  # long meeting: every line, shortened, so all of it fits
        overview = format_transcript([TranscriptLine(l.start, l.end, l.text[:60], l.speaker) for l in lines],
                                     started)
    chosen = await choose_shots(client, groups, question, overview, started, max_frames)
    if log:
        log.write("review_pick", latency_s=round(time.perf_counter() - t0, 3), screens=len(shots),
                  groups=len(groups), picked=len(chosen))
    near = transcript_for(lines, chosen)
    messages = build_review_messages(groups, chosen, near, question, started, history)
    frames = [Frame(s.last_ts, s.jpeg, s.width, s.height, s.first_ts) for s in chosen]
    return await answer_question(client, Snapshot(time.time(), frames, near), question, speak=speak,
                                 max_frames=max_frames, log=log, question_end=t0, messages=messages,
                                 max_sentences=MAX_SENTENCES, log_event="answer_recording")
