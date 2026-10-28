import numpy as np

from app.audio.vad import EnergyVAD, SpeechSegmenter
from app.audio.wav import SAMPLE_RATE, encode_wav
from app.stt.transcribe import clean_transcript


def tone(seconds, amp=0.3):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_segmenter_skips_silence_and_cuts_at_pauses():
    seg = SpeechSegmenter(EnergyVAD(), clock=lambda: 50.0)
    out = []
    for block in (silence(2), tone(1.0), silence(1.0), tone(0.8), silence(1.0)):
        for i in range(0, len(block), 1600):  # 100 ms blocks, like the audio callback
            out += seg.feed(block[i:i + 1600])
    assert len(out) == 2
    assert 1.0 <= out[0].duration <= 2.0
    assert out[0].end == 50.0


def test_segmenter_drops_clicks():
    seg = SpeechSegmenter(EnergyVAD())
    assert seg.feed(np.concatenate([tone(0.05), silence(1.0)])) == []


def test_segmenter_caps_segment_length():
    seg = SpeechSegmenter(EnergyVAD(), max_segment_s=2.0)
    out = seg.feed(tone(5.0)) + seg.flush()
    assert len(out) >= 2 and all(s.duration <= 2.1 for s in out)


def test_encode_wav_header():
    wav = encode_wav(silence(1))
    assert wav[:4] == b"RIFF" and len(wav) == 44 + SAMPLE_RATE * 2


def test_clean_transcript_drops_non_speech_markers():
    assert clean_transcript("[no speech]") == ""
    assert clean_transcript('"Hello there."') == "Hello there."


def test_find_screen_reader_picks_known_screen_reader(monkeypatch):
    from app.audio import process_loopback

    monkeypatch.setattr(process_loopback, "running_processes", lambda: [(1200, "explorer.exe"), (6532, "nvda.exe")])
    assert process_loopback.find_screen_reader() == (6532, "NVDA")
    monkeypatch.setattr(process_loopback, "running_processes", lambda: [(1200, "explorer.exe")])
    assert process_loopback.find_screen_reader() is None


def test_running_processes_lists_this_process():
    import os

    from app.audio.process_loopback import running_processes

    assert any(pid == os.getpid() for pid, _ in running_processes())
