"""Word error rate of the transcription pipeline on recorded clips.

Put pairs in a folder: clip1.wav + clip1.txt (the exact words spoken), ...

    python scripts/wer.py fixtures/clips

Each clip is cut at pauses with the same VAD the app uses, transcribed segment by
segment, and compared with its reference. Prints per-clip and overall WER.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jiwer  # noqa: E402

from app.audio.vad import SpeechSegmenter, make_vad  # noqa: E402
from app.audio.wav import encode_wav, load_wav  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm.client import make_client  # noqa: E402
from app.stt.transcribe import clean_transcript  # noqa: E402

NORMALIZE = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                           jiwer.RemoveMultipleSpaces(), jiwer.Strip(),
                           jiwer.ReduceToListOfListOfWords()])


async def transcribe_clip(client, path: Path) -> str:
    seg = SpeechSegmenter(make_vad())
    parts = []
    for s in seg.feed(load_wav(path)) + seg.flush():
        parts.append(clean_transcript(await client.transcribe(encode_wav(s.audio))))
    return " ".join(p for p in parts if p)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    args = ap.parse_args()

    client = make_client(load_settings(ROOT / ".env"))
    refs, hyps = [], []
    for wav in sorted(args.folder.glob("*.wav")):
        ref_file = wav.with_suffix(".txt")
        if not ref_file.exists():
            print(f"skip {wav.name}: no {ref_file.name}")
            continue
        ref = ref_file.read_text(encoding="utf-8").strip()
        hyp = await transcribe_clip(client, wav)
        score = jiwer.wer(ref, hyp, reference_transform=NORMALIZE, hypothesis_transform=NORMALIZE)
        print(f"{wav.name}: WER {score:.1%}\n  ref: {ref}\n  hyp: {hyp}")
        refs.append(ref)
        hyps.append(hyp)
    if refs:
        total = jiwer.wer(refs, hyps, reference_transform=NORMALIZE, hypothesis_transform=NORMALIZE)
        print(f"\nOverall WER over {len(refs)} clips: {total:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
