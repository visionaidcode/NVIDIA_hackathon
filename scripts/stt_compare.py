"""Compare speech-to-text models on the same clips: word error rate and latency.

    python scripts/stt_compare.py
    python scripts/stt_compare.py --models qwen/qwen3.8-omni-flash mistralai/voxtral-small-24b-2507
    python scripts/stt_compare.py --clips fixtures/clips      # name.wav + name.txt pairs

Uses STT_BASE_URL / STT_API_KEY / STT_API from .env (falling back to LLM_*).
Without --clips it speaks a few test sentences with every installed Windows voice,
so no real meeting audio is sent. Results go to eval/results/stt-compare.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio.wav import encode_wav, load_wav  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.eval.latency import LatencyLog  # noqa: E402
from app.llm.client import OpenAICompatClient  # noqa: E402
from app.stt.transcribe import clean_transcript  # noqa: E402

DEFAULT_MODELS = [
    "mistralai/voxtral-small-24b-2507",
    "qwen/qwen3.8-omni-flash",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-audio-mini",
]

SENTENCES = [
    "On the home page we will move the sign up button to the top right corner.",
    "Sales in March were forty two thousand dollars, up from thirty five in February.",
    "Can everyone see my screen? This is the new dashboard for the Bangalore team.",
]


SAFT_16KHZ_16BIT_MONO = 18
SSFM_CREATE_FOR_WRITE = 3


def sapi_clips() -> list[tuple[str, bytes, str]]:
    """Speak SENTENCES with every installed SAPI voice straight into 16 kHz WAV files."""
    import comtypes.client

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    tokens = voice.GetVoices()
    clips = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in range(tokens.Count):
            token = tokens.Item(t)
            voice.Voice = token
            name = token.GetDescription()
            for i, text in enumerate(SENTENCES):
                path = Path(tmp) / f"{len(clips)}.wav"
                stream = comtypes.client.CreateObject("SAPI.SpFileStream")
                fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
                fmt.Type = SAFT_16KHZ_16BIT_MONO
                stream.Format = fmt
                stream.Open(str(path), SSFM_CREATE_FOR_WRITE)
                voice.AudioOutputStream = stream
                voice.Speak(text)
                stream.Close()
                clips.append((f"{name.split(' - ')[0]} #{i + 1}", encode_wav(load_wav(path)), text))
        voice.AudioOutputStream = None
    return clips


def folder_clips(folder: Path) -> list[tuple[str, bytes, str]]:
    clips = []
    for wav in sorted(folder.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if ref.exists():
            clips.append((wav.name, encode_wav(load_wav(wav)), ref.read_text(encoding="utf-8").strip()))
    return clips


def wer(ref: str, hyp: str) -> float:
    import jiwer

    tr = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                        jiwer.RemoveMultipleSpaces(), jiwer.Strip(), jiwer.ReduceToListOfListOfWords()])
    return jiwer.wer(ref, hyp, reference_transform=tr, hypothesis_transform=tr)


async def run_model(settings, model: str, clips, log: LatencyLog) -> dict:
    client = OpenAICompatClient(replace(settings, stt_model=model))
    latencies, errors, refs, hyps = [], 0, [], []
    for name, wav, ref in clips:
        t0 = time.perf_counter()
        try:
            hyp = clean_transcript(await client.transcribe(wav))
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  {name}: ERROR {type(e).__name__}: {str(e)[:150]}")
            continue
        dt = time.perf_counter() - t0
        latencies.append(dt)
        refs.append(ref)
        hyps.append(hyp)
        print(f"  {name}: {dt:.2f}s WER {wer(ref, hyp):.0%} -> {hyp!r}")
    result = {"model": model, "clips": len(clips), "errors": errors,
              "wer": round(wer(refs, hyps), 4) if refs else None,
              "median_s": round(statistics.median(latencies), 3) if latencies else None,
              "worst_s": round(max(latencies), 3) if latencies else None}
    log.write("stt_compare", **result)
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--clips", type=Path, help="folder of name.wav + name.txt pairs")
    args = ap.parse_args()

    settings = load_settings(ROOT / ".env")
    clips = folder_clips(args.clips) if args.clips else sapi_clips()
    print(f"{len(clips)} clips, STT endpoint {settings.stt_base_url} ({settings.stt_api})")
    log = LatencyLog(ROOT / "eval" / "results" / "stt-compare.jsonl")

    results = []
    for model in args.models:
        print(f"\n== {model} ==")
        results.append(await run_model(settings, model, clips, log))

    print(f"\n{'model':<40} {'WER':>6} {'median':>8} {'worst':>7} {'errors':>7}")
    for r in sorted(results, key=lambda r: (r["wer"] is None, r["wer"] or 0, r["median_s"] or 0)):
        w = f"{r['wer']:.1%}" if r["wer"] is not None else "-"
        m = f"{r['median_s']:.2f}s" if r["median_s"] else "-"
        x = f"{r['worst_s']:.2f}s" if r["worst_s"] else "-"
        print(f"{r['model']:<40} {w:>6} {m:>8} {x:>7} {r['errors']:>4}/{r['clips']}")


if __name__ == "__main__":
    asyncio.run(main())
