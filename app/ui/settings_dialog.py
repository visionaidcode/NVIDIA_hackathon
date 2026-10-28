"""Settings window: provider, API keys and models. Native controls, keyboard only.

Each field has a visible label created just before it, which is how NVDA and
JAWS name native Windows edit boxes.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import numpy as np
import wx

from app.audio.wav import encode_wav
from app.config import DEFAULT_KEYTERMS, load_settings, read_values, save_values, settings_file

PROVIDER_CHOICES = [
    ("nebius", "Nebius Token Factory"),
    ("openrouter", "OpenRouter"),
    ("stub", "Test mode (no key, fake answers)"),
]
PRESETS = {
    "openrouter": {"url": "https://openrouter.ai/api/v1",
                   "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"},
    # Nebius Token Factory takes images but not audio, so speech goes to Sarvam AI below.
    # Thinking off: on real screenshots the model otherwise spends the whole answer budget reasoning.
    "nebius": {"url": "https://api.tokenfactory.nebius.com/v1", "model": "nvidia/Nemotron-3.5-Super-VL",
               "extra_body": '{"chat_template_kwargs": {"enable_thinking": false}}'},
}
STT_API_CHOICES = [
    ("chat", "Chat model with audio input (Voxtral, Qwen Omni, Gemini)"),
    ("transcriptions", "Transcriptions endpoint (Whisper style)"),
    ("sarvam", "Sarvam AI speech to text (saaras)"),
]
DEFAULT_STT_MODEL = "saaras:v4"
DEFAULT_STT_URL = "https://api.sarvam.ai"
DEFAULT_STT_API = "sarvam"
ANSWER_VOICE_CHOICES = [
    ("human", "Human voice (offline Piper voices, English, Tamil, Hindi...)"),
    ("screenreader", "Screen reader (NVDA or JAWS), else the Windows voice"),
]


async def check_connection(settings) -> list[str]:
    """One tiny text call to the answer model and one short audio call to the speech model."""
    from app.llm.client import OpenAICompatClient

    client = OpenAICompatClient(settings)
    results = []

    async def answer_call():
        msgs = [{"role": "user", "content": "Reply with the single word OK."}]
        return "".join([d async for d in client.stream_chat(msgs, temperature=0, max_tokens=300)])

    for label, call in (("Answer model", answer_call),
                        ("Speech to text model",
                         lambda: client.transcribe(encode_wav(np.zeros(8000, dtype=np.float32))))):
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(call(), timeout=45)
            results.append(f"{label}: works ({time.perf_counter() - t0:.1f} seconds).")
        except Exception as e:  # noqa: BLE001 - report every failure in words
            results.append(f"{label}: failed. {type(e).__name__}: {str(e)[:200]}")
    return results


class SettingsDialog(wx.Dialog):
    def __init__(self, parent=None, first_run: bool = False, error: str = ""):
        super().__init__(parent, title="Saarathi settings",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.values = read_values()
        self._building = True
        root = wx.BoxSizer(wx.VERTICAL)

        intro = "Welcome. Enter your API key to start." if first_run else "Change the providers, keys and models."
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

        # --- answer model -------------------------------------------------
        grid.Add(wx.StaticText(self, label="Screen answers (NVIDIA Nemotron)"), 0, wx.TOP, 4)
        grid.AddSpacer(0)
        self.provider = row("&Provider:", lambda: wx.Choice(
            self, choices=[label for _, label in PROVIDER_CHOICES]))
        self.base_url = row("Base &URL:", lambda: wx.TextCtrl(self))

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

        self.model = row("&Model ID:", lambda: wx.TextCtrl(self))

        # --- speech to text -----------------------------------------------
        grid.Add(wx.StaticText(self, label="Speech to text (meeting audio)"), 0, wx.TOP, 10)
        grid.AddSpacer(0)
        grid.AddSpacer(0)
        self.stt_same = wx.CheckBox(self, label="Use the same &base URL and key as above")
        grid.Add(self.stt_same)
        self.stt_url = row("Speech base U&RL:", lambda: wx.TextCtrl(self))
        grid.Add(wx.StaticText(self, label="Speech API k&ey:"), 0, wx.ALIGN_CENTER_VERTICAL)
        stt_key_box = wx.BoxSizer(wx.HORIZONTAL)
        self.stt_key_hidden = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.stt_key_shown = wx.TextCtrl(self)
        stt_key_box.Add(self.stt_key_hidden, 1, wx.EXPAND)
        stt_key_box.Add(self.stt_key_shown, 1, wx.EXPAND)
        grid.Add(stt_key_box, 1, wx.EXPAND)
        self.stt_model = row("Speech m&odel ID:", lambda: wx.TextCtrl(self))
        self.stt_api = row("Speech &API type:", lambda: wx.Choice(
            self, choices=[label for _, label in STT_API_CHOICES]))
        self.stt_language = row("Speech &language code (optional, e.g. ta-IN):", lambda: wx.TextCtrl(self))
        self.stt_keyterms = row("Speech key &words, comma separated (names, products):",
                                lambda: wx.TextCtrl(self))

        # --- answer voice -------------------------------------------------
        grid.Add(wx.StaticText(self, label="Spoken answers"), 0, wx.TOP, 10)
        grid.AddSpacer(0)
        self.answer_voice = row("Answer voi&ce:", lambda: wx.Choice(
            self, choices=[label for _, label in ANSWER_VOICE_CHOICES]))

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
        self.provider.Bind(wx.EVT_CHOICE, self._on_provider)
        self.show_key.Bind(wx.EVT_CHECKBOX, lambda e: self._sync_key_visibility())
        self.stt_same.Bind(wx.EVT_CHECKBOX, lambda e: self._update_enabled())
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self._building = False
        (self.key_hidden if self._provider() != "stub" and not self._key() else self.provider).SetFocus()

    # --- helpers ---------------------------------------------------------

    def _provider(self) -> str:
        return PROVIDER_CHOICES[max(0, self.provider.GetSelection())][0]

    def _key(self) -> str:
        return (self.key_shown if self.show_key.IsChecked() else self.key_hidden).GetValue().strip()

    def _stt_key(self) -> str:
        return (self.stt_key_shown if self.show_key.IsChecked() else self.stt_key_hidden).GetValue().strip()

    def _load(self) -> None:
        v = self.values
        provider = (v.get("LLM_PROVIDER") or "nebius").split(" #")[0].strip().lower()
        keys = [k for k, _ in PROVIDER_CHOICES]
        self.provider.SetSelection(keys.index(provider) if provider in keys else 0)
        preset = PRESETS.get(self._provider(), {})
        self.base_url.SetValue(v.get("LLM_BASE_URL") or preset.get("url", ""))
        self.key_hidden.SetValue(v.get("LLM_API_KEY", ""))
        self.model.SetValue(v.get("LLM_MODEL") or preset.get("model", ""))
        # First run: speech has its own provider (Sarvam AI), since Nebius does not take audio.
        same = bool(v) and not v.get("STT_BASE_URL") and not v.get("STT_API_KEY")
        self.stt_same.SetValue(same)
        self.stt_url.SetValue(v.get("STT_BASE_URL") or DEFAULT_STT_URL)
        self.stt_key_hidden.SetValue(v.get("STT_API_KEY", ""))
        self.stt_model.SetValue(v.get("STT_MODEL") or DEFAULT_STT_MODEL)
        stt_keys = [k for k, _ in STT_API_CHOICES]
        stt_api = v.get("STT_API", DEFAULT_STT_API)
        self.stt_api.SetSelection(stt_keys.index(stt_api) if stt_api in stt_keys else 0)
        self.stt_language.SetValue(v.get("STT_LANGUAGE", ""))
        self.stt_keyterms.SetValue(v.get("STT_KEYTERMS") or DEFAULT_KEYTERMS)
        voice_keys = [k for k, _ in ANSWER_VOICE_CHOICES]
        voice = (v.get("ANSWER_VOICE") or "human").lower()
        self.answer_voice.SetSelection(voice_keys.index(voice) if voice in voice_keys else 0)
        self._sync_key_visibility()

    def _sync_key_visibility(self) -> None:
        show = self.show_key.IsChecked()
        for hidden, shown in ((self.key_hidden, self.key_shown), (self.stt_key_hidden, self.stt_key_shown)):
            src, dst = (hidden, shown) if show else (shown, hidden)
            if not self._building or show:
                dst.SetValue(src.GetValue())
            hidden.Show(not show)
            shown.Show(show)
        self._update_enabled()
        self.Layout()

    def _update_enabled(self) -> None:
        real = self._provider() != "stub"
        same = self.stt_same.IsChecked()
        for c in (self.base_url, self.key_hidden, self.key_shown, self.model, self.show_key,
                  self.stt_same, self.stt_model, self.stt_api, self.stt_language, self.stt_keyterms,
                  self.test_btn):
            c.Enable(real)
        for c in (self.stt_url, self.stt_key_hidden, self.stt_key_shown):
            c.Enable(real and not same)

    def _on_provider(self, event) -> None:
        preset = PRESETS.get(self._provider())
        if preset:
            known_urls = {p["url"] for p in PRESETS.values()}
            if self.base_url.GetValue().strip() in known_urls | {""}:
                self.base_url.SetValue(preset["url"])
            known_models = {p["model"] for p in PRESETS.values()}
            if self.model.GetValue().strip() in known_models | {""}:
                self.model.SetValue(preset["model"])
        self._update_enabled()

    def collect(self) -> dict[str, str]:
        same = self.stt_same.IsChecked()
        return {
            "LLM_PROVIDER": self._provider(),
            "LLM_BASE_URL": self.base_url.GetValue().strip(),
            "LLM_API_KEY": self._key(),
            "LLM_MODEL": self.model.GetValue().strip(),
            "STT_BASE_URL": "" if same else self.stt_url.GetValue().strip(),
            "STT_API_KEY": "" if same else self._stt_key(),
            "STT_MODEL": self.stt_model.GetValue().strip(),
            "STT_API": STT_API_CHOICES[max(0, self.stt_api.GetSelection())][0],
            "STT_LANGUAGE": self.stt_language.GetValue().strip(),
            "STT_KEYTERMS": self.stt_keyterms.GetValue().strip() or "none",
            "LLM_EXTRA_BODY": self._extra_body(),
            "ANSWER_VOICE": ANSWER_VOICE_CHOICES[max(0, self.answer_voice.GetSelection())][0],
        }

    def _extra_body(self) -> str:
        """Keep a hand-set LLM_EXTRA_BODY; otherwise use the provider's (thinking off on Nebius)."""
        current = self.values.get("LLM_EXTRA_BODY", "")
        if current and current not in {p.get("extra_body", "") for p in PRESETS.values()}:
            return current
        return PRESETS.get(self._provider(), {}).get("extra_body", "")

    def _missing(self, values: dict[str, str]) -> tuple[str, wx.Window] | None:
        if values["LLM_PROVIDER"] == "stub":
            return None
        key_ctrl = self.key_shown if self.show_key.IsChecked() else self.key_hidden
        stt_key_ctrl = self.stt_key_shown if self.show_key.IsChecked() else self.stt_key_hidden
        checks = [("Base URL", "LLM_BASE_URL", self.base_url), ("API key", "LLM_API_KEY", key_ctrl),
                  ("Model ID", "LLM_MODEL", self.model), ("Speech model ID", "STT_MODEL", self.stt_model)]
        if not self.stt_same.IsChecked():
            checks += [("Speech base URL", "STT_BASE_URL", self.stt_url),
                       ("Speech API key", "STT_API_KEY", stt_key_ctrl)]
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
