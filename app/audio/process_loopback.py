"""Capture what the speakers play EXCEPT one program, as 16 kHz mono float32.

Windows 10 2004+ can record system audio while leaving out one process tree
("process loopback"). We leave out the screen reader (NVDA, JAWS, Narrator), so
its voice never reaches the meeting transcript. With no screen reader running we
leave out this app itself, so its own spoken answers are skipped too.

Windows converts the audio to 16 kHz mono 16-bit for us (AUTOCONVERTPCM).
"""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from ctypes import POINTER, byref, c_int, c_uint32, c_uint64, c_ubyte, c_void_p, wintypes

import numpy as np

from app.audio.vad import Segment, SpeechSegmenter
from app.audio.wav import SAMPLE_RATE

try:  # importing comtypes joins COM on the importing thread; do it here, not on the capture thread
    import comtypes  # noqa: F401
except ImportError:
    pass

# Screen readers whose voice must never be transcribed: process name -> friendly name.
SCREEN_READERS = {"nvda.exe": "NVDA", "jfw.exe": "JAWS", "narrator.exe": "Narrator"}


def running_processes() -> list[tuple[int, str]]:
    """(process id, exe name) of every running process, straight from Windows (milliseconds;
    `tasklist` took up to a minute on a busy PC)."""

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out, entry = [], PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = k32.Process32FirstW(snap, byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def find_screen_reader() -> tuple[int, str] | None:
    """(process id, name) of the first running screen reader, or None."""
    for pid, exe in running_processes():
        if exe.lower() in SCREEN_READERS:
            return pid, SCREEN_READERS[exe.lower()]
    return None


def _com_types():
    """COM interfaces and structures, defined on first use (Windows only)."""
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult",
                               (["out"], POINTER(HRESULT), "activateResult"),
                               (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"))]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                               (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "operation"))]

    class IAgileObject(IUnknown):  # marks the handler as callable from any thread
        _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
        _methods_ = []

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(POINTER(c_ubyte)), "data"),
                      (["out"], POINTER(c_uint32), "frames"), (["out"], POINTER(wintypes.DWORD), "flags"),
                      (["out"], POINTER(c_uint64), "devicePosition"),
                      (["out"], POINTER(c_uint64), "qpcPosition")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], c_int, "shareMode"),
                      (["in"], wintypes.DWORD, "streamFlags"), (["in"], ctypes.c_longlong, "bufferDuration"),
                      (["in"], ctypes.c_longlong, "periodicity"), (["in"], POINTER(WAVEFORMATEX), "format"),
                      (["in"], POINTER(GUID), "sessionGuid")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "frames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "latency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "frames")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], c_int, "shareMode"),
                      (["in"], POINTER(WAVEFORMATEX), "format"), (["in"], POINTER(c_void_p), "closest")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "format")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_longlong), "default"),
                      (["out"], POINTER(ctypes.c_longlong), "minimum")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "event")),
            COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "iid"),
                      (["out"], POINTER(POINTER(IUnknown)), "service")),
        ]

    class LOOPBACK_PARAMS(ctypes.Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS
        _fields_ = [("ActivationType", c_int), ("TargetProcessId", wintypes.DWORD),
                    ("ProcessLoopbackMode", c_int)]

    class BLOB(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD), ("r2", wintypes.WORD),
                    ("r3", wintypes.WORD), ("blob", BLOB)]

    class Handler(comtypes.COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, operation):
            self.done.set()
            return 0

    return (comtypes, GUID, IActivateAudioInterfaceAsyncOperation, IActivateAudioInterfaceCompletionHandler,
            WAVEFORMATEX, IAudioCaptureClient, IAudioClient, LOOPBACK_PARAMS, PROPVARIANT, BLOB, Handler)


PROCESS_LOOPBACK = 1
EXCLUDE_TARGET_PROCESS_TREE = 1
VT_BLOB = 65
STREAMFLAGS = 0x00020000 | 0x00040000 | 0x80000000  # LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM
BUFFERFLAGS_SILENT = 0x2


class ProcessLoopbackCapture:
    """Same interface as LoopbackCapture: start(), stop(), device_name."""

    def __init__(self, segmenter: SpeechSegmenter, on_segment: Callable[[Segment], None],
                 exclude_pid: int | None = None, exclude_name: str = ""):
        self.segmenter = segmenter
        self.on_segment = on_segment
        if exclude_pid is None:
            found = find_screen_reader()
            exclude_pid, exclude_name = found if found else (os.getpid(), "this app")
        self.exclude_pid = exclude_pid
        self.device_name = f"All system audio except {exclude_name or exclude_pid}"
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="process-loopback", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._error:
            raise RuntimeError(f"Process loopback unavailable: {self._error}") from self._error
        if not self._ready.is_set():
            raise RuntimeError("Process loopback did not start in time")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            client, capture, event, k32 = self._open()
        except Exception as e:  # noqa: BLE001 - reported to start(), which falls back
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        try:
            client.Start()
            while not self._stop.is_set():
                k32.WaitForSingleObject(event, 100)
                while capture.GetNextPacketSize():
                    data, frames, flags, _, _ = capture.GetBuffer()
                    if flags & BUFFERFLAGS_SILENT:
                        block = np.zeros(frames, dtype=np.float32)
                    else:
                        raw = ctypes.string_at(data, frames * 2)
                        block = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                    capture.ReleaseBuffer(frames)
                    for seg in self.segmenter.feed(block):
                        self.on_segment(seg)
            client.Stop()
        finally:
            for seg in self.segmenter.flush():
                self.on_segment(seg)
            k32.CloseHandle(event)

    def _open(self):
        (comtypes, GUID, IOperation, IHandler, WAVEFORMATEX, IAudioCaptureClient, IAudioClient,
         LOOPBACK_PARAMS, PROPVARIANT, BLOB, Handler) = _com_types()
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # this thread already joined COM in another mode; the handler is agile either way

        params = LOOPBACK_PARAMS(PROCESS_LOOPBACK, self.exclude_pid, EXCLUDE_TARGET_PROCESS_TREE)
        var = PROPVARIANT(vt=VT_BLOB, blob=BLOB(ctypes.sizeof(params), ctypes.cast(byref(params), c_void_p)))
        handler = Handler()
        op = POINTER(IOperation)()
        activate = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
        activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), POINTER(IHandler),
                             POINTER(POINTER(IOperation))]
        activate.restype = ctypes.HRESULT
        activate("VAD\\Process_Loopback", byref(IAudioClient._iid_), byref(var),
                 handler.QueryInterface(IHandler), byref(op))
        if not handler.done.wait(5):
            raise TimeoutError("audio activation timed out")
        hr, unknown = op.GetActivateResult()
        if hr < 0:
            raise OSError(hr, "audio activation failed")
        client = unknown.QueryInterface(IAudioClient)

        fmt = WAVEFORMATEX(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)  # 16 kHz mono 16-bit PCM
        client.Initialize(0, STREAMFLAGS, 2_000_000, 0, byref(fmt), None)  # shared, 200 ms buffer
        k32 = ctypes.WinDLL("kernel32")
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        event = k32.CreateEventW(None, False, False, None)
        client.SetEventHandle(event)
        capture = client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
        return client, capture, event, k32
