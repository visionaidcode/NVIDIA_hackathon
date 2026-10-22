# Meeting Screen Assistant

A Windows desktop app for blind and low-vision people in video meetings. When a
colleague shares their screen and says "look at this", press a hotkey (or type a
question) and hear a short spoken answer about what is shown and what was said, and by
whom. Works next to any meeting app (Zoom, Teams, Google Meet), because it
captures the screen and the system audio rather than plugging into the meeting.

Built for the Nebius x NVIDIA Global AI Hackathon (track: Best Apps and Agents).

## How it works

1. Press **Ctrl+Alt+M** to start the meeting transcript. Nothing is captured before that.
2. Every ~2 s the app takes a screenshot, downscales it and keeps the last 30 seconds
   in memory. A screen that hasn't changed is not stored twice.
3. System audio (what your speakers play) is captured through WASAPI loopback.
   Voice-activity detection drops silence and cuts speech at pauses; each segment
   is transcribed while the meeting goes on, by a separate speech-to-text model.
4. The speaker's name is read from the meeting screen: the screenshot taken while
   they spoke goes to the answer model, which reads the highlighted tile, speaking
   indicator or caption name (Teams, Zoom, Meet). If no name is visible the line says
   "Speaker". Each line is saved at once to
   `Documents\Meeting Assistant\Meeting <date> <time>.txt`, e.g.
   `[10:41:05] Priya: the button moves to the top right`.
5. Questions work only while the transcript is running. When you ask, a fresh
   screenshot is taken, and up to 3 screenshots, the named transcript and your
   question go to Nemotron 3.5 Super VL on Nebius Token Factory. The answer (at most
   two short sentences) is spoken through NVDA or JAWS if one is running, otherwise the Windows voice. The first sentence
   is spoken as soon as it arrives.

## Where NVIDIA Nemotron and Nebius Token Factory are used

| Job | Model | Where |
|---|---|---|
| Answer from screenshots + transcript + question | Nemotron 3.5 Super VL | Nebius Token Factory |
| Transcribe meeting audio segments | Whisper large-v3 turbo (configurable) | Groq |
| Read the active speaker's name from a screenshot | Nemotron 3.5 Super VL (the answer model) | Nebius Token Factory |
| Silence skipping | Silero VAD (or energy fallback) | local |
| Speech output | NVDA / JAWS / Windows SAPI | local |

The provider is config only (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); Nebius
Token Factory and OpenRouter are both OpenAI-compatible. Measured with Nebius:
answers start in about 1.4 s, speaker names take about 2 s.

Speech-to-text has its own settings (`STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`,
`STT_API`), because Nebius Token Factory does not accept audio. Groq's
`whisper-large-v3-turbo` transcribes a sentence in 0.5 to 0.8 s.
`STT_API=chat` sends audio to a chat model (Voxtral, Qwen Omni, Gemini, GPT Audio);
`STT_API=transcriptions` uses an OpenAI-style `/audio/transcriptions` endpoint for
Whisper-type models. Compare models with `scripts/stt_compare.py`.

## Setup (run from source)

Requires Windows 10/11 and Python 3.11 or newer.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e ".[dev,eval]"
```

Optional extra: `.[screenreader]` for direct NVDA / JAWS output.

## Run

```bash
.venv\Scripts\python -m app.main
```

On first run a **settings window** asks for the provider, API key and models
(Nebius Token Factory and Groq Whisper are pre-filled; paste a Nebius key for answers
and a Groq key, from console.groq.com/keys, for speech). **Test connection** checks both the answer
model and the speech model. Settings are saved in
`%APPDATA%\MeetingAssistant\settings.env`; developers can use a `.env` in the
project folder instead (see `.env.example`), which then takes priority. Never commit `.env`.

The main window has a question box (Enter asks; empty asks "what is on screen"),
buttons for Start/Stop meeting transcript, Ask, Repeat, Stop speaking and Settings,
and a log of every answer and transcript line that a screen reader can review. All controls are native Windows controls
with Alt shortcuts. Try it without any API key (fake answers):

```bash
.venv\Scripts\python -m app.main --stub
```

`--console` runs without a window (type `m` to start or stop the transcript, or a
question). The global hotkeys work from any app in both modes:

| Keys | Action |
|---|---|
| Ctrl+Alt+M | Start / stop the meeting transcript (saved with speaker names) |
| Ctrl+Alt+Space | Ask "what is on screen and what was just said?" (transcript must be running) |
| Ctrl+Alt+R | Repeat the last answer |
| Ctrl+Alt+X | Stop speaking |
| Ctrl+Alt+S | Status: transcript lines saved, last speaker, errors |

Window shortcuts: Alt+M start/stop transcript, Alt+Q question box, Alt+A ask,
Alt+R repeat, Alt+T stop speaking, Alt+G settings, Alt+L log.

Settings for this (in `settings.env` or `.env`): `TRANSCRIPT_DIR` changes the folder,
`SPEAKER_NAMES=off` skips the name lookup (one image call each time the screen changes).

## Windows .exe

Build a single-file `MeetingAssistant.exe` (about 60 MB) and a zip to share:

```bash
.venv\Scripts\python -m pip install -e ".[build]"
```

```bash
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Output: `dist\MeetingAssistant\MeetingAssistant.exe` and `dist\MeetingAssistant-win64.zip`.
Double-click the exe; the settings window opens on first run.
`MeetingAssistant.exe --stub` runs without keys.

The exe is not code-signed. Windows SmartScreen shows a warning ("More info" →
"Run anyway"), and on PCs with **Smart App Control** turned on Windows blocks it
completely. On those PCs set up from source (above) and double-click
`MeetingAssistant.bat` instead: it starts the same app with the project's Python,
which Windows trusts because Python itself is signed.

## Checks and measurements

```bash
.venv\Scripts\python -m pytest
```

Day-1 smoke test against the provider in `.env` (one image call, one audio call in
both audio block formats, latency recorded to `eval/results/`):

```bash
.venv\Scripts\python scripts\smoke_test.py
```

Live transcription from system audio, printing each line and its delay (add
`--speak-test` to play test sentences through the speakers):

```bash
.venv\Scripts\python scripts\live_transcribe.py --seconds 60
```

Compare speech-to-text models (WER and latency):

```bash
.venv\Scripts\python scripts\stt_compare.py
```

Word error rate on recorded clips (`name.wav` + `name.txt` pairs):

```bash
.venv\Scripts\python scripts\wer.py fixtures\clips
```

Answer latency for every question is logged to `eval/results/latency.jsonl`
(numbers only). Results will be reported here from real measurements.

## Privacy and consent

- Screenshots are sent to Nebius Token Factory and meeting audio to Groq, to be
  answered and transcribed.
- Nothing is captured until you press Ctrl+Alt+M, and capture stops when you press
  it again.
- The meeting transcript (text with speaker names) is saved in
  `Documents\Meeting Assistant`. Screenshots and audio are never saved: only the last
  30 seconds of screenshots are kept, in memory. The timing log has no audio,
  images or text.
- You must have the consent of the other participants to capture meeting audio.
  Follow your organisation's rules and local law.

## License

MIT, see [LICENSE](LICENSE).
