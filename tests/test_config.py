import pytest

from app.config import load_settings

ENV_NAMES = ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "STT_BASE_URL",
             "STT_API_KEY", "STT_MODEL", "STT_API", "STT_LANGUAGE", "AUDIO_BLOCK_FORMAT", "LLM_EXTRA_BODY"]


@pytest.fixture
def env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter   # openrouter | nebius")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_MODEL", "omni")
    return monkeypatch


def test_stt_falls_back_to_llm_settings(env):
    s = load_settings(env_file=None)
    assert s.provider == "openrouter"
    assert (s.stt_base_url, s.stt_api_key, s.stt_model, s.stt_api) == (
        "https://llm.example/v1", "llm-key", "omni", "chat")


def test_stt_can_use_its_own_provider(env):
    env.setenv("STT_BASE_URL", "https://stt.example/v1")
    env.setenv("STT_API_KEY", "stt-key")
    env.setenv("STT_MODEL", "whisper")
    env.setenv("STT_API", "transcriptions")
    s = load_settings(env_file=None)
    assert (s.stt_base_url, s.stt_api_key, s.stt_model, s.stt_api) == (
        "https://stt.example/v1", "stt-key", "whisper", "transcriptions")
    assert s.model == "omni"


def test_rejects_unknown_stt_api(env):
    env.setenv("STT_API", "websocket")
    with pytest.raises(ValueError):
        load_settings(env_file=None)
