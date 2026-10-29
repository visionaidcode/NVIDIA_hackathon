"""Settings window: API keys and a few choices. Native controls, keyboard only.

Saarthi uses fixed services: answers from NVIDIA Nemotron on Nebius Token Factory, speech to
text from Sarvam AI, meeting search from Qwen3-Embedding-8B on Nebius. They are shown read-only;
the user only enters the two keys. Each field has a visible label created just before it, which
is how NVDA and JAWS name native Windows edit boxes.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import numpy as np
import wx

from app.audio.wav import encode_wav
from app.config import DEFAULT_EMBED_MODEL, OFF, load_settings, read_values, save_values, settings_file

PROVIDER = "nebius"
PROVIDER_NAME = "Nebius Token Factory"
PRESETS = {
    # Nebius Token Factory takes images but not audio, so speech goes to Sarvam AI below.
    # Thinking off: on real screenshots the model otherwise spends the whole answer budget reasoning.
    "nebius": {"url": "https://api.tokenfactory.nebius.com/v1", "model": "nvidia/Nemotron-3.5-Super-VL",
               "extra_body": '{"chat_template_kwargs": {"enable_thinking": false}}'},
}
DEFAULT_STT_MODEL = "saaras:v4"
DEFAULT_STT_URL = "https://api.sarvam.ai"
DEFAULT_STT_API = "sarvam"
STT_API_NAME = "Sarvam AI speech to text (saaras)"
ANSWER_VOICE_CHOICES = [
    ("human", "Human voice (offline Piper voices, English, Tamil, Hindi...)"),
    ("screenreader", "Screen reader (NVDA or JAWS), else the Windows voice"),
]
SUMMARY_CHOICES = [("2.5", "Every 2.5 minutes"), ("5", "Every 5 minutes")]


async def check_connection(settings) -> list[str]:
    """One tiny text call to the answer model, one short audio call to the speech model, and one
    embedding call to the meeting search model if there is one."""
    from app.llm.client import OpenAICompatClient

    client = OpenAICompatClient(settings)
    results = []

    async def answer_call():
        msgs = [{"role": "user", "content": "Reply with the single word OK."}]
        return "".join([d async for d in client.stream_chat(msgs, temperature=0, max_tokens=300)])

    checks = [("Answer model", answer_call),
              ("Speech to text model", lambda: client.transcribe(encode_wav(np.zeros(8000, dtype=np.float32))))]
    if settings.embed_model:
        checks.append(("Meeting search model", lambda: client.embed(["test"])))
    for label, call in checks:
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(call(), timeout=45)
            results.append(f"{label}: works ({time.perf_counter() - t0:.1f} seconds).")
        except Exception as e:  # noqa: BLE001 - report every failure in words
            results.append(f"{label}: failed. {type(e).__name__}: {str(e)[:200]}")
    return results


def summary_choice(minutes: str) -> int:
    """The dropdown entry for a saved SUMMARY_MINUTES: 2.5, or 5 for anything above 3.75."""
    try:
        return 1 if float(minutes) > 3.75 else 0
    except ValueError:
        return 0


class SettingsDialog(wx.Dialog):
    def __init__(self, parent=None, first_run: bool = False, error: str = ""):
        super().__init__(parent, title="Saarthi settings",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.values = read_values()
        self._building = True
        root = wx.BoxSizer(wx.VERTICAL)

        intro = ("Welcome. Enter your Nebius and Sarvam API keys to start." if first_run
                 else "Change your API keys, answer voice and meeting options.")
        if error:
            intro = f"{error}\n{intro}"
        intro_text = wx.StaticText(self, label=intro)
        intro_text.Wrap(620)
        root.Add(intro_text, 0, wx.ALL | wx.EXPAND, 10)
        root.Add(wx.StaticText(self, label="Settings &file:"), 0, wx.LEFT | wx.RIGHT, 10)
        path_box = wx.TextCtrl(self, value=str(settings_file()), style=wx.TE_READONLY, size=(620, -1))
        root.Add(path_box, 0, wx.ALL | wx.EXPAND, 10)

        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        grid.AddGrowableCol(1)

        def row(label: str, make: Callable[[], wx.Window]) -> wx.Window:
            # Label first, then the control: screen readers name a control after the
            # static text created just before it.
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            ctrl = make()
            grid.Add(ctrl, 1, wx.EXPAND)
            return ctrl

        def fixed(label: str, value: str) -> wx.TextCtrl:
            # Read-only, but still reached with Tab and read by screen readers (a disabled
            # control would be skipped).
            return row(label, lambda: wx.TextCtrl(self, value=value, style=wx.TE_READONLY))

        # --- answer model -------------------------------------------------
        grid.Add(wx.StaticText(self, label="Screen answers (NVIDIA Nemotron)"), 0, wx.TOP, 4)
        grid.AddSpacer(0)
        self.provider = fixed("&Provider:", PROVIDER_NAME)

        grid.Add(wx.StaticText(self, label="API &key:"), 0, wx.ALIGN_CENTER_VERTICAL)
        key_box = wx.BoxSizer(wx.HORIZONTAL)
        self.key_hidden = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.key_shown = wx.TextCtrl(self)
        key_box.Add(self.key_hidden, 1, wx.EXPAND)
        key_box.Add(self.key_shown, 1, wx.EXPAND)
        grid.Add(key_box, 1, wx.EXPAND)
        grid.AddSpacer(0)
        self.show_key = wx.CheckBox(self, label="&Show keys")
        grid.Add(self.show_key)

        self.model = fixed("&Model ID:", PRESETS[PROVIDER]["model"])

        # --- speech to text -----------------------------------------------
        grid.Add(wx.StaticText(self, label="Speech to text (meeting audio)"), 0, wx.TOP, 10)
        grid.AddSpacer(0)
        self.stt_api = fixed("Speech &API type:", STT_API_NAME)
        grid.Add(wx.StaticText(self, label="Speech API k&ey:"), 0, wx.ALIGN_CENTER_VERTICAL)
        stt_key_box = wx.BoxSizer(wx.HORIZONTAL)
        self.stt_key_hidden = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.stt_key_shown = wx.TextCtrl(self)
        stt_key_box.Add(self.stt_key_hidden, 1, wx.EXPAND)
        stt_key_box.Add(self.stt_key_shown, 1, wx.EXPAND)
        grid.Add(stt_key_box, 1, wx.EXPAND)
        self.stt_model = fixed("Speech m&odel ID:", DEFAULT_STT_MODEL)

        # --- answer voice -------------------------------------------------
        grid.Add(wx.StaticText(self, label="Spoken answers"), 0, wx.TOP, 10)
        grid.AddSpacer(0)
        self.answer_voice = row("Answer voi&ce:", lambda: wx.Choice(
            self, choices=[label for _, label in ANSWER_VOICE_CHOICES]))

        # --- questions after a recording ------------------------------------
        grid.Add(wx.StaticText(self, label="Questions after the recording"), 0, wx.TOP, 10)
        grid.AddSpacer(0)
        self.summary_minutes = row("Meeting summar&y:", lambda: wx.Choice(
            self, choices=[label for _, label in SUMMARY_CHOICES]))
        grid.AddSpacer(0)
        self.save_screenshots = wx.CheckBox(self, label="Save every screenshot &in the meeting folder")
        grid.Add(self.save_screenshots)

        root.Add(grid, 1, wx.ALL | wx.EXPAND, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.test_btn = wx.Button(self, label="&Test connection")
        save_btn = wx.Button(self, wx.ID_OK, label="Sa&ve")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Cancel")
        for b in (self.test_btn, save_btn, cancel_btn):
            buttons.Add(b, 0, wx.LEFT, 8)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        save_btn.SetDefault()

        self.SetSizerAndFit(root)
        self.SetMinSize((640, -1))
        self.Fit()
        self.CentreOnScreen()

        self._load()
        self.show_key.Bind(wx.EVT_CHECKBOX, lambda e: self._sync_key_visibility())
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self._building = False
        (self.stt_key_hidden if self._key() and not self._stt_key() else self.key_hidden).SetFocus()

    # --- helpers ---------------------------------------------------------

    def _key(self) -> str:
        return (self.key_shown if self.show_key.IsChecked() else self.key_hidden).GetValue().strip()

    def _stt_key(self) -> str:
        return (self.stt_key_shown if self.show_key.IsChecked() else self.stt_key_hidden).GetValue().strip()

    def _load(self) -> None:
        v = self.values
        self.key_hidden.SetValue(v.get("LLM_API_KEY", ""))
        self.stt_key_hidden.SetValue(v.get("STT_API_KEY", ""))
        voice_keys = [k for k, _ in ANSWER_VOICE_CHOICES]
        voice = (v.get("ANSWER_VOICE") or "human").lower()
        self.answer_voice.SetSelection(voice_keys.index(voice) if voice in voice_keys else 0)
        self.summary_minutes.SetSelection(summary_choice(v.get("SUMMARY_MINUTES", "")))
        self.save_screenshots.SetValue((v.get("SAVE_SCREENSHOTS") or "on").lower() not in OFF)
        self._sync_key_visibility()

    def _sync_key_visibility(self) -> None:
        show = self.show_key.IsChecked()
        for hidden, shown in ((self.key_hidden, self.key_shown), (self.stt_key_hidden, self.stt_key_shown)):
            src, dst = (hidden, shown) if show else (shown, hidden)
            if not self._building or show:
                dst.SetValue(src.GetValue())
            hidden.Show(not show)
            shown.Show(show)
        self.Layout()

    def collect(self) -> dict[str, str]:
        preset = PRESETS[PROVIDER]
        return {
            "LLM_PROVIDER": PROVIDER,
            "LLM_BASE_URL": preset["url"],
            "LLM_API_KEY": self._key(),
            "LLM_MODEL": preset["model"],
            "STT_BASE_URL": DEFAULT_STT_URL,
            "STT_API_KEY": self._stt_key(),
            "STT_MODEL": DEFAULT_STT_MODEL,
            "STT_API": DEFAULT_STT_API,
            "STT_LANGUAGE": "",  # Sarvam detects the language of each piece of speech
            "LLM_EXTRA_BODY": self._extra_body(),
            "ANSWER_VOICE": ANSWER_VOICE_CHOICES[max(0, self.answer_voice.GetSelection())][0],
            "EMBED_MODEL": DEFAULT_EMBED_MODEL,
            "SUMMARY_MINUTES": SUMMARY_CHOICES[max(0, self.summary_minutes.GetSelection())][0],
            "SAVE_SCREENSHOTS": "on" if self.save_screenshots.IsChecked() else "off",
        }

    def _extra_body(self) -> str:
        """Keep a hand-set LLM_EXTRA_BODY; otherwise use the provider's (thinking off on Nebius)."""
        current = self.values.get("LLM_EXTRA_BODY", "")
        if current and current not in {p.get("extra_body", "") for p in PRESETS.values()}:
            return current
        return PRESETS[PROVIDER]["extra_body"]

    def _missing(self, values: dict[str, str]) -> tuple[str, wx.Window] | None:
        show = self.show_key.IsChecked()
        checks = [("API key", "LLM_API_KEY", self.key_shown if show else self.key_hidden),
                  ("Speech API key", "STT_API_KEY", self.stt_key_shown if show else self.stt_key_hidden)]
        for label, name, ctrl in checks:
            if not values[name]:
                return label, ctrl
        return None

    def _settings_from_form(self):
        """Build Settings from the form without saving, by layering it over the file values."""
        import os

        values = self.collect()
        saved = {k: os.environ.get(k) for k in values}
        try:
            os.environ.update(values)
            return load_settings(None)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # --- buttons -----------------------------------------------------------

    def _on_test(self, event) -> None:
        values = self.collect()
        if missing := self._missing(values):
            wx.MessageBox(f"Please enter the {missing[0]}.", "Missing setting", wx.OK | wx.ICON_WARNING, self)
            missing[1].SetFocus()
            return
        try:
            settings = self._settings_from_form()
        except ValueError as e:
            wx.MessageBox(str(e), "Settings problem", wx.OK | wx.ICON_WARNING, self)
            return
        self.test_btn.Disable()
        self.test_btn.SetLabel("Testing, please wait...")

        def work():
            results = asyncio.run(check_connection(settings))
            wx.CallAfter(self._show_test_results, results)

        threading.Thread(target=work, daemon=True).start()

    def _show_test_results(self, results: list[str]) -> None:
        self.test_btn.SetLabel("&Test connection")
        self.test_btn.Enable()
        ok = all("works" in r for r in results)
        wx.MessageBox("\n".join(results), "Connection works" if ok else "Connection problem",
                      wx.OK | (wx.ICON_INFORMATION if ok else wx.ICON_ERROR), self)
        self.test_btn.SetFocus()

    def _on_save(self, event) -> None:
        values = self.collect()
        if missing := self._missing(values):
            wx.MessageBox(f"Please enter the {missing[0]}.", "Missing setting", wx.OK | wx.ICON_WARNING, self)
            missing[1].SetFocus()
            return
        save_values(values)
        self.EndModal(wx.ID_OK)
