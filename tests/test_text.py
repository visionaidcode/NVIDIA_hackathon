from app.qa.text import SentenceSplitter, ThinkFilter, clean_for_speech


def feed_all(f, chunks):
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_think_filter_handles_split_tags():
    chunks = ["<thi", "nk>reasoning here</th", "ink>The answer", " is 42."]
    assert feed_all(ThinkFilter(), chunks) == "The answer is 42."


def test_think_filter_passes_plain_text_and_lone_angle_brackets():
    assert feed_all(ThinkFilter(), ["a < b", " and c > d"]) == "a < b and c > d"


def test_unclosed_think_is_dropped():
    assert feed_all(ThinkFilter(), ["Hi <think>never closed"]) == "Hi "


def test_sentence_splitter_streams_sentences():
    s = SentenceSplitter()
    assert s.feed("It shows 3.5 percent") == []
    assert s.feed(". Next") == ["It shows 3.5 percent."]
    assert s.flush() == ["Next"]


def test_clean_for_speech_strips_markdown():
    assert clean_for_speech("- **Revenue** is `up`") == "Revenue is up"
