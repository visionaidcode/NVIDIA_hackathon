"""Capture what the speakers play (WASAPI loopback), as 16 kHz mono float32.

The audio callback only queues raw blocks; resampling and VAD run on a worker
thread so the callback never blocks.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import numpy as np

from app.audio.vad import Segment, SpeechSegmenter
from app.audio.wav import SAMPLE_RATE


def find_loopback_device(pa) -> dict:
    import pyaudiowpatch as pyaudio

    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if speakers.get("isLoopbackDevice"):
        return speakers
    for dev in pa.get_loopback_device_info_generator():
        if speakers["name"] in dev["name"]:
            return dev
    raise RuntimeError(f"No loopback device found for default output {speakers['name']!r}")


class LoopbackCapture:
    def __init__(self, segmenter: SpeechSegmenter, on_segment: Callable[[Segment], None]):
        self.segmenter = segmenter
        self.on_segment = on_segment
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=500)
        self._pa = None
        self._stream = None
        self._worker: threading.Thread | None = None
        self.device_name = ""

    def start(self) -> None:
        import pyaudiowpatch as pyaudio
        import soxr

        self._pa = pyaudio.PyAudio()
        dev = find_loopback_device(self._pa)
        self.device_name = dev["name"]
        channels = int(dev["maxInputChannels"])
        rate = int(dev["defaultSampleRate"])
        resampler = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")

        def callback(data, frame_count, time_info, status):
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                pass  # worker is behind; dropping audio beats blocking the device
            return (None, pyaudio.paContinue)

        def work():
            while (data := self._queue.get()) is not None:
                block = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
                mono = block.reshape(-1, channels).mean(axis=1)
                for seg in self.segmenter.feed(resampler.resample_chunk(mono)):
                    self.on_segment(seg)
            for seg in self.segmenter.flush():
                self.on_segment(seg)

        self._worker = threading.Thread(target=work, name="audio-vad", daemon=True)
        self._worker.start()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                                     input=True, input_device_index=dev["index"],
                                     frames_per_buffer=rate // 10, stream_callback=callback)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa:
            self._pa.terminate()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=5)
