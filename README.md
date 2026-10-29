![Saarthi by Vision-Aid](packaging/logo.png)

# Saarthi: your guide to accessible meetings

> **Download Saarthi.exe** (Windows 10/11, one file, no installer) from the
> [**Releases** page of this repository](https://github.com/visionaidcode/NVIDIA_hackathon/releases).
> The exe is not in the code itself. You need your own Nebius Token Factory and Sarvam AI API keys
> (see [Not in this repository](#not-in-this-repository-what-you-need-to-get-yourself)), and then
> follow [Install and run Saarthi.exe](#install-and-run-saarthiexe).

Saarthi is a Windows desktop app for blind and low-vision people in video meetings. When a
colleague shares their screen and says "move this from here to here", press a hotkey (or type a
question) and hear a short spoken answer about what is shown, what was said, and by whom.

It works next to any meeting app (Zoom, Teams, Google Meet), because it captures the screen and
the system audio rather than plugging into the meeting. After the meeting, you can ask about
anything in it, without saying a time, and choose any earlier meeting to ask about.

Saarthi (Sanskrit: a guide) is made by [Vision-Aid](https://visionaid.org). Built for the Nebius
x NVIDIA Global AI Hackathon (track: Best Apps and Agents).

## What it does

- **During the meeting:** ask about the last 30 seconds. Saarthi sends the latest screenshots,
  the transcript with speaker names and your question to NVIDIA Nemotron, and speaks the answer
  in at most two short sentences.
- **After the meeting:** ask about anything in the whole meeting. Saarthi finds the right minutes
  itself (RAG), and answers from their screenshots, their transcript and the meeting summaries,
  in up to five sentences.
- **Any earlier meeting:** every meeting is saved in its own folder. Choose one in the main window
  and ask about it.
- **Stay in control:** global hotkeys work from any app; every control works with NVDA and JAWS.
- **Follow-ups:** your last 3 questions and answers are sent along, so "and who said that?" works.
- **Languages:** answers come in the meeting's language (English, Tamil, Hindi and more), read by
  offline human voices or your screen reader.

## Keyboard shortcuts

Saarthi is made to be used with the keyboard only. Every control has a shortcut, and NVDA and
JAWS read every control by its name.

### Global shortcuts (work in any app)

These work while Zoom, Teams, Meet, a browser or any other app is in front, so you never have to
leave the meeting to use Saarthi.

| Keys | Action | Details |
|---|---|---|
| **Ctrl+Alt+M** | Start / stop the meeting transcript | Starting creates a new meeting folder and starts capturing the screen and the system audio. Stopping finishes the last lines, notes and summary, adds the end time to the folder name, and says where it was saved. Nothing is captured before you press it. |
| **Ctrl+Alt+Space** | Ask | While recording: asks "What is on the screen and what was just said about it?" about the last 30 seconds. After stopping: asks the same about the chosen meeting. To ask your own question, type it in the main window (Alt+Q). |
| **Ctrl+Alt+R** | Repeat the last answer | Says the last answer again, from the start. |
| **Ctrl+Alt+X** | Stop speaking | Stops the answer being spoken right away. |
| **Ctrl+Alt+S** | Status | Says what is happening: whether the transcript is running, how many lines are saved, the last speaker, which meeting questions are about, and any error (screen, audio, speech-to-text, search). |

### Main window

| Keys | Goes to / does | Details |
|---|---|---|
| **Alt+Q** | Question box | Type a question and press **Enter** to ask. Pressing Enter on an empty box asks "What is on the screen and what was just said about it?". |
| **Alt+A** | Ask button | Asks the question in the box. |
| **Alt+M** | Start / Stop meeting transcript button | Same as Ctrl+Alt+M. The button's name changes to "Stop meeting transcript" while recording. |
| **Alt+P** | Past meeting to ask about | A list of your saved meetings, newest first, e.g. "29 October 2026, 14:58 to 15:18". Move with the **Up / Down arrow** keys. Greyed out while recording. |
| **Alt+U** | Use this meeting button | Questions are now about the meeting chosen in the list. Saarthi says which meeting and how many lines it has. |
| **Alt+R** | Repeat answer button | Same as Ctrl+Alt+R. |
| **Alt+T** | Stop speaking button | Same as Ctrl+Alt+X. |
| **Alt+G** | Settings button | Opens the settings window. |
| **Alt+L** | Answers, transcript and log | Every question, answer and transcript line, with its time. Read it with the arrow keys; it cannot be typed in. |

**Tab** and **Shift+Tab** move between the controls. The status bar at the bottom (read it with
your screen reader's status bar key, e.g. NVDA+End or JAWS Insert+Page Down) shows the same as
Ctrl+Alt+S.

### Settings window (Alt+G)

Saarthi always uses the same services, so only the two API keys and a few choices can be
changed. The services are shown read-only (still reached with Tab and read by screen readers).

| Keys | Goes to | Can be changed |
|---|---|---|
| **Alt+F** | Settings file (where settings are saved) | no |
| **Alt+P** | Provider: Nebius Token Factory | no |
| **Alt+K** | API key (Nebius) | yes |
| **Alt+S** | Show keys (shows the keys instead of dots) | yes |
| **Alt+M** | Model ID: nvidia/Nemotron-3.5-Super-VL | no |
| **Alt+A** | Speech API type: Sarvam AI speech to text (saaras) | no |
| **Alt+E** | Speech API key (Sarvam) | yes |
| **Alt+O** | Speech model ID: saaras:v4 | no |
| **Alt+C** | Answer voice: human voice or screen reader | yes |
| **Alt+Y** | Meeting summary: every 2.5 minutes or every 5 minutes | yes |
| **Alt+I** | Save every screenshot in the meeting folder | yes |
| **Alt+T** | Test connection | |
| **Alt+V** or **Enter** | Save | |
| **Esc** | Cancel | |

**Test connection** checks the answer model, the speech model and the meeting search model
(Qwen3-Embedding-8B on Nebius, used with the same Nebius key), and says which ones work.

### Console mode (`--console`, no window)

| Type | Then Enter | Action |
|---|---|---|
| `m` | Enter | Start / stop the meeting transcript |
| a question | Enter | Ask it |
| nothing | Enter | Ask "What is on the screen and what was just said about it?" |
| `q`, `quit` or `exit` | Enter | Close Saarthi (or Ctrl+C) |

The global shortcuts work in console mode too.

## Using Saarthi, step by step

1. **Start Saarthi** (`Saarthi.exe`, or `Saarthi.bat` from source). It says "Saarthi ready".
2. **Join your meeting** in Zoom, Teams or Meet as usual.
3. **Press Ctrl+Alt+M** to start the transcript. Saarthi says the folder it saves to, e.g.
   "Meeting 29-10-2026 14-58".
4. **During the meeting**, press **Ctrl+Alt+Space** whenever you want to know what is on the
   screen, or switch to Saarthi (Alt+Tab), press **Alt+Q**, type a question such as "Where did the
   Sign up button move?" and press Enter. The answer is spoken in one or two sentences. Ask a
   follow-up such as "Why?" and Saarthi remembers your last 3 questions.
5. **Press Ctrl+Alt+M** again when the meeting ends. Saarthi says "Preparing the meeting for
   questions", then where the meeting was saved.
6. **After the meeting**, ask anything about it, e.g. "What did Priya say about the launch date?"
   or "What were the numbers on the sales slide?". You don't need to say when it happened.
7. **To ask about an older meeting**, press **Alt+P** in the main window, choose it with the arrow
   keys, press **Alt+U**, then ask.
8. **Press Ctrl+Alt+R** to hear an answer again, **Ctrl+Alt+X** to stop it.

## How it works

1. Press **Ctrl+Alt+M** to start. Nothing is captured before that.
2. Every second Saarthi takes a screenshot and keeps the last 30 seconds in memory. A screen that
   hasn't changed is not stored twice.
3. System audio is captured through WASAPI loopback, leaving out the screen reader, so its voice
   is never transcribed. Voice-activity detection (Silero) cuts speech at pauses, and each piece
   is transcribed while the meeting goes on (Sarvam AI saaras:v4).
4. The speaker's name is read from the meeting screen (highlighted tile, speaking indicator or
   caption). If no name is visible, the line says "Speaker".
5. Every different screen is saved as a picture named by its time, and every line of the
   transcript is written at once, so nothing is lost if the app closes.
6. While recording, the meeting is made searchable (`app/rag`):
   - it is cut into **1-minute parts** (what was said, with names, and notes on the screens), and
     each part gets an embedding from **Qwen3-Embedding-8B** on Nebius Token Factory;
   - every **2.5 minutes** Nemotron writes a short summary that keeps names, numbers, dates,
     decisions and tasks.
7. When you stop (Ctrl+Alt+M), the last minutes are indexed and the folder name gets the end time.

### Questions after the meeting (RAG)

You never have to say a time. For each question Saarthi:

1. **Finds** the 3 best 1-minute parts in two ways and merges the rankings: by meaning (the
   question's embedding against each part's) and by keywords (BM25, for exact names and numbers).
   Searching by meaning lets a Tamil question find an English discussion.
2. **Sends** Nemotron in one call:
   - all the summaries, for the whole meeting at a glance;
   - the transcript: all of it for a meeting of about 20 minutes or less, otherwise the found
     minutes word for word;
   - every screenshot shown in those minutes, each with its exact time (at most 8, Nemotron's limit
     per request on Nebius; near-identical screens are left out first and their notes are sent as
     text);
   - your question and your last questions and answers.
3. **Speaks** the answer as it arrives, in up to five short sentences.

The last meeting is loaded again when Saarthi starts. To ask about an earlier one, choose it in
**Past meeting to ask about** (Alt+P) and press **Use this meeting** (Alt+U). Meetings are listed
newest first, e.g. "29 October 2026, 14:58 to 15:18".

### Where each meeting is saved

```
Documents\Saarthi\Meeting 29-10-2026 14-58 to 15-18\
    transcript.txt     what was said, with times and names, screen notes and summaries
    screens\           every different screenshot, e.g. 14-58-27.jpg
    meeting.json       the search index: lines, screens, 1-minute parts with embeddings, summaries
```

The folder name is the date (day-month-year), the start time and the end time. Plain files, no
database: delete the folder to delete the meeting.

## Models

| Job | Model | Where |
|---|---|---|
| Answers from screenshots + transcript + question | NVIDIA Nemotron 3.5 Super VL | Nebius Token Factory |
| Speaker names, screen notes, summaries every 2.5 minutes | NVIDIA Nemotron 3.5 Super VL | Nebius Token Factory |
| Finding the minutes that answer a question (embeddings) | Qwen3-Embedding-8B | Nebius Token Factory |
| Meeting audio to text | Sarvam saaras:v4 | Sarvam AI |
| Silence skipping | Silero VAD | on your PC |
| Speech output | Piper voices, or NVDA / JAWS / Windows voice | on your PC, offline |

Providers and models are settings only (OpenAI-compatible APIs), so they can be changed
without changing the code.

## Tested

On real recordings of YouTube tutorials played on the PC, with Nebius and Sarvam (October 2026):

- **7-minute meeting** (51 lines, 78 screens): 10 of 10 questions answered correctly.
- **20-minute meeting** (171 lines, 1,038 screens, 21 parts): 18 of 18 questions answered
  correctly, including details shown only on screen, a question in Tamil, a follow-up, and a
  question about something never mentioned (Saarthi said it was not in the meeting).
- **Both meetings through the meeting chooser:** 20 of 20 questions answered correctly.
- **The built Saarthi.exe, driven with the keyboard only** (on copies of both meetings): 33 of 33
  checks passed. It started in 5 seconds; every global and window shortcut worked; 12 questions
  about the two meetings were answered correctly (including on-screen details, a follow-up, Tamil
  and a question about something never mentioned); switching meetings, the Settings window, a new
  recording with its dated folder, and closing the app (1.2 seconds) all worked.
- First spoken words after a question about a recording: 2.6 to 9.1 seconds, about 5 seconds on
  average, depending on how busy Nebius was.

These are small team tests, not a benchmark. Meetings longer than 20 minutes are supported by
the design but not yet tested.

## Not in this repository: what you need to get yourself

Some things are kept out of GitHub on purpose (see `.gitignore`), because they are secret,
private, too large, or made on your own PC. Here is each one and how to get it.

| Not in the repository | Why | How to get it |
|---|---|---|
| **API keys** (Nebius Token Factory, Sarvam AI) | Secret: anyone with a key can use its credit | Make your own: a Nebius Token Factory key in the Nebius Token Factory console (answers, notes, summaries and meeting search) and a Sarvam AI key at [dashboard.sarvam.ai](https://dashboard.sarvam.ai) (speech to text). Paste them in the Settings window on first start; they are saved in `%APPDATA%\Saarthi\settings.env`, outside the project. Never put a key in the code, the README or an issue. |
| **`.env`** | Holds keys | Optional, for developers: copy `.env.example` to `.env` in the project folder and fill it in. |
| **`Saarthi.exe`** | About 200 MB, too large for git | Download it from the repository's **Releases** page if one is published, or build it yourself (see [Build the exe](#build-the-exe)). |
| **Piper voice files** (`.onnx` + `.onnx.json`) | About 60 MB each, with their own licenses | Download them into `%APPDATA%\Saarthi\voices` (see [Human voices](#human-voices-offline)). The exe has the English and Hindi voices built in. Without any voice file, answers are read by NVDA, JAWS or the Windows voice. |
| **Meeting recordings** (transcripts, screenshots, `meeting.json`) | Private meeting content | Made by Saarthi on your PC when you record, in `Documents\Saarthi\Meeting <date> <start> to <end>`. |
| **Python environment** (`.venv`) | Large and specific to each PC | Create it yourself (see [Run from source](#run-from-source)). |
| **Timing logs** (`eval/results/`) and the app log | Made on your PC | Written to `%APPDATA%\Saarthi\eval\results\latency.jsonl` and `%APPDATA%\Saarthi\saarthi.log`. |
| Build and cache folders (`build/`, `dist/`, `__pycache__/`, `.pytest_cache/`) | Made automatically | Nothing to do. |

The repository does include the voice-activity model (`app/audio/data/silero_vad.onnx`, small,
MIT license), so speech detection works with no download.

## Install and run Saarthi.exe

`Saarthi.exe` is one file with everything inside (Python, the libraries and the English and
Hindi voices). There is no installer. It is not in the repository itself: download it from the
**Releases** page, or build it (see [Build the exe](#build-the-exe)). You need your own API keys
(see [Not in this repository](#not-in-this-repository-what-you-need-to-get-yourself)).

1. Copy `Saarthi.exe` to any folder, for example the Desktop.
2. Press Enter on it. Windows may show **"Windows protected your PC"**, because the exe is not
   code-signed yet. Choose **More info**, then **Run anyway**. With NVDA or JAWS: press Tab to
   reach "More info", press Enter, then Tab to "Run anyway" and press Enter.
3. Each start takes a few seconds while the exe unpacks itself to a temporary folder.
4. The **Saarthi settings** window opens. Paste your Nebius Token Factory key (answers and
   search) and your Sarvam AI key (speech, from dashboard.sarvam.ai). Choose **Test connection**,
   then **Save**.
5. To see what the app is doing (which voice it uses, any error), press Windows+R and run
   `notepad %APPDATA%\Saarthi\saarthi.log`.

If the app does not start:

- **"VCRUNTIME140.dll" was not found:** install the free Microsoft Visual C++ Redistributable
  (x64), then start Saarthi again.
- **There is no "Run anyway" button:** Smart App Control blocks every unsigned app. Set up from
  source (below) and press Enter on `Saarthi.bat`, which starts the same app with Python.
- **A downloaded file is blocked:** open its Properties (Alt+Enter), check **Unblock**, choose OK.
- To try the app without keys (fake answers), run `Saarthi.exe --stub`.

To remove Saarthi, delete `Saarthi.exe` and `%APPDATA%\Saarthi` (keys, voices and settings).
Meetings stay in `Documents\Saarthi` until you delete them.

## Run from source

Requires Windows 10/11 and Python 3.11 or newer.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e ".[dev,eval,screenreader]"
```

```bash
.venv\Scripts\python -m app.main
```

Or press Enter on `Saarthi.bat`. Try it without any API key (fake answers):

```bash
.venv\Scripts\python -m app.main --stub
```

Command-line options (also for `Saarthi.exe`):

| Option | What it does |
|---|---|
| `--stub` | Fake answers, no network and no keys, to try the app |
| `--console` | No window; type in the console (see Console mode above) |
| `--no-hotkeys` | Don't register the global Ctrl+Alt shortcuts |

## Settings

On first start the **settings window** asks for your Nebius Token Factory key and your Sarvam
AI key (see "Settings window" above). Settings are saved in `%APPDATA%\Saarthi\settings.env`.
The settings window always writes Nebius, Nemotron, Sarvam saaras:v4 and Qwen3-Embedding-8B.
Developers can change any value below in a `.env` in the project folder (see `.env.example`),
which takes priority over `settings.env`. Never commit `.env`.

| Setting | What it does | Default |
|---|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Answer model | Nebius, Nemotron 3.5 Super VL |
| `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`, `STT_API` | Speech to text | Sarvam, saaras:v4 |
| `STT_LANGUAGE` | Meeting language, e.g. `ta-IN` (`.env` only; the Settings window clears it) | detected |
| `STT_KEYTERMS` | Names the speech model should listen for | VisionAid, NVIDIA... |
| `EMBED_MODEL` | Meeting search model (`none` = keyword search only) | Qwen/Qwen3-Embedding-8B |
| `SUMMARY_MINUTES` | Minutes between summaries (Settings: 2.5 or 5) | 2.5 |
| `SAVE_SCREENSHOTS` | Save every screenshot in the meeting folder | on |
| `MAX_RECORDING_IMAGES` | Screenshots per question after the meeting | 8 |
| `TRANSCRIPT_DIR` | Where meetings are saved | Documents\Saarthi |
| `SPEAKER_NAMES` | Read speaker names from the screen | on |
| `ANSWER_VOICE` | `human` (Piper) or `screenreader` (NVDA / JAWS) | human |
| `VOICE_DIR` | Folder with extra Piper voices | %APPDATA%\Saarthi\voices |
| `WINDOW_SECONDS` | How far back a question during the meeting looks | 30 |
| `FRAME_INTERVAL_SECONDS` | Seconds between screenshots | 1 |
| `MAX_FRAMES_PER_QUESTION` | Screenshots per question during the meeting | 4 |
| `FRAME_MAX_SIDE`, `JPEG_QUALITY` | Screenshot size and quality | 1280, 70 |
| `STT_FIXES` | Fix words the speech model often mishears | "Vision 18" -> "VisionAid team" |

`STT_API=transcriptions` uses an OpenAI-style `/audio/transcriptions` endpoint (Whisper-type
models), and `STT_API=chat` sends audio to a chat model. Compare models with
`scripts\stt_compare.py`.

## Human voices (offline)

Answers can be read by human-sounding [Piper](https://github.com/rhasspy/piper) voices that run
on your PC with no internet. Choose **Answer voice** in Settings: *Human voice*, or *Screen
reader* for NVDA / JAWS. Each part of an answer goes to the voice for its script, so an answer
that mixes English and Tamil is read by both.

Voice files (`.onnx` + `.onnx.json`) go in `%APPDATA%\Saarthi\voices`; the file name starts with
the language (`en_`, `ta_`, `hi_`...). A voice there wins over the one built into the exe.

```bash
.venv\Scripts\python -m piper.download_voices --download-dir "%APPDATA%\Saarthi\voices" en_US-lessac-medium hi_IN-priyamvada-medium
```

Tamil: `ta_IN-rasa_male-medium` from
[tinisoft/piper-ta_IN-rasa_male-medium](https://huggingface.co/tinisoft/piper-ta_IN-rasa_male-medium).

| Voice | Source | Training data license |
|---|---|---|
| English `en_US-lessac-medium` | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) | [Lessac Blizzard 2013](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html) (research use) |
| Hindi `hi_IN-priyamvada-medium` | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) | AI4Bharat IndicNLP, CC BY-NC-SA 4.0 (non-commercial) |
| Tamil `ta_IN-rasa_male-medium` | [tinisoft](https://huggingface.co/tinisoft/piper-ta_IN-rasa_male-medium) | AI4Bharat Rasa, CC-BY-4.0 |

## Build the exe

Saarthi.exe is built with [PyInstaller](https://pyinstaller.org), which packs the Python code,
Python itself and every library (wxPython, numpy, onnxruntime, Piper and the voices, the
voice-activity model, the NVDA / JAWS helpers) into one file with the Saarthi logo as its icon.

1. Set up from source first, and put the voices to build in into `%APPDATA%\Saarthi\voices`.
2. Install PyInstaller:

   ```bash
   .venv\Scripts\python -m pip install -e ".[build,screenreader]"
   ```

3. Build (a few minutes):

   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\build_pyinstaller.ps1
   ```

4. The exe is `C:\Saarthi-build\Saarthi.exe`. The build folder is outside OneDrive on purpose,
   because OneDrive locks the build files.

For a new version, change `APP_VERSION` in `app/config.py`, `version` in `pyproject.toml` and the
numbers in `packaging/version_info.txt`.

## Project layout

| Folder | What is in it |
|---|---|
| `app/main.py` | The app: hotkeys, recording, questions |
| `app/ui/` | Main window and settings window |
| `app/screen/`, `app/audio/`, `app/stt/` | Screen capture, system audio, speech to text, speaker names |
| `app/memory/` | Last 30 seconds, screen history, the transcript file |
| `app/rag/` | Meeting folder (`store.py`), parts, embeddings and summaries (`index.py`), search (`search.py`) |
| `app/qa/` | Questions during the meeting (`ask.py`) and after it (`review.py`) |
| `app/llm/`, `app/speech/` | Model calls (Nebius), spoken answers |
| `tests/` | Automated tests |
| `scripts/` | Exe build, smoke test, speech-to-text comparison |
| `packaging/` | Logo, icon, exe details |

## Checks

```bash
.venv\Scripts\python -m pytest
```

Smoke test against your provider (one image call, one audio call):

```bash
.venv\Scripts\python scripts\smoke_test.py
```

Answer timings are logged to `%APPDATA%\Saarthi\eval\results\latency.jsonl` (numbers only).

## Privacy and consent

- You must have the consent of the other participants to capture a meeting. Follow your
  organisation's rules and local law.
- Nothing is captured until you press Ctrl+Alt+M, and capture stops when you press it again.
- Screenshots and the meeting text are sent to Nebius Token Factory (answers, notes, summaries
  and search), and meeting audio to Sarvam AI (speech to text).
- Each meeting is saved on your PC in `Documents\Saarthi`: the transcript, every different
  screenshot, and `meeting.json`. If Documents is in OneDrive, it is synced to the cloud; set
  `TRANSCRIPT_DIR` to keep it on the PC only.
- To keep screenshots off the disk, untick *Save every screenshot in the meeting folder* in
  Settings (`SAVE_SCREENSHOTS=off`): they are then kept in memory until the app closes.
- Audio is never saved. The timing log has no audio, images or text.
- Use a non-confidential sample meeting when testing.

## License

MIT, see [LICENSE](LICENSE).
