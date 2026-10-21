"""Day-1 smoke tests (spec section 8).

1. One text+image call to Omni.
2. One audio transcription call, trying both audio block formats.
3. Latency of each call, written to eval/results/smoke-<provider>.jsonl.

Run against whatever .env points to (OpenRouter now, Token Factory later):

    python scripts/smoke_test.py
    python scripts/smoke_test.py --audio fixtures/my_clip.wav --reference "words spoken in the clip"
    python scripts/smoke_test.py --stub        # plumbing check, no network

The default test audio is generated locally with Windows SAPI, so no real meeting
audio is sent. Use only recorded test clips with --audio.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.audio.wav import encode_wav, load_wav  # noqa: E402
from app.config import AUDIO_FORMATS, load_settings  # noqa: E402
from app.eval.latency import LatencyLog  # noqa: E402
from app.llm.client import OpenAICompatClient, StubClient  # noqa: E402
from app.memory.buffer import Frame, Snapshot, TranscriptLine  # noqa: E402
from app.qa.ask import answer_question  # noqa: E402
from app.stt.transcribe import clean_transcript  # noqa: E402

TTS_SENTENCE = ("On the home page we will move the sign up button to the top right corner, "
                "and sales in March were forty two thousand dollars.")

# Known values so the answer can be checked by hand.
CHART_TITLE = "Monthly sales (thousand USD)"
CHART_VALUES = {"Jan": 30, "Feb": 35, "Mar": 42}


def make_test_image() -> bytes:
    img = Image.new("RGB", (960, 540), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
        small = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = small = ImageFont.load_default()
    d.rectangle([0, 0, 960, 60], fill="#1f3b73")
    d.text((20, 15), "Home page redesign", fill="white", font=font)
    d.rounded_rectangle([780, 12, 940, 50], radius=8, fill="#2e7d32")
    d.text((805, 18), "Sign up", fill="white", font=small)
    d.text((60, 90), CHART_TITLE, fill="black", font=font)
    base, x = 480, 120
    for label, value in CHART_VALUES.items():
        top = base - value * 7
        d.rectangle([x, top, x + 120, base], fill="#4a78c2")
        d.text((x + 40, top - 30), str(value), fill="black", font=small)
        d.text((x + 35, base + 10), label, fill="black", font=small)
        x += 200
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def make_test_audio() -> bytes:
    """Speak TTS_SENTENCE with Windows SAPI into a WAV, then convert to 16 kHz mono."""
    import pyttsx3

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tts.wav"
        engine = pyttsx3.init()
        engine.save_to_file(TTS_SENTENCE, str(path))
        engine.runAndWait()
        return encode_wav(load_wav(path))


def wer(reference: str, hypothesis: str) -> float | None:
    try:
        import jiwer
    except ImportError:
        return None
    tr = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                        jiwer.RemoveMultipleSpaces(), jiwer.Strip(), jiwer.ReduceToListOfListOfWords()])
    return jiwer.wer(reference, hypothesis, reference_transform=tr, hypothesis_transform=tr)


async def test_image(client, log: LatencyLog) -> bool:
    print("\n== Test 1: text + image ==")
    now = time.time()
    snap = Snapshot(now, [Frame(now - 2, make_test_image(), 960, 540)],
                    [TranscriptLine(now - 6, now - 3, "so this is the sales chart for the first quarter")])
    question = "What was the sales figure for March, and what does the green button say?"
    try:
        result = await answer_question(client, snap, question)
    except Exception as e:  # noqa: BLE001 - report any provider error
        print(f"FAILED: {type(e).__name__}: {e}")
        log.write("smoke_image", ok=False, error=f"{type(e).__name__}: {e}"[:500])
        return False
    print(f"Q: {question}\nA: {result.text}")
    print(f"first sentence {result.first_sentence_s:.2f}s, total {result.total_s:.2f}s")
    print("Expected: March is 42 (thousand USD), button says 'Sign up'. Check the answer by hand.")
    log.write("smoke_image", ok=True, first_sentence_s=round(result.first_sentence_s or 0, 3),
              total_s=round(result.total_s, 3), answer=result.text)
    return True


async def test_audio(client, log: LatencyLog, wav: bytes, reference: str | None,
                     formats: list[str]) -> str | None:
    print("\n== Test 2: audio transcription ==")
    working = None
    for fmt in formats:
        t0 = time.perf_counter()
        try:
            text = (await client.transcribe(wav) if isinstance(client, StubClient)
                    else await client.transcribe(wav, fmt=fmt))
        except Exception as e:  # noqa: BLE001
            dt = time.perf_counter() - t0
            print(f"[{fmt}] FAILED after {dt:.2f}s: {type(e).__name__}: {e}")
            log.write("smoke_audio", format=fmt, ok=False, latency_s=round(dt, 3),
                      error=f"{type(e).__name__}: {e}"[:500])
            continue
        dt = time.perf_counter() - t0
        score = wer(reference, text) if reference else None
        print(f"[{fmt}] {dt:.2f}s -> {text!r}")
        if score is not None:
            print(f"[{fmt}] WER vs reference: {score:.1%}")
        # A reply like "(No output)" means the audio block was ignored, not transcribed.
        ok = bool(clean_transcript(text)) and (score is None or score < 0.5)
        if not ok:
            print(f"[{fmt}] not a usable transcript; the model probably did not receive the audio")
        log.write("smoke_audio", format=fmt, ok=ok, latency_s=round(dt, 3),
                  audio_s=round((len(wav) - 44) / 32000, 2), wer=score, transcript=text)
        if ok and working is None:
            working = fmt
    return working


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stub", action="store_true", help="use the stub client (no network)")
    ap.add_argument("--audio", type=Path, help="16-bit PCM WAV test clip (not private meeting audio)")
    ap.add_argument("--reference", help="what is said in --audio, for WER")
    ap.add_argument("--format", choices=AUDIO_FORMATS, help="try only this audio block format")
    ap.add_argument("--skip-image", action="store_true")
    ap.add_argument("--skip-audio", action="store_true")
    args = ap.parse_args()

    settings = load_settings(ROOT / ".env")
    provider = "stub" if args.stub else settings.provider
    client = StubClient() if args.stub or settings.is_stub else OpenAICompatClient(settings)
    log = LatencyLog(ROOT / "eval" / "results" / f"smoke-{provider}.jsonl")
    print(f"Provider: {provider}  model: {getattr(client, 'model', 'stub')}")

    ok = True
    if not args.skip_image:
        ok &= await test_image(client, log)

    if not args.skip_audio:
        if args.audio:
            wav, reference = encode_wav(load_wav(args.audio)), args.reference
        else:
            wav, reference = make_test_audio(), TTS_SENTENCE
            print(f"Generated SAPI test clip ({(len(wav) - 44) / 32000:.1f}s): {TTS_SENTENCE!r}")
        formats = [args.format] if args.format else list(AUDIO_FORMATS)
        working = await test_audio(client, log, wav, reference, formats)
        if working:
            print(f"\nAudio works with AUDIO_BLOCK_FORMAT={working}")
        else:
            print("\nNo audio block format worked. Transcription needs another service on this provider.")
            ok = False

    print(f"\nResults appended to {log.path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
