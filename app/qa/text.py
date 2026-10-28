"""Streaming text helpers: drop <think> blocks, split into sentences, clean for speech."""

from __future__ import annotations

import re


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest end of `text` that is a start of `tag`."""
    for k in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:k]):
            return k
    return 0


class ThinkFilter:
    """Removes <think>...</think> spans from a stream, even when tags are split across chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while True:
            tag = self.CLOSE if self._inside else self.OPEN
            i = self._buf.find(tag)
            if i == -1:
                keep = _partial_suffix(self._buf, tag)
                if not self._inside:
                    out.append(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep:]
                return "".join(out)
            if not self._inside:
                out.append(self._buf[:i])
            self._buf = self._buf[i + len(tag):]
            self._inside = not self._inside

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf = ""
        return rest


_SENTENCE_END = re.compile(r"[.!?।](?=\s)")  # । = "।", the Hindi full stop


class SentenceSplitter:
    """Yields whole sentences as soon as they are complete, so the first one can be spoken early."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out = []
        while (m := _SENTENCE_END.search(self._buf)):
            sentence = self._buf[: m.end()].strip()
            self._buf = self._buf[m.end():]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if rest else []


_MARKDOWN = re.compile(r"[*_`#>|]+")


def clean_for_speech(text: str) -> str:
    text = re.sub(r"^\s*[-•]\s+", "", text)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()
