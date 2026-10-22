"""Speech output: the running screen reader (NVDA, JAWS) if there is one, else Windows SAPI."""

from __future__ import annotations

import queue
import threading
import time
from typing import Protocol

SVSF_ASYNC = 1
SVSF_PURGE_BEFORE_SPEAK = 2


class Speaker(Protocol):
    name: str
    def speak(self, text: str, interrupt: bool = False) -> None: ...
    def stop(self) -> None: ...


class ScreenReaderSpeaker:
    """accessible_output2 talks to NVDA/JAWS directly, so the user's own voice and rate are used."""

    def __init__(self):
        from accessible_output2.outputs.auto import Auto

        self._out = Auto()
        active = [o for o in self._out.outputs if o.is_active() and o.name != "sapi5"]
        if not active:
            raise RuntimeError("no screen reader running")
        self.name = active[0].name

    def speak(self, text: str, interrupt: bool = False) -> None:
        self._out.output(text, interrupt=interrupt)

    def stop(self) -> None:
        self._out.output("", interrupt=True)


class SapiSpeaker:
    """Windows SAPI voice on its own COM thread. Non-blocking; interrupt purges queued speech."""

    name = "Windows voice"

    def __init__(self):
        self._queue: queue.Queue[tuple[str, int]] = queue.Queue()
        ready = threading.Event()
        self._error: Exception | None = None

        def run():
            try:
                import comtypes
                import comtypes.client

                comtypes.CoInitialize()
                voice = comtypes.client.CreateObject("SAPI.SpVoice")
            except Exception as e:  # noqa: BLE001
                self._error = e
                ready.set()
                return
            ready.set()
            while True:
                text, flags = self._queue.get()
                voice.Speak(text, flags)

        threading.Thread(target=run, name="sapi", daemon=True).start()
        ready.wait(10)
        if self._error:
            raise RuntimeError(f"SAPI unavailable: {self._error}")

    def speak(self, text: str, interrupt: bool = False) -> None:
        self._queue.put((text, SVSF_ASYNC | (SVSF_PURGE_BEFORE_SPEAK if interrupt else 0)))

    def stop(self) -> None:
        self._queue.put(("", SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK))


class PrintSpeaker:
    name = "console"

    def speak(self, text: str, interrupt: bool = False) -> None:
        print(f"[speak] {text}", flush=True)

    def stop(self) -> None:
        pass


class TeeSpeaker:
    """Speaks and also prints, so a sighted developer can follow along."""

    def __init__(self, inner: Speaker):
        self.inner = inner
        self.name = inner.name

    def speak(self, text: str, interrupt: bool = False) -> None:
        print(f"> {text}", flush=True)
        self.inner.speak(text, interrupt)

    def stop(self) -> None:
        self.inner.stop()


class SelfSpeechGuard:
    """Remembers when the app itself is talking, so loopback capture can skip its own voice.

    Screen readers do not report when they finish, so the end is estimated from the text
    length at a fairly slow rate; skipping a little meeting audio beats transcribing ourselves.
    """

    CHARS_PER_SECOND = 12.0
    MARGIN_S = 1.0

    def __init__(self, inner: Speaker, clock=time.time):
        self.inner = inner
        self.name = inner.name
        self.clock = clock
        self._spans: list[list[float]] = []  # [start, end] of each thing said, oldest first
        self._lock = threading.Lock()

    def speak(self, text: str, interrupt: bool = False) -> None:
        with self._lock:
            now = self.clock()
            if interrupt:
                self._cut(now)
            start = max(now, self._spans[-1][1]) if self._spans else now
            self._spans.append([start, start + len(text) / self.CHARS_PER_SECOND])
            self._spans = [s for s in self._spans if s[1] > now - 300]
        self.inner.speak(text, interrupt)

    def stop(self) -> None:
        with self._lock:
            self._cut(self.clock())
        self.inner.stop()

    def _cut(self, now: float) -> None:
        """Queued speech is dropped: nothing said ends later than now."""
        self._spans = [[s, min(e, now)] for s, e in self._spans if s < now]

    def overlaps(self, start: float, end: float) -> bool:
        """True if [start, end] overlaps a time when the app was speaking (with a margin)."""
        with self._lock:
            return any(start < e + self.MARGIN_S and end > s for s, e in self._spans)


def make_speaker(echo: bool = True) -> Speaker:
    inner: Speaker
    for factory in (ScreenReaderSpeaker, SapiSpeaker):
        try:
            inner = factory()
            break
        except Exception:  # noqa: BLE001 - try the next output
            continue
    else:
        return PrintSpeaker()
    return TeeSpeaker(inner) if echo else inner
