import pytest

from app.config import load_settings, save_values

ENV_NAMES = ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "STT_BASE_URL",
             "STT_API_KEY", "STT_MODEL", "STT_API", "STT_LANGUAGE", "AUDIO_BLOCK_FORMAT", "LLM_EXTRA_BODY",
             "TRANSCRIPT_DIR", "SPEAKER_NAMES"]


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


def test_save_and_reload_settings_file(env, tmp_path):
    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.delenv(name)
    path = tmp_path / "settings.env"
    path.write_text("# my notes\nWINDOW_SECONDS=20\n", encoding="utf-8")
    save_values({"LLM_PROVIDER": "openrouter", "LLM_BASE_URL": "https://x/v1",
                 "LLM_API_KEY": "secret-key", "LLM_MODEL": "omni"}, path)
    save_values({"LLM_MODEL": "omni-2"}, path)
    s = load_settings(path)
    assert (s.provider, s.api_key, s.model, s.window_seconds) == ("openrouter", "secret-key", "omni-2", 20.0)
    assert "# my notes" in path.read_text(encoding="utf-8")
    assert s.latency_log.startswith(str(tmp_path))


def test_environment_wins_over_file(env, tmp_path):
    path = tmp_path / "settings.env"
    save_values({"LLM_MODEL": "from-file"}, path)
    assert load_settings(path).model == "omni"


def test_rejects_unknown_stt_api(env):
    env.setenv("STT_API", "websocket")
    with pytest.raises(ValueError):
        load_settings(env_file=None)


def test_transcript_settings(env, tmp_path):
    s = load_settings(env_file=None)
    assert s.transcript_dir.endswith("Meeting Assistant") and s.speaker_names
    env.setenv("TRANSCRIPT_DIR", str(tmp_path))
    env.setenv("SPEAKER_NAMES", "off")
    s = load_settings(env_file=None)
    assert (s.transcript_dir, s.speaker_names) == (str(tmp_path), False)
