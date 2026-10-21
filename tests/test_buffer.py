from app.memory.buffer import Frame, RollingBuffer, TranscriptLine


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_prunes_old_frames_and_lines():
    clock = Clock()
    buf = RollingBuffer(30, clock)
    buf.add_frame(Frame(clock.t, b"a", 1, 1))
    buf.add_line(TranscriptLine(clock.t - 2, clock.t, "hello"))
    clock.t += 31
    buf.add_frame(Frame(clock.t, b"b", 1, 1))
    snap = buf.snapshot()
    assert [f.jpeg for f in snap.frames] == [b"b"]
    assert snap.transcript == []


def test_unchanged_frame_refreshes_timestamp_instead_of_duplicating():
    clock = Clock()
    buf = RollingBuffer(30, clock)
    buf.add_frame(Frame(clock.t, b"slide", 1, 1))
    clock.t += 25
    buf.add_frame(Frame(clock.t, b"", 0, 0), unchanged=True)
    clock.t += 10  # first sighting is now 35 s old, but the slide is still on screen
    snap = buf.snapshot()
    assert len(snap.frames) == 1
    assert snap.frames[0].jpeg == b"slide"
    assert snap.frames[0].first_ts == 1000.0


def test_empty_lines_are_ignored():
    buf = RollingBuffer(30, Clock())
    buf.add_line(TranscriptLine(0, 1000, "  "))
    assert buf.snapshot().transcript == []
