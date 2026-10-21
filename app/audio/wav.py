"""WAV encode/decode helpers. All audio inside the app is 16 kHz mono float32 in [-1, 1]."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


def to_mono(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1) if samples.ndim == 2 else samples


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    if src_rate == dst_rate:
        return samples.astype(np.float32, copy=False)
    import soxr

    return soxr.resample(samples.astype(np.float32, copy=False), src_rate, dst_rate).astype(np.float32)


def encode_wav(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def load_wav(path: str | Path) -> np.ndarray:
    """Read a 16-bit PCM WAV file and return 16 kHz mono float32."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM WAV is supported")
        rate, channels = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    samples = data.astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return resample(to_mono(samples), rate)
