"""Wiring and lifecycle.

    python -m app.main              # window (first run asks for the API key)
    python -m app.main --stub       # fake answers, no network
    python -m app.main --console    # no window: type questions in the console

Global hotkeys work in both modes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

from app.config import load_settings, settings_file
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
        self._stt_task: asyncio.Task | None = None
        self._unregister_hotkeys = None
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

    # --- actions (thread-safe: called from the window, hotkey thread or console) ---

    def ask(self, question: str = "") -> None:
        question_end = time.perf_counter()
        if not self.loop:
            self.speaker.speak("Still starting, please ask again in a moment.", True)
            return
        self.loop.call_soon_threadsafe(self._start_answer, question or DEFAULT_QUESTION, question_end)

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
            self.speaker.speak(f"Sorry, the answer failed: {type(e).__name__}. {str(e)[:150]}", True)

    def repeat(self) -> None:
        self.speaker.speak(self.last_answer or "No answer yet.", True)

    def stop_speaking(self) -> None:
        self.speaker.stop()

    def status_text(self) -> str:
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
            parts.append(f"Transcription error: {type(self.transcriber.last_error).__name__}.")
        return " ".join(parts)

    def status(self) -> None:
        self.speaker.speak(self.status_text(), True)

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

    async def start(self, use_hotkeys: bool = True) -> None:
        self.loop = asyncio.get_running_loop()
        self.screen.start()
        try:
            self.start_audio()
        except Exception as e:  # noqa: BLE001
            print(f"System audio capture unavailable: {e}")
            self.speaker.speak("System audio capture is not available. Screen questions still work.")
        self._stt_task = asyncio.create_task(self.transcriber.run())
        if use_hotkeys:
            try:
                self._unregister_hotkeys = hotkeys.register({
                    "ask": self.ask, "repeat": self.repeat, "stop": self.stop_speaking,
                    "status": self.status, "pause": self.toggle_pause,
                })
            except Exception as e:  # noqa: BLE001
                print(f"Hotkeys unavailable: {e}")

    async def stop(self) -> None:
        if self._unregister_hotkeys:
            self._unregister_hotkeys()
            self._unregister_hotkeys = None
        for task in (self._current, self._stt_task):
            if task and not task.done():
                task.cancel()
        self.screen.stop()
        if self.audio:
            self.audio.stop()
            self.audio = None
        self.buffer.clear()

    def greeting(self) -> str:
        mode = "test mode with fake answers" if isinstance(self.client, StubClient) else "ready"
        return f"Meeting assistant {mode}."

    async def run_console(self, use_hotkeys: bool = True) -> None:
        await self.start(use_hotkeys)
        self.speaker.speak(f"{self.greeting()} Press {hotkeys.describe()}.")
        print("Type a question and press Enter. Empty line asks the default question. Ctrl+C to quit.")
        try:
            while True:
                line = await self.loop.run_in_executor(None, sys.stdin.readline)
                if not line:  # stdin closed
                    await asyncio.Event().wait()
                question = line.strip()
                if question.lower() in {"q", "quit", "exit"}:
                    break
                self.ask(question)
        finally:
            await self.stop()


class BackgroundRunner:
    """Runs an App's asyncio loop on its own thread, so a window can own the main thread."""

    def __init__(self, app: App, use_hotkeys: bool = True):
        self.app = app
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(app.start(use_hotkeys))
            started.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, name="app-loop", daemon=True)
        self.thread.start()
        started.wait(30)

    def stop(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self.app.stop(), self.loop).result(10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(10)


class LogSpeaker:
    """Speaks and also sends each line to a callback (the window's log)."""

    def __init__(self, inner, on_text):
        self.inner = inner
        self.name = inner.name
        self.on_text = on_text

    def speak(self, text: str, interrupt: bool = False) -> None:
        self.on_text(text)
        self.inner.speak(text, interrupt)

    def stop(self) -> None:
        self.inner.stop()


def make_app(settings, stub: bool, speaker) -> App:
    client = StubClient() if stub else make_client(settings)
    return App(settings, client, speaker)


def run_gui(force_stub: bool, use_hotkeys: bool) -> None:
    import wx

    from app.ui.main_window import MainWindow
    from app.ui.settings_dialog import SettingsDialog

    wx_app = wx.App()

    def load_or_ask(first_run: bool, error: str = ""):
        while True:
            if first_run or error:
                dlg = SettingsDialog(None, first_run=first_run, error=error)
                ok = dlg.ShowModal() == wx.ID_OK
                dlg.Destroy()
                if not ok:
                    return None
            try:
                return load_settings()
            except ValueError as e:
                first_run, error = False, f"Please check the settings: {e}"

    needs_setup = not force_stub and not settings_file().exists() and "LLM_PROVIDER" not in os.environ
    settings = load_or_ask(needs_setup)
    if settings is None:
        return

    state: dict = {}
    frame: MainWindow | None = None

    def log(text: str) -> None:
        if frame:
            wx.CallAfter(frame.add_log, text)

    speaker = LogSpeaker(make_speaker(echo=False), log)

    def start(s) -> None:
        state["app"] = make_app(s, force_stub, speaker)
        state["runner"] = BackgroundRunner(state["app"], use_hotkeys)
        app = state["app"]
        model = "fake answers" if isinstance(app.client, StubClient) else \
            f"answers {s.model}, speech {s.stt_model}"
        state["summary"] = f"{s.provider}: {model}"
        speaker.speak(f"{app.greeting()} Type a question and press Enter, "
                      f"or press control alt space from any app.")

    def open_settings() -> None:
        dlg = SettingsDialog(frame)
        ok = dlg.ShowModal() == wx.ID_OK
        dlg.Destroy()
        if not ok:
            return
        try:
            new = load_settings()
        except ValueError as e:
            wx.MessageBox(str(e), "Settings problem", wx.OK | wx.ICON_WARNING, frame)
            return
        state["runner"].stop()
        start(new)

    def get_status() -> tuple[str, bool]:
        app = state["app"]
        return f"{state['summary']}. {app.status_text()}", app.paused

    frame = MainWindow(
        on_ask=lambda q: state["app"].ask(q),
        on_repeat=lambda: state["app"].repeat(),
        on_stop=lambda: state["app"].stop_speaking(),
        on_pause=lambda: state["app"].toggle_pause(),
        on_settings=open_settings,
        get_status=lambda: get_status() if "app" in state else ("Starting...", False),
    )
    frame.Show()
    start(settings)

    def on_close(event):
        frame.timer.Stop()
        state["runner"].stop()
        event.Skip()

    frame.Bind(wx.EVT_CLOSE, on_close)
    wx_app.MainLoop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Meeting Screen Assistant")
    ap.add_argument("--stub", action="store_true", help="fake model responses, no network")
    ap.add_argument("--console", action="store_true", help="no window; type questions in the console")
    ap.add_argument("--no-hotkeys", action="store_true")
    args = ap.parse_args()

    if getattr(sys, "frozen", False):
        # Packaged .exe: look for a .env and write logs next to the exe, wherever it was started from.
        os.chdir(Path(sys.executable).parent)

    if not args.console:
        run_gui(args.stub, not args.no_hotkeys)
        return

    settings = load_settings()
    app = make_app(settings, args.stub, make_speaker())
    try:
        asyncio.run(app.run_console(use_hotkeys=not args.no_hotkeys))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
