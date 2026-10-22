"""Periodic screenshots, downscaled and JPEG-compressed, pushed into the rolling buffer."""

from __future__ import annotations

import io
import threading
import time

import numpy as np
from PIL import Image

from app.memory.buffer import Frame, RollingBuffer

# Mean absolute difference (0-255) of a small grayscale thumbnail below which two
# screenshots count as "the same screen" (cursor blink, clock tick).
SAME_SCREEN_THRESHOLD = 1.5


def encode_frame(img: Image.Image, max_side: int, quality: int) -> tuple[bytes, int, int]:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), img.width, img.height


def fingerprint(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L").resize((64, 36), Image.Resampling.BILINEAR), dtype=np.float32)


def same_screen(a: np.ndarray | None, b: np.ndarray) -> bool:
    return a is not None and float(np.abs(a - b).mean()) < SAME_SCREEN_THRESHOLD


class ScreenCapture:
    """Background thread that grabs one monitor every `interval` seconds.

    monitor=1 is the primary monitor in mss numbering (0 is all monitors combined).
    """

    def __init__(self, buffer: RollingBuffer, interval: float = 2.0, max_side: int = 1280,
                 quality: int = 70, monitor: int = 1):
        self.buffer = buffer
        self.interval = interval
        self.max_side = max_side
        self.quality = quality
        self.monitor = monitor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    def grab(self, sct) -> Image.Image:
        shot = sct.grab(sct.monitors[self.monitor])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def capture_now(self) -> None:
        """Take one screenshot immediately, e.g. when a question is asked. Any thread."""
        import mss

        with mss.mss() as sct:
            img = self.grab(sct)
        jpeg, w, h = encode_frame(img, self.max_side, self.quality)
        self.buffer.add_frame(Frame(time.time(), jpeg, w, h))

    def _run(self) -> None:
        import mss

        last_fp = None
        with mss.mss() as sct:  # mss handles are per-thread
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    img = self.grab(sct)
                    fp = fingerprint(img)
                    now = time.time()
                    if same_screen(last_fp, fp):
                        self.buffer.add_frame(Frame(now, b"", 0, 0), unchanged=True)
                    else:
                        jpeg, w, h = encode_frame(img, self.max_side, self.quality)
                        self.buffer.add_frame(Frame(now, jpeg, w, h))
                        last_fp = fp
                    self.last_error = None
                except Exception as e:  # noqa: BLE001 - keep capturing after transient errors
                    self.last_error = e
                self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="screen-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
