# Saarathi

A Windows desktop app for blind and low-vision people in video meetings. When a
colleague shares their screen and says "look at this", press a hotkey (or type a
question) and hear a short spoken answer about what is shown and what was said, and by
whom. Works next to any meeting app (Zoom, Teams, Google Meet), because it
captures the screen and the system audio rather than plugging into the meeting.

Built for the Nebius x NVIDIA Global AI Hackathon (track: Best Apps and Agents).

## How it works

1. Press **Ctrl+Alt+M** to start the meeting transcript. Nothing is captured before that.
2. Every second (`FRAME_INTERVAL_SECONDS`) the app takes a screenshot, downscales it and
   keeps the last 30 seconds in memory. A screen that hasn't changed is not stored twice.
3. System audio (what your speakers play) is captured through WASAPI loopback, leaving out
   the screen reader (NVDA, JAWS or Narrator), so its voice is never transcribed (Windows 10
   2004 or newer; older Windows captures everything).
   Voice-activity detection drops silence and cuts speech at pauses; each segment
   is transcribed while the meeting goes on, by a separate speech-to-text model.
4. The speaker's name is read from the meeting screen: the screenshot taken while
   they spoke goes to the answer model, which reads the highlighted tile, speaking
   indicator or caption name (Teams, Zoom, Meet). If no name is visible the line says
   "Speaker". Each line is saved at once to
   `Documents\Saarathi\Meeting <date> <time>.txt`, e.g.
   `[10:41:05] Host: the button moves to the top right`.
5. While the transcript is running, questions are about the last 30 seconds. When you ask, a fresh
   screenshot is taken, and up to 4 screenshots (`MAX_FRAMES_PER_QUESTION`), the named transcript and your
   question go to Nemotron 3.5 Super VL on Nebius Token Factory, with your last 3
   questions and answers so follow-ups work ("what did they scroll?" → "Messages.").
   These are kept in memory only and cleared when a new transcript starts. The answer (at most
   two short sentences) is in the meeting's language and is spoken by an offline human voice
   (Piper: English, Tamil, Hindi), or by NVDA / JAWS if chosen in Settings. The first sentence
   is spoken as soon as it arrives.
6. Every different screen of the recording is also kept (in memory only). When a screen
   stays up for 3 seconds, Nemotron writes a one-line note on it, saved in the transcript
   as `[10:02:05] Screen: Slide "Yesterday's status" ...`.
7. After you stop the transcript (Ctrl+Alt+M) you can still ask about the whole recording,
   e.g. "What was on the yesterday's status page?". Nemotron answers in two steps: it picks
   the screens that answer the question from the notes and transcript, then reads those
   screenshots and answers in up to five short sentences. Starting a new transcript or
   closing the app clears the recording.

## Where NVIDIA Nemotron and Nebius Token Factory are used

| Job | Model | Where |
|---|---|---|
| Answer from screenshots + transcript + question | Nemotron 3.5 Super VL | Nebius Token Factory |
| Transcribe meeting audio segments | Sarvam saaras:v4 (configurable) | Sarvam AI |
| Read the active speaker's name from a screenshot | Nemotron 3.5 Super VL (the answer model) | Nebius Token Factory |
| Note on each screen of the recording; pick and read screens for questions after it | Nemotron 3.5 Super VL | Nebius Token Factory |
| Silence skipping | Silero VAD (or energy fallback) | local |
| Speech output | Piper human voices (English, Tamil, Hindi...), or NVDA / JAWS / Windows SAPI | local, offline |

The provider is config only (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); Nebius
Token Factory and OpenRouter are both OpenAI-compatible. Measured with Nebius in real
meetings (23-27 Oct 2026): the first spoken sentence comes in about 2.5 s with 3 screenshots
and 3.5-4.7 s with 6; questions after a recording (two Nemotron calls) take about 2.2-2.9 s.

Speech-to-text has its own settings (`STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`,
`STT_API`), because Nebius Token Factory does not accept audio. The default is
Sarvam AI `saaras:v4` (`STT_API=sarvam`, Indian languages and English, language detected
automatically or set with `STT_LANGUAGE`, e.g. `ta-IN`). Sarvam is told to listen for names in
`STT_KEYTERMS` (Settings: *Speech key words*); mishearings that keyterms do not fix are
rewritten with `STT_FIXES` (by default "Vision 18" -> "VisionAid team").
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
(Nebius Token Factory and Sarvam AI saaras:v4 are pre-filled; paste a Nebius key for answers
and a Sarvam key, from dashboard.sarvam.ai, for speech). **Test connection** checks both the answer
model and the speech model. Settings are saved in
`%APPDATA%\Saarathi\settings.env`; developers can use a `.env` in the
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

## Human voice (offline, no API)

