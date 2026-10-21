"""Speech output: the running screen reader (NVDA, JAWS) if there is one, else Windows SAPI."""

from __future__ import annotations

import queue
import threading
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
