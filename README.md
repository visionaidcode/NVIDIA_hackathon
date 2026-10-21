# Meeting Screen Assistant

A Windows desktop app for blind and low-vision people in video meetings. When a
colleague shares their screen and says "look at this", press a hotkey (or type a
question) and hear a short spoken answer about what was shown and said in the last
30 seconds. Works next to any meeting app (Zoom, Teams, Google Meet), because it
captures the screen and the system audio rather than plugging into the meeting.

Built for the Nebius x NVIDIA Global AI Hackathon (track: Best Apps and Agents).

## How it works

1. Every ~2 s the app takes a screenshot, downscales it and keeps it in memory.
   A screen that hasn't changed is not stored twice.
2. System audio (what your speakers play) is captured through WASAPI loopback.
   Voice-activity detection drops silence and cuts speech at pauses; each segment
   is transcribed while the meeting goes on, by a separate speech-to-text model.
3. Only the last 30 seconds of screenshots and transcript are kept, in memory.
4. When you ask, up to 3 screenshots, the transcript and your question go to
   Nemotron 3 Nano Omni. The answer (at most two short sentences) is spoken through
   NVDA or JAWS if one is running, otherwise the Windows voice. The first sentence
   is spoken as soon as it arrives.

## Where NVIDIA Nemotron and Nebius Token Factory are used

| Job | Model | Where |
|---|---|---|
| Answer from screenshots + transcript + question | Nemotron 3 Nano Omni | Nebius Token Factory |
| Transcribe meeting audio segments | Voxtral Small 24B (configurable) | OpenRouter |
| Silence skipping | Silero VAD (or energy fallback) | local |
| Speech output | NVDA / JAWS / Windows SAPI | local |

During development Nemotron is called through OpenRouter. The provider is config
only (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); both are OpenAI-compatible. The
submitted build calls Nebius Token Factory for the screen answers.

Speech-to-text has its own settings (`STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`,
`STT_API`), because OpenRouter's free Nemotron Omni endpoint drops audio input.
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

Optional extras: `.[vad]` for Silero VAD (installs PyTorch, large) and
`.[screenreader]` for direct NVDA / JAWS output.

Copy `.env.example` to `.env` and fill in `LLM_API_KEY` (and for Nebius, the base
URL and Omni model ID from the Token Factory docs and Models page). Never commit `.env`.

## Run

```bash
.venv\Scripts\python -m app.main
```

Try it without any API key (fake answers):

```bash
.venv\Scripts\python -m app.main --stub
```

Type a question in the console window and press Enter, or use the global hotkeys:

| Keys | Action |
|---|---|
| Ctrl+Alt+Space | Ask "what is on screen and what was just said?" |
| Ctrl+Alt+R | Repeat the last answer |
| Ctrl+Alt+X | Stop speaking |
| Ctrl+Alt+S | Status: what is being captured |
| Ctrl+Alt+P | Pause capture and clear memory / resume |

## Windows .exe

Build a single-file `MeetingAssistant.exe` (about 50 MB) and a zip to share:

```bash
.venv\Scripts\python -m pip install -e ".[build]"
```

```bash
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Output: `dist\MeetingAssistant\MeetingAssistant.exe` and `dist\MeetingAssistant-win64.zip`.
Put a `.env` (copied from `.env.example`, with your keys) in the same folder as
the exe, then run it. `MeetingAssistant.exe --stub` runs without keys.

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

- Screenshots and meeting audio are sent to the model provider (Nebius Token Factory,
  or OpenRouter during development) to be transcribed and answered.
- Only the last 30 seconds are kept, in memory. Nothing is written to disk except
  the timing log, which has no audio, images or text.
- Ctrl+Alt+P pauses capture and clears memory.
- You must have the consent of the other participants to capture meeting audio.
  Follow your organisation's rules and local law.

## License

MIT, see [LICENSE](LICENSE).
