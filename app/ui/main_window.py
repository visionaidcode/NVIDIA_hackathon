"""Main window: type a question, hear the answer, review past answers. Keyboard only."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import wx

from app.config import APP_NAME, APP_VERSION
from app.input import hotkeys
from app.rag.store import meeting_label

# The Saarthi logo, for the title bar and taskbar (also inside the exe, scripts/build_pyinstaller.ps1).
ICON_FILE = Path(__file__).resolve().parents[2] / "packaging" / "icon.ico"


class MainWindow(wx.Frame):
    RECORD_LABELS = {False: "Start &meeting transcript", True: "Stop &meeting transcript"}

    def __init__(self, on_ask: Callable[[str], None], on_repeat: Callable[[], None],
                 on_stop: Callable[[], None], on_record: Callable[[], None],
                 on_settings: Callable[[], None], get_status: Callable[[], tuple[str, bool]],
                 get_meetings: Callable[[], tuple[list[Path], Path | None]] = lambda: ([], None),
                 on_meeting: Callable[[Path], bool] = lambda folder: False):
        super().__init__(None, title=f"{APP_NAME} {APP_VERSION}", size=(760, 600))
        if ICON_FILE.exists():
            self.SetIcons(wx.IconBundle(str(ICON_FILE)))
        self.get_status = get_status
        self.get_meetings = get_meetings
        self.meeting_folders: list[Path] = []
        self.current_meeting: Path | None = None
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(wx.StaticText(panel, label="&Question (start the meeting transcript first; "
                                            "Enter asks, empty asks what is on screen):"),
                 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.question = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        root.Add(self.question, 0, wx.ALL | wx.EXPAND, 10)

        # Questions after the transcript is stopped are about this meeting: the last one recorded,
        # or one chosen here. Choosing takes the button, so moving through the list with the
        # arrow keys does not open every meeting on the way.
        root.Add(wx.StaticText(panel, label="&Past meeting to ask about (when the transcript is stopped):"),
                 0, wx.LEFT | wx.RIGHT, 10)
        pick = wx.BoxSizer(wx.HORIZONTAL)
        self.meeting_choice = wx.Choice(panel)
        self.meeting_btn = wx.Button(panel, label="&Use this meeting")
        pick.Add(self.meeting_choice, 1, wx.RIGHT, 8)
        pick.Add(self.meeting_btn, 0)
        root.Add(pick, 0, wx.ALL | wx.EXPAND, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.record_btn = wx.Button(panel, label=self.RECORD_LABELS[False])
        self.ask_btn = wx.Button(panel, label="&Ask")
        repeat_btn = wx.Button(panel, label="&Repeat answer")
        stop_btn = wx.Button(panel, label="S&top speaking")
        settings_btn = wx.Button(panel, label="Settin&gs...")
        for b in (self.record_btn, self.ask_btn, repeat_btn, stop_btn, settings_btn):
            buttons.Add(b, 0, wx.RIGHT, 8)
        root.Add(buttons, 0, wx.LEFT | wx.RIGHT, 10)

        root.Add(wx.StaticText(panel, label="Answers, transcript and &log:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        root.Add(self.log, 1, wx.ALL | wx.EXPAND, 10)

        keys = ", ".join(f"{k.title()}: {v.replace('+', ' ')}" for k, v in hotkeys.DEFAULT_HOTKEYS.items())
        root.Add(wx.StaticText(panel, label=f"Global hotkeys work in any app. {keys}."),
                 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(root)
        self.CreateStatusBar()

        def ask(_event=None):
            text = self.question.GetValue().strip()
            self.add_log(f"You asked: {text or 'What is on the screen?'}")
            self.question.SetValue("")
            on_ask(text)

        self.question.Bind(wx.EVT_TEXT_ENTER, ask)
        self.ask_btn.Bind(wx.EVT_BUTTON, ask)
        repeat_btn.Bind(wx.EVT_BUTTON, lambda e: on_repeat())
        stop_btn.Bind(wx.EVT_BUTTON, lambda e: on_stop())
        self.record_btn.Bind(wx.EVT_BUTTON, lambda e: on_record())
        settings_btn.Bind(wx.EVT_BUTTON, lambda e: on_settings())

        def use_meeting(_event=None):
            i = self.meeting_choice.GetSelection()
            if 0 <= i < len(self.meeting_folders):
                folder = self.meeting_folders[i]
                if on_meeting(folder):
                    self.current_meeting = folder
                    self.add_log(f"Questions are now about the meeting of {meeting_label(folder)}.")

        self.meeting_btn.Bind(wx.EVT_BUTTON, use_meeting)
        self.refresh_meetings()

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.refresh_status(), self.timer)
        self.timer.Start(1000)
        self.question.SetFocus()

    def add_log(self, text: str) -> None:
        self.log.AppendText(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    def refresh_status(self) -> None:
        status, recording = self.get_status()
        if self.GetStatusBar().GetStatusText() != status:
            self.SetStatusText(status)
        label = self.RECORD_LABELS[recording]
        if self.record_btn.GetLabel() != label:
            self.record_btn.SetLabel(label)
        for control in (self.meeting_choice, self.meeting_btn):
            if control.IsEnabled() == recording:
                control.Enable(not recording)
        self.refresh_meetings()

    def refresh_meetings(self) -> None:
        """Keep the list in step with the meeting folders, and show the meeting questions are
        about when that changes (a recording just stopped, the app started). The list is left
        alone otherwise, so it does not move while the user is in it."""
        folders, current = self.get_meetings()
        changed = folders != self.meeting_folders
        if changed:
            selected = self.meeting_choice.GetSelection()
            keep = self.meeting_folders[selected] if 0 <= selected < len(self.meeting_folders) else None
            self.meeting_folders = folders
            self.meeting_choice.Set([meeting_label(f) for f in folders])
            if keep in folders:
                self.meeting_choice.SetSelection(folders.index(keep))
        if (changed or current != self.current_meeting) and current in folders:
            self.meeting_choice.SetSelection(folders.index(current))
        self.current_meeting = current
