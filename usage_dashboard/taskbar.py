"""Windows taskbar extras: a progress strip on the taskbar button, and the app's own identity.

Everything here is optional and safe: off Windows, or if Windows refuses, each call just
does nothing and returns False.
"""
import ctypes
import sys
import uuid
from ctypes import wintypes

APP_ID = "ClaudeUsageDashboard"

# ITaskbarList3 progress states
NOPROGRESS, NORMAL, ERROR, PAUSED = 0, 2, 4, 8
STATE_FOR_LEVEL = {"ok": NORMAL, "warn": PAUSED, "hot": ERROR}   # green, yellow, red

_CLSID_TASKBAR_LIST = "56FDF344-FD6D-11D0-958A-006097C9A090"
_IID_TASKBAR_LIST3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"
_HRINIT, _SET_PROGRESS_VALUE, _SET_PROGRESS_STATE = 3, 9, 10   # vtable slots


def _is_windows():
    return sys.platform.startswith("win")


def set_app_id():
    """Give the process its own taskbar identity, so the taskbar groups it as this app
    (with this app's icon) and not under Python. Call before the window is created."""
    if not _is_windows():
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        return True
    except Exception:
        return False


class _Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class TaskbarProgress:
    """Sets the progress strip on this app's taskbar button."""

    def __init__(self):
        self._ptr = None
        self._set_value = None
        self._set_state = None
        self._failed = not _is_windows()

    def _connect(self):
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        ptr = ctypes.c_void_p()
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_Guid), ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = ctypes.HRESULT
        ole32.CoCreateInstance(ctypes.byref(_Guid.of(_CLSID_TASKBAR_LIST)), None, 1,
                               ctypes.byref(_Guid.of(_IID_TASKBAR_LIST3)), ctypes.byref(ptr))
        vtable = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                             ctypes.POINTER(ctypes.c_void_p))
        proto = ctypes.WINFUNCTYPE
        proto(ctypes.HRESULT, ctypes.c_void_p)(vtable[_HRINIT])(ptr)
        self._set_value = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND,
                                ctypes.c_ulonglong, ctypes.c_ulonglong)(vtable[_SET_PROGRESS_VALUE])
        self._set_state = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND,
                                ctypes.c_int)(vtable[_SET_PROGRESS_STATE])
        self._ptr = ptr

    def update(self, hwnd, pct, level):
        """Show pct (0-100) on the taskbar button in the colour for level. True if it worked."""
        if self._failed or not hwnd:
            return False
        try:
            if self._ptr is None:
                self._connect()
            self._set_state(self._ptr, hwnd, STATE_FOR_LEVEL.get(level, NORMAL))
            self._set_value(self._ptr, hwnd, max(0, min(100, int(round(pct)))), 100)
            return True
        except Exception:
            self._failed = True   # don't retry every refresh
            return False

    def clear(self, hwnd):
        if self._ptr is None or self._failed or not hwnd:
            return False
        try:
            self._set_state(self._ptr, hwnd, NOPROGRESS)
            return True
        except Exception:
            return False
