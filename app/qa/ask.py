"""Build the question prompt from the rolling window, stream the answer, speak it early."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass

from app.eval.latency import LatencyLog
from app.llm.client import ModelClient, image_block
from app.memory.buffer import Frame, Snapshot
from app.qa.text import SentenceSplitter, ThinkFilter, clean_for_speech

MAX_SENTENCES = 2

SYSTEM_PROMPT = """\
You help a blind person follow a video meeting. You receive screenshots of the \
shared screen from the last 30 seconds (oldest first, each with how long ago it \
was taken), a transcript of recent speech, and the user's question.
Rules:
- Use only what is in the transcript and the screenshots. Never invent numbers, names or text.
- If something is not legible or not shown, say so.
- Label estimates as estimates.
- Answer in at most two short sentences of plain language. No markdown, lists or emoji. \
Your answer is read aloud.
- The transcript is automatic and may contain errors. If it does not cover the question, \
answer from the screen and say that."""


def select_frames(frames: list[Frame], n: int) -> list[Frame]:
    """Up to n frames spread over the window, always including the latest."""
    if n <= 0 or not frames:
        return []
    if len(frames) <= n:
        return list(frames)
    if n == 1:
        return [frames[-1]]
    step = (len(frames) - 1) / (n - 1)
    return [frames[round(i * step)] for i in range(n)]


def _ago(now: float, ts: float) -> str:
    return f"{max(0, round(now - ts))} s ago"


def build_messages(snapshot: Snapshot, question: str, max_frames: int = 3) -> list[dict]:
    now = snapshot.now
    if snapshot.transcript:
        transcript = "\n".join(f"[{_ago(now, line.end)}] {line.text}" for line in snapshot.transcript)
    else:
        transcript = "(no speech captured)"

    content: list[dict] = [{"type": "text", "text": f"Transcript of the last 30 seconds:\n{transcript}"}]
    frames = select_frames(snapshot.frames, max_frames)
    if not frames:
        content.append({"type": "text", "text": "(no screenshots captured)"})
    for frame in frames:
        if frame.first_ts and frame.ts - frame.first_ts >= 1:
            when = f"on screen from {_ago(now, frame.first_ts)} until {_ago(now, frame.ts)}"
        else:
            when = f"taken {_ago(now, frame.ts)}"
        content.append({"type": "text", "text": f"Screenshot {when}:"})
        content.append(image_block(frame.jpeg))
    content.append({"type": "text", "text": f"Question: {question.strip()}"})

    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}]


@dataclass
class AnswerResult:
    text: str
    first_sentence_s: float | None
    total_s: float


async def answer_question(client: ModelClient, snapshot: Snapshot, question: str, *,
                          speak: Callable[[str, bool], None] | None = None,
                          max_frames: int = 3, log: LatencyLog | None = None,
                          question_end: float | None = None) -> AnswerResult:
    """Stream an answer. Each finished sentence goes to `speak(text, interrupt)` right away.

    `question_end` is a perf_counter() time for when the user finished asking, so the
    logged latency covers prompt building and upload too.
    """
    t0 = question_end if question_end is not None else time.perf_counter()
    messages = build_messages(snapshot, question, max_frames)
    think, splitter = ThinkFilter(), SentenceSplitter()
    sentences: list[str] = []
    first_s: float | None = None

    def emit(raw: str) -> bool:
        nonlocal first_s
        text = clean_for_speech(raw)
        if not text:
            return False
        if first_s is None:
            first_s = time.perf_counter() - t0
        if speak:
            speak(text, not sentences)
        sentences.append(text)
        return len(sentences) >= MAX_SENTENCES

    async with aclosing(client.stream_chat(messages, temperature=0.2, max_tokens=200)) as stream:
        done = False
        async for delta in stream:
            for s in splitter.feed(think.feed(delta)):
                if emit(s):
                    done = True
                    break
            if done:
                break
        if not done:
            for s in splitter.feed(think.flush()) + splitter.flush():
                if emit(s):
                    break

    if not sentences:
        emit("Sorry, I got no answer.")
    total = time.perf_counter() - t0
    if log:
        log.write("answer", first_sentence_s=round(first_s or total, 3), total_s=round(total, 3),
                  frames=len(select_frames(snapshot.frames, max_frames)),
                  transcript_lines=len(snapshot.transcript))
    return AnswerResult(" ".join(sentences), first_s, total)
