"""Build the question prompt from the rolling window, stream the answer, speak it early."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass

from app.eval.latency import LatencyLog
from app.llm.client import ModelClient, image_block
from app.memory.buffer import Frame, Snapshot, TranscriptLine
from app.memory.transcript import UNKNOWN_SPEAKER
from app.qa.text import SentenceSplitter, ThinkFilter, clean_for_speech
from app.speech.out import split_by_language

MAX_SENTENCES = 2

# Answers are read by the voice for their script (app/speech/out.py), so answering in the
# meeting's language makes a Tamil meeting answered in the Tamil voice.
LANGUAGE_RULE = """\
- Answer in the main language spoken in the meeting transcript (for example Tamil if the \
meeting is in Tamil, Hindi if it is in Hindi), even when the question is in English. With no \
speech in the transcript, answer in the language of the question. Keep names of apps, code \
and technical words as they are."""

SYSTEM_PROMPT = """\
You help a blind person follow a video meeting. You receive a transcript of the \
meeting with speaker names, screenshots of the screen from the last 30 seconds \
(oldest first, the last one taken when the question was asked), and the user's question.
Rules:
- Use only what is in the transcript and the screenshots. Never invent numbers, names or text.
- Say who said something when it helps. Speaker names are read from the meeting screen \
and may be wrong; "Speaker" means the name was not visible.
- If something is not legible or not shown, say so.
- Label estimates as estimates.
- Be very brief and to the point: give only what matters, no filler, no repeating the question. \
At most two short sentences of plain language. No markdown, lists or emoji. Your answer is read aloud.
- For "what happened" questions about a time span, start with the span, e.g. \
"Last 30 sec: Priya said the launch moves to Friday. A WhatsApp chat with Ravi opened and was scrolled." \
Name the app or window, whose chat or file it is, and what was done in it.
- Earlier questions and answers may come before the current question. Use them to understand \
follow-ups ("what did they scroll?" refers to the last answer). Answer a follow-up in as few \
words as possible, e.g. "Messages."
- The transcript is automatic and may contain errors. If it does not cover the question, \
answer from the screen and say that.
""" + LANGUAGE_RULE


LANGUAGE_NAMES = {"ta": "Tamil", "hi": "Hindi", "te": "Telugu", "ml": "Malayalam", "kn": "Kannada",
                  "bn": "Bengali", "gu": "Gujarati"}


def meeting_language(lines: Sequence[TranscriptLine]) -> str | None:
    """The Indian language most of the transcript is written in, or None (English or nothing).
    Told to Nemotron next to the question; a general rule alone lost to the English examples."""
    counts: dict[str, int] = {}
    for line in lines:
        for lang, part in split_by_language(line.text):
            counts[lang] = counts.get(lang, 0) + sum(c.isalpha() for c in part)
    total = sum(counts.values())
    lang, n = max(((k, v) for k, v in counts.items() if k != "en"), key=lambda kv: kv[1], default=("en", 0))
    return LANGUAGE_NAMES.get(lang) if total and n >= 0.3 * total else None


def question_text(question: str, lines: Sequence[TranscriptLine]) -> str:
    lang = meeting_language(lines)
    return f"Question: {question.strip()}" + (f"\nAnswer in {lang}." if lang else "")


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
    seconds = max(0, round(now - ts))
    return f"{seconds} s ago" if seconds < 120 else f"{round(seconds / 60)} min ago"


def build_messages(snapshot: Snapshot, question: str, max_frames: int = 3,
                   history: Sequence[tuple[str, str]] = ()) -> list[dict]:
    """`history` is earlier (question, answer) pairs, oldest first, sent as text only
    so follow-up questions make sense."""
    now = snapshot.now
    if snapshot.transcript:
        transcript = "\n".join(f"[{_ago(now, line.end)}] {line.speaker or UNKNOWN_SPEAKER}: {line.text}"
                               for line in snapshot.transcript)
    else:
        transcript = "(no speech captured)"

    content: list[dict] = [{"type": "text", "text": f"Meeting transcript:\n{transcript}"}]
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
    content.append({"type": "text", "text": question_text(question, snapshot.transcript)})

    earlier: list[dict] = []
    for q, a in history:
        earlier += [{"role": "user", "content": f"Question: {q.strip()}"},
                    {"role": "assistant", "content": a}]
    return [{"role": "system", "content": SYSTEM_PROMPT}, *earlier,
            {"role": "user", "content": content}]


@dataclass
class AnswerResult:
    text: str
    first_sentence_s: float | None
    total_s: float


async def answer_question(client: ModelClient, snapshot: Snapshot, question: str, *,
                          speak: Callable[[str, bool], None] | None = None,
                          max_frames: int = 3, log: LatencyLog | None = None,
                          question_end: float | None = None,
                          history: Sequence[tuple[str, str]] = (),
                          messages: list[dict] | None = None, max_sentences: int = MAX_SENTENCES,
                          log_event: str = "answer") -> AnswerResult:
    """Stream an answer. Each finished sentence goes to `speak(text, interrupt)` right away.

    `question_end` is a perf_counter() time for when the user finished asking, so the
    logged latency covers prompt building and upload too. `messages` replaces the live
    prompt (questions about a finished recording build their own, app/qa/review.py).
    """
    t0 = question_end if question_end is not None else time.perf_counter()
    if messages is None:
        messages = build_messages(snapshot, question, max_frames, history)
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
        return len(sentences) >= max_sentences

    async with aclosing(client.stream_chat(messages, temperature=0.2,
                                           max_tokens=100 * max_sentences)) as stream:
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
        log.write(log_event, first_sentence_s=round(first_s or total, 3), total_s=round(total, 3),
                  frames=len(select_frames(snapshot.frames, max_frames)),
                  transcript_lines=len(snapshot.transcript))
    return AnswerResult(" ".join(sentences), first_s, total)
