"""Wiring and lifecycle.

    python -m app.main            # uses .env
    python -m app.main --stub     # no network, fake answers

Type a question in the console and press Enter, or use the global hotkeys.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from app.config import load_settings
from app.eval.latency import LatencyLog
from app.input import hotkeys
from app.llm.client import StubClient, make_client
from app.memory.buffer import RollingBuffer
from app.qa.ask import answer_question
from app.screen.capture import ScreenCapture
from app.speech.out import make_speaker
from app.stt.transcribe import Transcriber

DEFAULT_QUESTION = "What is on the screen and what was just said about it?"


class App:
    def __init__(self, settings, client, speaker):
        self.settings = settings
        self.client = client
        self.speaker = speaker
        self.log = LatencyLog(settings.latency_log)
        self.buffer = RollingBuffer(settings.window_seconds)
        self.screen = ScreenCapture(self.buffer, settings.frame_interval,
                                    settings.frame_max_side, settings.jpeg_quality)
        self.transcriber = Transcriber(client, self.buffer, self.log)
        self.audio = None
        self.paused = False
        self.last_answer = ""
        self._current: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    # --- capture ---------------------------------------------------------

    def start_audio(self) -> None:
        from app.audio.loopback import LoopbackCapture
        from app.audio.vad import SpeechSegmenter, make_vad

        vad = make_vad()
        def on_segment(seg):
            if not self.paused:
                self.loop.call_soon_threadsafe(self.transcriber.queue.put_nowait, seg)
        self.audio = LoopbackCapture(SpeechSegmenter(vad), on_segment)
        self.audio.start()
        print(f"Audio: {self.audio.device_name}, VAD: {type(vad).__name__}")

    # --- actions (called from the hotkey thread or the console) -----------

    def ask(self, question: str = DEFAULT_QUESTION) -> None:
        question_end = time.perf_counter()
        self.loop.call_soon_threadsafe(self._start_answer, question, question_end)

    def _start_answer(self, question: str, question_end: float) -> None:
        if self._current and not self._current.done():
            self._current.cancel()
        self.speaker.stop()
        self._current = self.loop.create_task(self._answer(question, question_end))

    async def _answer(self, question: str, question_end: float) -> None:
        try:
            result = await answer_question(
                self.client, self.buffer.snapshot(), question, speak=self.speaker.speak,
                max_frames=self.settings.max_frames_per_question, log=self.log,
                question_end=question_end)
            self.last_answer = result.text
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - say it, never crash
            self.log.write("answer_error", error=type(e).__name__)
            self.speaker.speak(f"Sorry, the answer failed: {type(e).__name__}.", True)

    def repeat(self) -> None:
        self.speaker.speak(self.last_answer or "No answer yet.", True)

    def stop_speaking(self) -> None:
        self.speaker.stop()

    def status(self) -> None:
        snap = self.buffer.snapshot()
        parts = [f"{len(snap.frames)} screenshots and {len(snap.transcript)} transcript lines "
                 f"from the last {int(self.settings.window_seconds)} seconds."]
        if self.paused:
            parts.append("Capture is paused.")
        if self.screen.last_error:
            parts.append("Screen capture has an error.")
        if self.audio is None:
            parts.append("System audio is not being captured.")
        if self.transcriber.last_error:
            parts.append("Transcription has an error.")
        self.speaker.speak(" ".join(parts), True)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.screen.stop()
            self.buffer.clear()
            self.speaker.speak("Capture paused and memory cleared.", True)
        else:
            self.screen = ScreenCapture(self.buffer, self.settings.frame_interval,
                                        self.settings.frame_max_side, self.settings.jpeg_quality)
            self.screen.start()
            self.speaker.speak("Capture resumed.", True)

    # --- lifecycle --------------------------------------------------------

    async def run(self, use_hotkeys: bool = True) -> None:
        self.loop = asyncio.get_running_loop()
        self.screen.start()
        try:
            self.start_audio()
        except Exception as e:  # noqa: BLE001
            print(f"System audio capture unavailable: {e}")
            self.speaker.speak("System audio capture is not available. Screen questions still work.")
        stt_task = asyncio.create_task(self.transcriber.run())

        unregister = None
        if use_hotkeys:
            try:
                unregister = hotkeys.register({
                    "ask": self.ask, "repeat": self.repeat, "stop": self.stop_speaking,
                    "status": self.status, "pause": self.toggle_pause,
                })
            except Exception as e:  # noqa: BLE001
                print(f"Hotkeys unavailable: {e}")

        mode = "test mode with fake answers" if isinstance(self.client, StubClient) else "ready"
        self.speaker.speak(f"Meeting assistant {mode}. Press {hotkeys.describe()}.")
        print("Type a question and press Enter. Empty line asks the default question. Ctrl+C to quit.")
        try:
            while True:
                line = await self.loop.run_in_executor(None, sys.stdin.readline)
                if not line:  # stdin closed
                    await asyncio.Event().wait()
                question = line.strip()
                if question.lower() in {"q", "quit", "exit"}:
                    break
                self.ask(question or DEFAULT_QUESTION)
        finally:
            if unregister:
                unregister()
            stt_task.cancel()
            self.screen.stop()
            if self.audio:
                self.audio.stop()
            self.buffer.clear()


def main() -> None:
    ap = argparse.ArgumentParser(description="Meeting Screen Assistant")
    ap.add_argument("--stub", action="store_true", help="fake model responses, no network")
    ap.add_argument("--no-hotkeys", action="store_true")
    args = ap.parse_args()

    if getattr(sys, "frozen", False):
        # Packaged .exe: read .env and write logs next to the exe, wherever it was started from.
        os.chdir(Path(sys.executable).parent)
    settings = load_settings()
    client = StubClient() if args.stub else make_client(settings)
    app = App(settings, client, make_speaker())
    try:
        asyncio.run(app.run(use_hotkeys=not args.no_hotkeys))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