Answers are read by human-sounding [Piper](https://github.com/rhasspy/piper) voices that run
on your PC, with no API and no internet. One voice file per language; each part of an answer
goes to the voice for its script, so an answer that mixes English and Tamil is read by both.
Choose **Answer voice** in Settings (Alt+G): *Human voice*, or *Screen reader* for NVDA / JAWS.
If no voice files are found, the screen reader is used.

Voice files (`.onnx` + `.onnx.json`) go in `%APPDATA%\Saarathi\voices` (or `VOICE_DIR`).
The file name starts with the language: `en_...`, `ta_...`, `hi_...`, `te_...`, `ml_...`.
English and Hindi (and Telugu, Malayalam, Marathi) come from the official Piper list:

```bash
.venv\Scripts\python -m piper.download_voices --download-dir "%APPDATA%\Saarathi\voices" en_US-lessac-medium hi_IN-priyamvada-medium
```

Tamil: `ta_IN-rasa_male-medium.onnx` and `.onnx.json` from
[tinisoft/piper-ta_IN-rasa_male-medium](https://huggingface.co/tinisoft/piper-ta_IN-rasa_male-medium),
trained on the [AI4Bharat Rasa](https://huggingface.co/datasets/ai4bharat/Rasa) dataset (CC-BY-4.0).

## Install and run Saarathi.exe (version 1.0)

Saarathi 1.0 is one file, `Saarathi.exe` (about 95 MB). There is no installer and no
Python to install.

1. Copy `Saarathi.exe` to any folder, for example `Documents` or the Desktop.
2. Press Enter on it. Windows may show **"Windows protected your PC"**, because the exe is not
   code-signed yet. Choose **More info**, then **Run anyway**. With NVDA or JAWS: press Tab
   to reach "More info", press Enter, then Tab to the "Run anyway" button and press Enter.
   Windows asks only once for each copy of the file.
3. The first start takes a few seconds up to about 20 seconds, while the app unpacks itself once to
   `%LOCALAPPDATA%\VisionAid\Saarathi\1.0.0`. Later starts are quick.
4. The **Saarathi settings** window opens. Paste your Nebius Token Factory key (answers)
   and your Sarvam AI key (speech, from dashboard.sarvam.ai). Choose **Test connection**,
   then **Save**. The window title now says **Saarathi 1.0** and the app says "Saarathi ready".
5. For the human voices, copy the `voices` folder to `%APPDATA%\Saarathi\voices`
   (see Human voice). Without it, answers are read by NVDA, JAWS or the Windows voice.

If the app does not start:

- **"VCRUNTIME140.dll" or "MSVCP140.dll" was not found:** install the free Microsoft
  Visual C++ Redistributable (x64) from Microsoft, then start Saarathi again.
- **There is no "Run anyway" button:** Smart App Control is on, and it blocks every
  unsigned app. On that PC set up from source (see Setup) and press Enter on
  `Saarathi.bat`. It starts the same app with the project's Python, which Windows trusts.
- **A file from the internet is still blocked:** open the file's Properties (Alt+Enter),
  check **Unblock**, and choose OK.
- To try the app without keys (fake answers), run `Saarathi.exe --stub`.

To remove Saarathi, delete `Saarathi.exe`, then delete `%LOCALAPPDATA%\VisionAid\Saarathi`
(unpacked app) and `%APPDATA%\Saarathi` (your keys, voices and settings). Transcripts stay in
`Documents\Saarathi` until you delete them.

## How Saarathi became one exe

Saarathi is written in Python. To ship it as one exe we used **Nuitka**, which turns the
Python code into C and compiles it with a C compiler (MinGW gcc, which Nuitka downloads
by itself). Nuitka then packs the program, Python and every library (wxPython, numpy,
onnxruntime, Piper, the voice-activity model, the NVDA / JAWS helpers) into a single file
with the VisionAid logo as its icon. When the exe starts, it unpacks that bundle once and
runs from it.

Version 1.0 was tested on its own in a separate folder: the window opened in 5 s, a live
question was answered in 1.6 s and a question after the recording in 2.1 s.

## Build the exe yourself

1. Set up from source first (see Setup).
2. Install Nuitka:

   ```bash
   .venv\Scripts\python -m pip install nuitka ordered-set zstandard
   ```

3. Build:

   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\build_nuitka.ps1
   ```

4. The exe is `C:\Saarathi-build\Saarathi.exe`. The first build takes 1-2 hours; later builds
   take less because the compiler cache is reused. Close other programs while it builds: it
   runs much faster. The build folder is outside OneDrive on purpose, because OneDrive locks
   the build files.

For a new version, change the version number in `app/config.py` (`APP_VERSION`),
`pyproject.toml` and `scripts/build_nuitka.ps1`.

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

- Screenshots are sent to Nebius Token Factory and meeting audio to Sarvam AI, to be
  answered and transcribed.
- Nothing is captured until you press Ctrl+Alt+M, and capture stops when you press
  it again.
- The meeting transcript (text with speaker names) is saved in
  `Documents\Saarathi`, with Nemotron's one-line notes on each screen.
  Screenshots and audio are never saved to disk: the recording's screenshots are kept in
  memory only and are gone when a new transcript starts or the app closes. The timing
  log has no audio, images or text.
- You must have the consent of the other participants to capture meeting audio.
  Follow your organisation's rules and local law.

## License

MIT, see [LICENSE](LICENSE).
