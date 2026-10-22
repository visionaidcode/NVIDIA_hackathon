"""Main window: type a question, hear the answer, review past answers. Keyboard only."""

from __future__ import annotations

import time
from collections.abc import Callable

import wx

from app.input import hotkeys


class MainWindow(wx.Frame):
    RECORD_LABELS = {False: "Start &meeting transcript", True: "Stop &meeting transcript"}

    def __init__(self, on_ask: Callable[[str], None], on_repeat: Callable[[], None],
                 on_stop: Callable[[], None], on_record: Callable[[], None],
                 on_settings: Callable[[], None], get_status: Callable[[], tuple[str, bool]]):
        super().__init__(None, title="Meeting Screen Assistant", size=(760, 560))
        self.get_status = get_status
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(wx.StaticText(panel, label="&Question (start the meeting transcript first; "
                                            "Enter asks, empty asks what is on screen):"),
                 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.question = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        root.Add(self.question, 0, wx.ALL | wx.EXPAND, 10)

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
