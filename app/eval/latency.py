"""Append-only JSONL log of timings. The app logs only numbers, never audio, images or meeting text."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class LatencyLog:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields) -> None:
        if not self.path:
            return
        record = {"ts": round(time.time(), 3), "event": event, **fields}
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
