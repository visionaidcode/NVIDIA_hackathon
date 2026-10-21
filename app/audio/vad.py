"""Voice-activity detection and speech segmentation, so silent audio is never sent."""

from __future__ import annotations

import time
import warnings
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.audio.wav import SAMPLE_RATE

FRAME_SAMPLES = 512  # 32 ms at 16 kHz, the window size Silero expects


class VAD(Protocol):
    def prob(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class EnergyVAD:
    """Fallback when Silero is not installed: RMS loudness threshold."""

    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold

    def prob(self, frame: np.ndarray) -> float:
        return 1.0 if float(np.sqrt(np.mean(frame ** 2))) >= self.threshold else 0.0

    def reset(self) -> None:
        pass


SILERO_ONNX = Path(__file__).parent / "data" / "silero_vad.onnx"
SILERO_CONTEXT = 64  # samples of the previous window Silero v5 expects in front of each window at 16 kHz


class SileroOnnxVAD:
    """Silero VAD on onnxruntime: same model as the PyTorch one, without the 1 GB dependency."""

    def __init__(self, model_path: Path = SILERO_ONNX):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_path), sess_options=opts,
                                                    providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, frame.astype(np.float32, copy=False)])[None, :]
        out, self._state = self.session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[0, -SILERO_CONTEXT:]
        return float(out[0, 0])

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(SILERO_CONTEXT, dtype=np.float32)


class SileroVAD:
    def __init__(self):
        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        self._torch = torch
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # torch.jit.load deprecation notice
            self.model = load_silero_vad(onnx=False)

    def prob(self, frame: np.ndarray) -> float:
        return float(self.model(self._torch.from_numpy(frame), SAMPLE_RATE).item())

    def reset(self) -> None:
        self.model.reset_states()


def make_vad() -> VAD:
    for factory in (SileroOnnxVAD, SileroVAD):
        try:
            return factory()
        except (ImportError, OSError):
            continue
    return EnergyVAD()


@dataclass(frozen=True)
class Segment:
    start: float  # wall-clock seconds
    end: float
    audio: np.ndarray  # 16 kHz mono float32

    @property
    def duration(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class SpeechSegmenter:
    """Cuts a 16 kHz stream into speech segments at pauses (or at max length)."""

    def __init__(self, vad: VAD, threshold: float = 0.5, min_silence_s: float = 0.6,
                 max_segment_s: float = 8.0, min_speech_s: float = 0.3, preroll_s: float = 0.2,
                 clock: Callable[[], float] = time.time):
        def frames(seconds: float) -> int:
            return max(1, round(seconds * SAMPLE_RATE / FRAME_SAMPLES))

        self.vad = vad
        self.threshold = threshold
        self.min_silence = frames(min_silence_s)
        self.max_frames = frames(max_segment_s)
        self.min_speech = frames(min_speech_s)
        self.clock = clock
        self._pending = np.empty(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=frames(preroll_s))
        self._active: list[np.ndarray] = []
        self._speech = 0
        self._silence = 0

    def feed(self, samples: np.ndarray) -> list[Segment]:
        self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        out: list[Segment] = []
        n = len(self._pending) // FRAME_SAMPLES
        for i in range(n):
            frame = self._pending[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            is_speech = self.vad.prob(frame) >= self.threshold
            if not self._active:
                if is_speech:
                    self._active = [*self._preroll, frame]
                    self._speech, self._silence = 1, 0
                else:
                    self._preroll.append(frame)
                continue
            self._active.append(frame)
            if is_speech:
                self._speech += 1
                self._silence = 0
            else:
                self._silence += 1
            if self._silence >= self.min_silence or len(self._active) >= self.max_frames:
                if seg := self._close():
                    out.append(seg)
        self._pending = self._pending[n * FRAME_SAMPLES:].copy()
        return out

    def flush(self) -> list[Segment]:
        seg = self._close() if self._active else None
        return [seg] if seg else []

    def _close(self) -> Segment | None:
        frames, speech = self._active, self._speech
        self._active, self._speech, self._silence = [], 0, 0
        self._preroll.clear()
        if speech < self.min_speech:
            return None
        audio = np.concatenate(frames)
        end = self.clock()
        return Segment(end - len(audio) / SAMPLE_RATE, end, audio)
