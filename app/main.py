"""Wiring and lifecycle.

    python -m app.main              # window (first run asks for the API key)
    python -m app.main --stub       # fake answers, no network
    python -m app.main --console    # no window: type questions in the console

Global hotkeys work in both modes. Ctrl+Alt+M starts the meeting transcript (screen and
system audio capture, speaker names, saved to a text file); questions need it running.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

from app.config import load_settings, settings_file, user_settings_dir
from app.eval.latency import LatencyLog
from app.input import hotkeys
from app.llm.client import StubClient, make_client
from app.memory.buffer import RollingBuffer, Snapshot, TranscriptLine
from app.memory.history import PageNoter, ScreenHistory
from app.memory.transcript import MeetingTranscript, format_line
from app.qa.ask import answer_question
from app.qa.review import answer_from_recording
from app.screen.capture import ScreenCapture
from app.speech.out import SelfSpeechGuard, make_speaker
from app.stt.speaker import SpeakerNamer
from app.stt.transcribe import Transcriber

DEFAULT_QUESTION = "What is on the screen and what was just said about it?"
HISTORY_TURNS = 3


class App:
    """Capture runs only while a meeting transcript is being recorded (Ctrl+Alt+M).
    While recording, questions are answered from the last 30 seconds of screen and the
    transcript. After it stops, from the whole recording (every screen, Nemotron's page notes,
    the transcript) until a new recording starts or the app closes."""

    def __init__(self, settings, client, speaker):
        self.settings = settings
        self.client = client
        self.speaker = SelfSpeechGuard(speaker)
        self.log = LatencyLog(settings.latency_log)
        self.buffer = RollingBuffer(settings.window_seconds)
        self.screens = ScreenHistory()  # every different screen of the recording, in memory only
        self.noter = PageNoter(client, self.screens, on_note=self._on_note)
        self._noter_task: asyncio.Task | None = None
        self.screen = self._new_screen()
        self.meeting = MeetingTranscript(settings.transcript_dir)
        self.namer = SpeakerNamer(client, self.buffer) if settings.speaker_names else None
        self.transcriber = Transcriber(client, self.buffer, self.log,
                                       name_speaker=self.namer.name_for if self.namer else None,
                                       on_line=self._on_line)
        self.on_transcript = None  # optional callback for each new transcript line (the window log)
        self.audio = None
        self.recording = False
        self._switching = False  # a start or stop is in progress
        self.last_answer = ""
        # Last 3 (question, answer) pairs, in memory only, so follow-ups make sense.
        # Cleared when a new meeting transcript starts.
        self.history: deque[tuple[str, str]] = deque(maxlen=HISTORY_TURNS)
        self._current: asyncio.Task | None = None
        self._stt_task: asyncio.Task | None = None
        self._unregister_hotkeys = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _new_screen(self) -> ScreenCapture:
        return ScreenCapture(self.buffer, self.settings.frame_interval,
                             self.settings.frame_max_side, self.settings.jpeg_quality,
                             history=self.screens)

    # --- capture ---------------------------------------------------------

    def start_audio(self) -> None:
        from app.audio.loopback import LoopbackCapture
        from app.audio.process_loopback import ProcessLoopbackCapture
        from app.audio.vad import SpeechSegmenter, make_vad

        vad = make_vad()
        def on_segment(seg):
            # The speakers also play our own answers; never transcribe those.
            if self.speaker.overlaps(seg.start, seg.end):
                self.log.write("stt_skip_own_voice", audio_s=round(seg.duration, 2))
                return
            self.loop.call_soon_threadsafe(self.transcriber.queue.put_nowait, seg)
        try:
            # Leaves out the screen reader's voice (NVDA, JAWS, Narrator); Windows 10 2004+.
            self.audio = ProcessLoopbackCapture(SpeechSegmenter(vad), on_segment)
            self.audio.start()
        except Exception as e:  # noqa: BLE001 - older Windows: capture everything as before
            print(f"Screen reader filter unavailable ({e}); capturing all system audio.")
            self.audio = LoopbackCapture(SpeechSegmenter(vad), on_segment)
            self.audio.start()
        print(f"Audio: {self.audio.device_name}, VAD: {type(vad).__name__}")

    def _on_note(self, shot) -> None:
        """Nemotron's note on a new screen: into the transcript file and the window log."""
        self.meeting.add_note(shot.first_ts, shot.note)
        if self.on_transcript:
            self.on_transcript(TranscriptLine(shot.first_ts, shot.first_ts, shot.note, "Screen"))

    def _on_line(self, line) -> None:
        self.meeting.add(line)
        if self.on_transcript:
            self.on_transcript(line)

    async def start_recording(self) -> None:
        path = self.meeting.start()
        self.buffer.clear()
        self.history.clear()
        self.screens.clear(time.time())  # the previous recording's screens are gone
        self.screen = self._new_screen()
        self.screen.start()
        try:
            await asyncio.to_thread(self.start_audio)
        except Exception as e:  # noqa: BLE001
            print(f"System audio capture unavailable: {e}")
            self.speaker.speak("System audio capture is not available, so nothing said will be "
                               "transcribed. Screen questions still work.")
        self.recording = True
        self._noter_task = asyncio.create_task(self.noter.run())
        self.speaker.speak(f"Meeting transcript started. Saving to {path.name}. "
                           f"Ask with control alt space; press control alt M to stop.", True)

    async def stop_recording(self) -> None:
        self.recording = False
        self.screen.stop()
        if self.audio:
            # Stopping flushes the last speech segment into the queue.
            await asyncio.to_thread(self.audio.stop)
            self.audio = None
        if not self.transcriber.queue.empty():
            self.speaker.speak("Stopping. Finishing the last lines.", True)
        try:
            await asyncio.wait_for(self.transcriber.queue.join(), 60)
        except asyncio.TimeoutError:
            pass
        if self._noter_task:
            self._noter_task.cancel()
        if self.noter.pending():
            self.speaker.speak("Finishing the notes on the last screens.", True)
            await self.noter.finish(30)
        path = self.meeting.stop()
        self.buffer.clear()
        if path:
            self.speaker.speak(f"Meeting transcript stopped. {len(self.meeting.lines)} lines saved to "
                               f"{path.name} in {path.parent}. You can still ask about this recording.",
                               True)

    async def _toggle_recording(self) -> None:
        if self._switching:
            return
        self._switching = True
        try:
            await (self.stop_recording() if self.recording else self.start_recording())
        finally:
            self._switching = False

    # --- actions (thread-safe: called from the window, hotkey thread or console) ---

    def toggle_recording(self) -> None:
        if not self.loop:
            self.speaker.speak("Still starting, please try again in a moment.", True)
            return
        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self._toggle_recording()))

    def ask(self, question: str = "") -> None:
        question_end = time.perf_counter()
        if not self.loop:
            self.speaker.speak("Still starting, please ask again in a moment.", True)
            return
        if self._switching:  # half-started or half-stopped: neither the live nor the recorded data is ready
            self.speaker.speak("The transcript is still starting or stopping. Please ask again in a moment.",
                               True)
            return
        if not self.recording and not len(self.screens):
            self.speaker.speak("The meeting is not being transcribed. Press control alt M to start, "
                               "then ask your question.", True)
            return
        self.loop.call_soon_threadsafe(self._start_answer, question or DEFAULT_QUESTION, question_end)

    def _start_answer(self, question: str, question_end: float) -> None:
        if self._current and not self._current.done():
            self._current.cancel()
        self.speaker.stop()
        self._current = self.loop.create_task(self._answer(question, question_end))

    async def _answer(self, question: str, question_end: float) -> None:
        if not self.recording:
            await self._answer_recording(question, question_end)
            return
        try:
            try:
                await asyncio.to_thread(self.screen.capture_now)  # the screen as it is now
            except Exception as e:  # noqa: BLE001 - fall back to the periodic screenshots
                self.log.write("capture_error", error=type(e).__name__)
            frames = self.buffer.snapshot()
            snapshot = Snapshot(frames.now, frames.frames, self.meeting.recent())
            result = await answer_question(
                self.client, snapshot, question, speak=self.speaker.speak,
                max_frames=self.settings.max_frames_per_question, log=self.log,
                question_end=question_end, history=list(self.history))
            self.last_answer = result.text
            self.history.append((question, result.text))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - say it, never crash
            self.log.write("answer_error", error=type(e).__name__)
            self.speaker.speak(f"Sorry, the answer failed: {type(e).__name__}. {str(e)[:150]}", True)

    async def _answer_recording(self, question: str, question_end: float) -> None:
        """After recording stopped: Nemotron picks the screens, then answers from them."""
        try:
            self.speaker.speak("Checking the recording.", True)
            result = await answer_from_recording(
                self.client, self.screens.shots(), list(self.meeting.lines), question,
                self.screens.started, speak=self.speaker.speak,
                max_frames=self.settings.max_frames_per_question, log=self.log,
                question_end=question_end, history=list(self.history))
            self.last_answer = result.text
            self.history.append((question, result.text))
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
        if not self.recording:
            if len(self.screens):
                n, notes = len(self.screens), sum(1 for s in self.screens.shots() if s.note)
                return (f"Not transcribing. Questions answer from the last recording: "
                        f"{len(self.meeting.lines)} lines, {n} screen{'' if n == 1 else 's'}, "
                        f"{notes} with notes. Press control alt M to start a new one.")
            return "Not transcribing. Press control alt M to start the meeting transcript."
        snap = self.buffer.snapshot()
        parts = [f"Transcribing the meeting: {len(self.meeting.lines)} lines saved, "
                 f"{len(snap.frames)} screenshots from the last {int(self.settings.window_seconds)} seconds."]
        if self.meeting.lines:
            parts.append(f"Last speaker: {self.meeting.lines[-1].speaker or 'unknown'}.")
        if self.screen.last_error:
            parts.append("Screen capture has an error.")
        if self.audio is None:
            parts.append("System audio is not being captured.")
        if self.transcriber.last_error:
            parts.append(f"Transcription error: {type(self.transcriber.last_error).__name__}.")
        if self.namer and self.namer.last_error:
            parts.append(f"Speaker name error: {type(self.namer.last_error).__name__}.")
        if self.noter.last_error:
            parts.append(f"Screen note error: {type(self.noter.last_error).__name__}.")
        return " ".join(parts)

    def status(self) -> None:
        self.speaker.speak(self.status_text(), True)

    # --- lifecycle --------------------------------------------------------

    async def start(self, use_hotkeys: bool = True) -> None:
        self.loop = asyncio.get_running_loop()
        self._stt_task = asyncio.create_task(self.transcriber.run())
        if use_hotkeys:
            try:
                self._unregister_hotkeys = hotkeys.register({
                    "record": self.toggle_recording, "ask": self.ask, "repeat": self.repeat,
                    "stop": self.stop_speaking, "status": self.status,
                })
            except Exception as e:  # noqa: BLE001
                print(f"Hotkeys unavailable: {e}")

    async def stop(self) -> None:
        if self._unregister_hotkeys:
            self._unregister_hotkeys()
            self._unregister_hotkeys = None
        if self.recording:
            await self.stop_recording()
        for task in (self._current, self._stt_task, self._noter_task):
            if task and not task.done():
                task.cancel()
        self.screen.stop()
        if self.audio:
            self.audio.stop()
            self.audio = None
        self.meeting.stop()
        self.buffer.clear()

    def greeting(self) -> str:
        mode = "test mode with fake answers" if isinstance(self.client, StubClient) else "ready"
        return f"Saarthi {mode}."

    async def run_console(self, use_hotkeys: bool = True) -> None:
        await self.start(use_hotkeys)
        self.speaker.speak(f"{self.greeting()} Press {hotkeys.describe()}.")
        print("Type m and Enter to start or stop the meeting transcript, or type a question and press "
              "Enter. Empty line asks the default question. Ctrl+C to quit.")
        try:
            while True:
                line = await self.loop.run_in_executor(None, sys.stdin.readline)
                if not line:  # stdin closed
                    await asyncio.Event().wait()
                question = line.strip()
                if question.lower() in {"q", "quit", "exit"}:
                    break
                if question.lower() == "m":
                    self.toggle_recording()
                    continue
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

    speaker = LogSpeaker(make_speaker(echo=False, settings=settings), log)

    def start(s) -> None:
        old = state.get("settings")
        if old and (old.answer_voice, old.voice_dir) != (s.answer_voice, s.voice_dir):
            # The answer voice changed in Settings: switch it without restarting.
            speaker.inner.stop()
            speaker.inner = make_speaker(echo=False, settings=s)
            speaker.name = speaker.inner.name
        state["settings"] = s
        state["app"] = make_app(s, force_stub, speaker)
        state["app"].on_transcript = lambda line: log(format_line(line))
        state["runner"] = BackgroundRunner(state["app"], use_hotkeys)
        app = state["app"]
        model = "fake answers" if isinstance(app.client, StubClient) else \
            f"answers {s.model}, speech {s.stt_model}"
        state["summary"] = f"{s.provider}: {model}"
        speaker.speak(f"{app.greeting()} Press control alt M to start transcribing the meeting, "
                      f"then type a question and press Enter, or press control alt space from any app.")

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
        return f"{state['summary']}. {app.status_text()}", app.recording

    frame = MainWindow(
        on_ask=lambda q: state["app"].ask(q),
        on_repeat=lambda: state["app"].repeat(),
        on_stop=lambda: state["app"].stop_speaking(),
        on_record=lambda: state["app"].toggle_recording(),
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
    ap = argparse.ArgumentParser(description="Saarthi")
    ap.add_argument("--stub", action="store_true", help="fake model responses, no network")
    ap.add_argument("--console", action="store_true", help="no window; type questions in the console")
    ap.add_argument("--no-hotkeys", action="store_true")
    args = ap.parse_args()

    if getattr(sys, "frozen", False) or "__compiled__" in globals():  # PyInstaller / Nuitka exe
        # Packaged .exe: look for a .env and write logs next to the exe, wherever it was started from.
        os.chdir(Path(sys.executable).parent)
        # The exe has no console, so messages and errors (a voice that fails to load...) go to a
        # file instead of nowhere. Rewritten on each start, so it stays small.
        log_file = user_settings_dir() / "saarthi.log"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            sys.stdout = sys.stderr = open(log_file, "w", encoding="utf-8", buffering=1)
        except OSError:
            pass

    if not args.console:
        run_gui(args.stub, not args.no_hotkeys)
        return

    settings = load_settings()
    app = make_app(settings, args.stub, make_speaker(settings=settings))
    try:
        asyncio.run(app.run_console(use_hotkeys=not args.no_hotkeys))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
