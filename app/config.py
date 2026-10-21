"""Settings from environment variables (and an optional .env file)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS = ("openrouter", "nebius", "stub")
AUDIO_FORMATS = ("input_audio", "audio_url")
# chat: audio sent to a chat model (Omni, Qwen, Voxtral, Gemini...)
# transcriptions: OpenAI-style /audio/transcriptions endpoint (Whisper-type models)
STT_APIS = ("chat", "transcriptions")


@dataclass(frozen=True)
class Settings:
    provider: str = "stub"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # Speech-to-text can use a different provider and model; empty means "same as LLM_*".
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = ""
    stt_api: str = "chat"
    stt_language: str = ""
    audio_format: str = "input_audio"
    extra_body: dict = field(default_factory=dict)
    gemini_api_key: str = ""
    window_seconds: float = 30.0
    frame_interval: float = 2.0
    frame_max_side: int = 1280
    jpeg_quality: int = 70
    max_frames_per_question: int = 3
    latency_log: str = "eval/results/latency.jsonl"

    @property
    def is_stub(self) -> bool:
        return self.provider == "stub"


def _env(name: str, default: str = "") -> str:
    # Strip inline comments like "openrouter   # openrouter | nebius".
    value = os.environ.get(name, default).split(" #", 1)[0].strip()
    return value or default


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)

    provider = _env("LLM_PROVIDER", "stub").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {PROVIDERS}, got {provider!r}")

    audio_format = _env("AUDIO_BLOCK_FORMAT", "input_audio")
    if audio_format not in AUDIO_FORMATS:
        raise ValueError(f"AUDIO_BLOCK_FORMAT must be one of {AUDIO_FORMATS}, got {audio_format!r}")

    stt_api = _env("STT_API", "chat")
    if stt_api not in STT_APIS:
        raise ValueError(f"STT_API must be one of {STT_APIS}, got {stt_api!r}")

    extra_raw = _env("LLM_EXTRA_BODY")
    extra_body = json.loads(extra_raw) if extra_raw else {}

    base_url, api_key, model = _env("LLM_BASE_URL"), _env("LLM_API_KEY"), _env("LLM_MODEL")
    settings = Settings(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        stt_base_url=_env("STT_BASE_URL") or base_url,
        stt_api_key=_env("STT_API_KEY") or api_key,
        stt_model=_env("STT_MODEL") or model,
        stt_api=stt_api,
        stt_language=_env("STT_LANGUAGE"),
        audio_format=audio_format,
        extra_body=extra_body,
        gemini_api_key=_env("GEMINI_API_KEY"),
        window_seconds=float(_env("WINDOW_SECONDS", "30")),
        frame_interval=float(_env("FRAME_INTERVAL_SECONDS", "2")),
        frame_max_side=int(_env("FRAME_MAX_SIDE", "1280")),
        jpeg_quality=int(_env("JPEG_QUALITY", "70")),
        max_frames_per_question=int(_env("MAX_FRAMES_PER_QUESTION", "3")),
        latency_log=_env("LATENCY_LOG", "eval/results/latency.jsonl"),
    )
    if not settings.is_stub:
        missing = [n for n, v in (("LLM_BASE_URL", settings.base_url),
                                  ("LLM_API_KEY", settings.api_key),
                                  ("LLM_MODEL", settings.model)) if not v]
        if missing:
            raise ValueError(f"Missing settings for provider {provider}: {', '.join(missing)}")
    return settings
