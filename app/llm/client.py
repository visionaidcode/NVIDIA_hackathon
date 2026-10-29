"""Provider-agnostic, OpenAI-compatible model client, plus a stub for offline work.

OpenRouter and Nebius Token Factory both speak the OpenAI chat API, so the
provider is only base_url + api_key + model in config.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator
from typing import Protocol

from app.config import Settings

STT_INSTRUCTION = (
    "Transcribe the speech in this audio exactly as spoken. "
    "Output only the spoken words, with no comments or labels. "
    "If there is no speech, output nothing."
)

SPEAKER_INSTRUCTION = (
    "This is a screenshot of a video meeting taken while someone was talking. "
    "Who is speaking right now? Look for the highlighted or outlined participant tile, "
    "a speaking or microphone indicator, or the name shown with live captions. "
    "Reply with only that person's name as shown on screen. "
    "If you cannot tell who is speaking, reply Unknown."
)


PAGE_INSTRUCTION = (
    "This is a screenshot of a shared screen during a meeting. In one or two short sentences, "
    "say what is shown: the app, page or document, its title, and its key content (main text, "
    "numbers, names, dates). Plain text only, no lists or markdown. Never invent anything that "
    "is not legible."
)


SUMMARY_INSTRUCTION = (
    "Below is part of a meeting transcript with speaker names, and notes on what the shared "
    "screen showed. Summarize it in at most three short sentences of plain text. Always keep "
    "names, numbers, dates, decisions and tasks (who will do what). Never invent anything. "
    "Write in the main language of the transcript. No lists or markdown."
)

# Embedding size asked for: Qwen3-Embedding gives 4096 by default; 1024 keeps meeting.json small.
EMBED_DIMENSIONS = 1024


def apply_fixes(text: str, fixes: tuple[tuple[str, str], ...]) -> str:
    """Rewrite known mishearings, e.g. "Vision 18" -> "VisionAid team" (whole words, any case)."""
    for heard, meant in fixes:
        text = re.sub(rf"(?<!\w){re.escape(heard)}(?!\w)", meant, text, flags=re.I)
    return text


def keyterm_suspect(text: str, keyterms: tuple[str, ...], max_words: int = 4) -> bool:
    """A short transcript that names a key word: possibly the key word bias, not real speech."""
    words = re.findall(r"\w+", text)
    if not words or len(words) > max_words:
        return False
    low = text.lower()
    return any(t.lower() in low for t in keyterms)


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

    async def identify_speaker(self, jpeg: bytes) -> str: ...

    async def describe_page(self, jpeg: bytes) -> str: ...

    async def summarize(self, text: str) -> str: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


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
        self.stt_keyterms = settings.stt_keyterms
        self.stt_fixes = settings.stt_fixes
        self.audio_format = settings.audio_format
        self.extra_body = settings.extra_body
        self.embed_model = settings.embed_model  # empty: no embeddings, keyword search only
        # Sarvam AI is not OpenAI-compatible: its own path and key header.
        self._sarvam = None
        if self.stt_api == "sarvam":
            import httpx

            self._sarvam = httpx.AsyncClient(base_url=stt_base.rstrip("/"), timeout=timeout,
                                             headers={"api-subscription-key": stt_key})

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
        return apply_fixes(await self._transcribe(wav, fmt), self.stt_fixes)

    async def _transcribe(self, wav: bytes, fmt: str | None = None) -> str:
        if self.stt_api == "transcriptions":
            kwargs = {"language": self.stt_language} if self.stt_language else {}
            resp = await self._stt_client.audio.transcriptions.create(
                model=self.stt_model, file=("segment.wav", wav, "audio/wav"), temperature=0, **kwargs)
            return resp.text.strip()

        if self.stt_api == "sarvam":
            use_terms = bool(self.stt_keyterms) and self.stt_model.startswith("saaras:v4")  # v4 only
            text = await self._sarvam_transcribe(wav, use_terms)
            if use_terms and keyterm_suspect(text, self.stt_keyterms):
                # Key words pull short unclear words onto themselves ("print i" -> "VisionAid."):
                # a short result naming one is checked again without them.
                text = await self._sarvam_transcribe(wav, False)
            return text

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

    async def _sarvam_transcribe(self, wav: bytes, keyterms: bool) -> str:
        # No language code lets Sarvam detect it; codes look like ta-IN or en-IN.
        data = {"model": self.stt_model}
        if self.stt_language:
            data["language_code"] = self.stt_language
        if keyterms:
            data["keyterms"] = json.dumps(list(self.stt_keyterms))
        resp = await self._sarvam.post("/speech-to-text", data=data,
                                       files={"file": ("segment.wav", wav, "audio/wav")})
        if resp.status_code != 200:
            raise RuntimeError(f"Sarvam error {resp.status_code}: {resp.text[:200]}")
        return (resp.json().get("transcript") or "").strip()

    async def identify_speaker(self, jpeg: bytes) -> str:
        """Raw reply naming the active speaker in a meeting screenshot (see app.stt.speaker)."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": SPEAKER_INSTRUCTION}, image_block(jpeg)]}]
        # Room for a reasoning model's <think> block before the name.
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, max_tokens=400,
            extra_body=self.extra_body or None,
        )
        return (resp.choices[0].message.content or "").strip() if resp.choices else ""

    async def describe_page(self, jpeg: bytes) -> str:
        """One- or two-sentence note on a screen of the recording (see app.memory.history)."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": PAGE_INSTRUCTION}, image_block(jpeg)]}]
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, max_tokens=400,
            extra_body=self.extra_body or None,
        )
        return (resp.choices[0].message.content or "").strip() if resp.choices else ""

    async def summarize(self, text: str) -> str:
        """A few sentences on one stretch of the meeting (see app.rag.index)."""
        messages = [{"role": "system", "content": SUMMARY_INSTRUCTION}, {"role": "user", "content": text}]
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, max_tokens=400,
            extra_body=self.extra_body or None,
        )
        return (resp.choices[0].message.content or "").strip() if resp.choices else ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, for finding the minutes of a meeting that answer a question."""
        if not self.embed_model:
            raise RuntimeError("no embedding model set (EMBED_MODEL)")
        resp = await self._client.embeddings.create(model=self.embed_model, input=texts,
                                                    dimensions=EMBED_DIMENSIONS)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


class StubClient:
    """Fake responses so the app can be built and tested without cloud access."""

    def __init__(self, answer: str = "The slide shows the home page with a blue Sign up button. "
                                      "He said the button will move to the top right.",
                 transcript: str = "so on the home page we are moving the sign up button",
                 speaker: str = "Alex", delay: float = 0.05):
        self.answer = answer
        self.transcript = transcript
        self.speaker = speaker
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

    async def identify_speaker(self, jpeg: bytes) -> str:
        await asyncio.sleep(self.delay)
        return self.speaker

    async def describe_page(self, jpeg: bytes) -> str:
        await asyncio.sleep(self.delay)
        return "Slide: Launch plan, beta on 15 November, 3 testers."

    embed_model = "stub"

    async def summarize(self, text: str) -> str:
        await asyncio.sleep(self.delay)
        return "Summary: " + " ".join(text.split()[-12:])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Bag of words hashed into 64 numbers: texts sharing words come out close."""
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in re.findall(r"\w+", t.lower()):
                v[sum(map(ord, w)) % 64] += 1.0
            out.append(v)
        return out


def make_client(settings: Settings) -> ModelClient:
    if settings.is_stub:
        return StubClient()
    return OpenAICompatClient(settings)
