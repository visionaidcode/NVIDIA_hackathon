from app.config import Settings
from app.speech.out import PiperSpeaker, make_speaker, split_by_language


def test_english_only_is_one_run():
    assert split_by_language("Last 30 sec: Priya said hello.") == [("en", "Last 30 sec: Priya said hello.")]


def test_mixed_english_and_tamil_split_by_script():
    assert split_by_language("Ravi said வணக்கம் நண்பா. Then he left.") == [
        ("en", "Ravi said"), ("ta", "வணக்கம் நண்பா."), ("en", "Then he left.")]


def test_hindi_and_digits_stay_together():
    assert split_by_language("30 सेकंड पहले") == [("en", "30"), ("hi", "सेकंड पहले")]


def test_human_voice_without_voice_files_falls_back(tmp_path):
    s = Settings(answer_voice="human", voice_dir=str(tmp_path))
    assert not isinstance(make_speaker(echo=False, settings=s), PiperSpeaker)
