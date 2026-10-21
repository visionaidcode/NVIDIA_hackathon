"""Live transcription check: capture system audio and print each transcript line as it arrives.

    python scripts/live_transcribe.py                 # listen for 60 s (e.g. during a test meeting)
    python scripts/live_transcribe.py --seconds 120
    python scripts/live_transcribe.py --speak-test    # play test sentences through the speakers

Shows, for each line, how long after the speaker stopped the text was ready.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio.loopback import LoopbackCapture  # noqa: E402
from app.audio.vad import SpeechSegmenter, make_vad  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.eval.latency import LatencyLog  # noqa: E402
from app.llm.client import make_client  # noqa: E402
from app.memory.buffer import RollingBuffer, TranscriptLine  # noqa: E402
from app.stt.transcribe import Transcriber  # noqa: E402

TEST_SENTENCES = [
    "Okay, can everyone see my screen?",
    "This is the new home page. We are moving the sign up button to the top right corner.",
    "Sales in March were forty two thousand dollars.",
    "Next week we will review the mobile version with the Bangalore team.",
]


def speak_test_sentences(stop: threading.Event) -> None:
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    time.sleep(2)
    for text in TEST_SENTENCES:
        if stop.is_set():
            return
        voice.Speak(text)  # blocking; plays through the default speakers
        time.sleep(2.5)    # a pause, like between speakers in a meeting


class PrintingBuffer(RollingBuffer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.delays: list[float] = []

    def add_line(self, line: TranscriptLine) -> None:
        delay = time.time() - line.end
        self.delays.append(delay)
        print(f"[{time.strftime('%H:%M:%S')}] (+{delay:.1f}s after speech ended) {line.text}", flush=True)
        super().add_line(line)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--speak-test", action="store_true")
    args = ap.parse_args()

    settings = load_settings(ROOT / ".env")
    client = make_client(settings)
    buffer = PrintingBuffer(settings.window_seconds)
    transcriber = Transcriber(client, buffer, LatencyLog(settings.latency_log))
    loop = asyncio.get_running_loop()

    vad = make_vad()
    capture = LoopbackCapture(SpeechSegmenter(vad),
                              lambda seg: loop.call_soon_threadsafe(transcriber.queue.put_nowait, seg))
    capture.start()
    print(f"Listening on {capture.device_name} with {type(vad).__name__}, "
          f"STT model {settings.stt_model}, for {args.seconds:.0f}s ...", flush=True)

    stop = threading.Event()
    if args.speak_test:
        seconds = max(args.seconds, 10 + 6 * len(TEST_SENTENCES))
        threading.Thread(target=speak_test_sentences, args=(stop,), daemon=True).start()
    else:
        seconds = args.seconds

    task = asyncio.create_task(transcriber.run())
    try:
        await asyncio.sleep(seconds)
        await asyncio.sleep(0)  # let queued segments start
        while not transcriber.queue.empty():
            await asyncio.sleep(0.5)
        await asyncio.sleep(4)  # last request in flight
    finally:
        stop.set()
        capture.stop()
        task.cancel()

    if transcriber.last_error:
        print(f"Last transcription error: {transcriber.last_error}")
    if buffer.delays:
        print(f"\n{len(buffer.delays)} lines. Delay after speech ended: median "
              f"{statistics.median(buffer.delays):.1f}s, worst {max(buffer.delays):.1f}s")
    else:
        print("\nNo speech transcribed.")


if __name__ == "__main__":
    asyncio.run(main())
