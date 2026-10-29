from app.config import Settings
from app.speech.out import PiperSpeaker, make_speaker, split_by_language


def test_english_only_is_one_run():
    assert split_by_language("Last 30 sec: Priya said hello.") == [("en", "Last 30 sec: Priya said hello.")]


def test_mixed_english_and_tamil_split_by_script():
    assert split_by_language("Ravi said வணக்கம் நண்பா. Then he left.") == [
        ("en", "Ravi said"), ("ta", "வணக்கம் நண்பா."), ("en", "Then he left.")]


def test_hindi_and_digits_stay_together():
    assert split_by_language("30 सेकंड पहले") == [("en", "30"), ("hi", "सेकंड पहले")]


def test_human_voice_without_voice_files_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("app.speech.out.BUNDLED_VOICE_DIR", tmp_path / "none")
    s = Settings(answer_voice="human", voice_dir=str(tmp_path))
    assert not isinstance(make_speaker(echo=False, settings=s), PiperSpeaker)


def _voice(folder, name):
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(b"")
    (folder / f"{name}.json").write_text("{}", encoding="utf-8")


def test_voices_inside_the_exe_fill_in_missing_languages(tmp_path):
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    _voice(user, "en_US-mine.onnx")
    _voice(bundled, "en_US-lessac-medium.onnx")
    _voice(bundled, "ta_IN-rasa_male-medium.onnx")
    sp = PiperSpeaker(user, bundled)
    assert sp._files == {"en": user / "en_US-mine.onnx", "ta": bundled / "ta_IN-rasa_male-medium.onnx"}


def test_voices_inside_the_exe_used_when_user_folder_is_missing(tmp_path):
    _voice(tmp_path / "bundled", "hi_IN-priyamvada-medium.onnx")
    assert set(PiperSpeaker(tmp_path / "missing", tmp_path / "bundled")._files) == {"hi"}
