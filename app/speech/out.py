"""Speech output: offline human voices (Piper) if chosen, else the running screen reader
(NVDA, JAWS) if there is one, else Windows SAPI."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
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


# Unicode blocks of Indian scripts -> language code of the Piper voice that reads them.
# Anything else (Latin letters) is read by the English voice.
_SCRIPTS = [(0x0900, 0x097F, "hi"), (0x0980, 0x09FF, "bn"), (0x0A80, 0x0AFF, "gu"),
            (0x0B80, 0x0BFF, "ta"), (0x0C00, 0x0C7F, "te"), (0x0C80, 0x0CFF, "kn"),
            (0x0D00, 0x0D7F, "ml")]


def _script_lang(ch: str) -> str | None:
    """Language of one character, or None for spaces, digits and punctuation."""
    code = ord(ch)
    for lo, hi, lang in _SCRIPTS:
        if lo <= code <= hi:
            return lang
    return "en" if ch.isalpha() else None


def split_by_language(text: str) -> list[tuple[str, str]]:
    """Cut text into (language, part) runs by script, so "Ravi said வணக்கம்" is read by
    two voices. Spaces, digits and punctuation stay with the run they are in."""
    runs: list[list[str]] = []
    for ch in text:
        lang = _script_lang(ch)
        if runs and (lang is None or lang == runs[-1][0]):
            runs[-1][1] += ch
        elif lang is None:
            runs.append(["en", ch])
        else:
            runs.append([lang, ch])
    return [(lang, part.strip()) for lang, part in runs if part.strip()]


class PiperSpeaker:
    """Human-sounding offline voices (Piper, no API). One voice file per language; each part of
    the text goes to the voice for its script, so one answer can mix English and Tamil.

    Voices are loaded on a background thread (about 4 s each); speech waits only for the
    voice it needs. Interrupt and stop take effect within about a tenth of a second.
    """

    name = "Human voice"
    BLOCK_S = 0.1

    def __init__(self, voice_dir: str | Path):
        files = sorted(Path(voice_dir).glob("*.onnx"))
        self._files: dict[str, Path] = {}
        for f in files:
            if Path(f"{f}.json").exists():
                self._files.setdefault(f.name[:2].lower(), f)  # en_US-lessac-medium.onnx -> en
        if not self._files:
            raise RuntimeError(f"no Piper voices in {voice_dir}")
        self.default = "en" if "en" in self._files else next(iter(self._files))
        self._voices: dict[str, object] = {}
        self._loaded = {lang: threading.Event() for lang in self._files}
        self._queue: queue.Queue[tuple[str, int]] = queue.Queue()
        self._gen = 0  # bumped by interrupt/stop; older queued speech is dropped
        self._lock = threading.Lock()
        threading.Thread(target=self._load_all, name="piper-load", daemon=True).start()
        threading.Thread(target=self._run, name="piper", daemon=True).start()

    def _load(self, lang: str) -> None:
        from piper import PiperVoice

        try:
            self._voices[lang] = PiperVoice.load(self._files[lang])
        except Exception as e:  # noqa: BLE001 - a broken voice falls back to the default one
            print(f"Piper voice {self._files[lang].name} failed to load: {e}")
        self._loaded[lang].set()

    def _load_all(self) -> None:
        # The default voice first, so the first words come quickly; then the rest side by side.
        self._load(self.default)
        for lang in self._files:
            if lang != self.default:
                threading.Thread(target=self._load, args=(lang,), name=f"piper-load-{lang}",
                                 daemon=True).start()

    def _voice(self, lang: str):
        if lang not in self._files:
            lang = self.default
        self._loaded[lang].wait(60)
        return self._voices.get(lang) or self._voices.get(self.default)

    def speak(self, text: str, interrupt: bool = False) -> None:
        with self._lock:
            if interrupt:
                self._gen += 1
            self._queue.put((text, self._gen))

    def stop(self) -> None:
        with self._lock:
            self._gen += 1

    def _run(self) -> None:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        streams: dict[int, object] = {}
        while True:
            text, gen = self._queue.get()
            for lang, part in split_by_language(text):
                if gen != self._gen:
                    break
                voice = self._voice(lang)
                if voice is None:
                    break
                try:
                    for chunk in voice.synthesize(part):
                        if gen != self._gen:
                            break
                        rate = chunk.sample_rate
                        if rate not in streams:
                            streams[rate] = pa.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                                    output=True)
                        data = chunk.audio_int16_bytes
                        step = int(rate * self.BLOCK_S) * 2
                        for i in range(0, len(data), step):
                            if gen != self._gen:
                                break
                            streams[rate].write(data[i:i + step])
                except Exception as e:  # noqa: BLE001 - one bad sentence must not stop the voice
                    print(f"Piper could not speak: {e}")


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


def make_speaker(echo: bool = True, settings=None) -> Speaker:
    """The human voice if chosen in settings (ANSWER_VOICE=human) and voices are installed,
    else the screen reader, else the Windows voice."""
    inner: Speaker
    factories = [ScreenReaderSpeaker, SapiSpeaker]
    if settings is not None and settings.answer_voice == "human":
        factories.insert(0, lambda: PiperSpeaker(settings.voice_dir))
    for factory in factories:
        try:
            inner = factory()
            break
        except Exception:  # noqa: BLE001 - try the next output
            continue
    else:
        return PrintSpeaker()
    return TeeSpeaker(inner) if echo else inner
