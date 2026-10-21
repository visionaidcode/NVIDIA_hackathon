"""Provider-agnostic, OpenAI-compatible model client, plus a stub for offline work.

OpenRouter and Nebius Token Factory both speak the OpenAI chat API, so the
provider is only base_url + api_key + model in config.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Protocol

from app.config import Settings

STT_INSTRUCTION = (
    "Transcribe the speech in this audio exactly as spoken. "
    "Output only the spoken words, with no comments or labels. "
    "If there is no speech, output nothing."
)


def image_block(jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def audio_block(wav: bytes, fmt: str = "input_audio") -> dict:
    """Build one audio content block.

    "input_audio" is the OpenAI / OpenRouter shape; "audio_url" is the vLLM shape
    shown on the Nemotron model card. Which one a provider accepts is checked by
    scripts/smoke_test.py.
    """
    b64 = base64.b64encode(wav).decode("ascii")
    if fmt == "input_audio":
        return {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}
    if fmt == "audio_url":
        return {"type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{b64}"}}
    raise ValueError(f"unknown audio block format {fmt!r}")


class ModelClient(Protocol):
    def stream_chat(self, messages: list[dict], *, temperature: float = 0.2,
                    max_tokens: int = 200) -> AsyncIterator[str]: ...

    async def transcribe(self, wav: bytes) -> str: ...


class OpenAICompatClient:
    def __init__(self, settings: Settings, timeout: float = 30.0):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key,
                                   timeout=timeout, max_retries=1)
        stt_base = settings.stt_base_url or settings.base_url
        stt_key = settings.stt_api_key or settings.api_key
        same = (stt_base, stt_key) == (settings.base_url, settings.api_key)
        self._stt_client = self._client if same else AsyncOpenAI(
            base_url=stt_base, api_key=stt_key, timeout=timeout, max_retries=1)
        self.model = settings.model
        self.stt_model = settings.stt_model or settings.model
        self.stt_api = settings.stt_api
        self.stt_language = settings.stt_language
        self.audio_format = settings.audio_format
        self.extra_body = settings.extra_body

    async def stream_chat(self, messages: list[dict], *, temperature: float = 0.2,
                          max_tokens: int = 200) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, stream=True, extra_body=self.extra_body or None,
        )
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                # Reasoning text (delta.reasoning on OpenRouter) is ignored on purpose.
                text = chunk.choices[0].delta.content
                if text:
                    yield text
        finally:
            await stream.close()

    async def transcribe(self, wav: bytes, fmt: str | None = None) -> str:
        if self.stt_api == "transcriptions":
            kwargs = {"language": self.stt_language} if self.stt_language else {}
            resp = await self._stt_client.audio.transcriptions.create(
                model=self.stt_model, file=("segment.wav", wav, "audio/wav"), temperature=0, **kwargs)
            return resp.text.strip()

        # A plain call at temperature 0; LLM_EXTRA_BODY is for the answer model, not this one.
        instruction = STT_INSTRUCTION
        if self.stt_language:
            instruction += f" The speech is in language code '{self.stt_language}'."
        messages = [{"role": "user", "content": [
            {"type": "text", "text": instruction},
            audio_block(wav, fmt or self.audio_format),
        ]}]
        resp = await self._stt_client.chat.completions.create(
            model=self.stt_model, messages=messages, temperature=0, max_tokens=400,
        )
        if not resp.choices:
            # OpenRouter reports some upstream failures (e.g. overload) as a 200 with an error body.
            error = (getattr(resp, "model_extra", None) or {}).get("error") or {}
            raise RuntimeError(f"provider error: {error.get('message', 'no choices in response')}")
        return (resp.choices[0].message.content or "").strip()


class StubClient:
    """Fake responses so the app can be built and tested without cloud access."""

    def __init__(self, answer: str = "The slide shows the home page with a blue Sign up button. "
                                      "He said the button will move to the top right.",
                 transcript: str = "so on the home page we are moving the sign up button",
                 delay: float = 0.05):
        self.answer = answer
        self.transcript = transcript
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages: list[dict], *, temperature: float = 0.2,
                          max_tokens: int = 200) -> AsyncIterator[str]:
        self.calls.append(messages)
        for word in self.answer.split(" "):
            await asyncio.sleep(self.delay)
            yield word + " "

    async def transcribe(self, wav: bytes) -> str:
        await asyncio.sleep(self.delay)
        return self.transcript


def make_client(settings: Settings) -> ModelClient:
    if settings.is_stub:
        return StubClient()
    return OpenAICompatClient(settings)
