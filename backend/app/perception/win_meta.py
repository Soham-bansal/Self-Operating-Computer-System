from __future__ import annotations
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import psutil

user32 = ctypes.windll.user32

@dataclass
class ActiveWindowInfo:
    hwnd: int
    title: str
    pid: int
    process_name: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    minimized: bool

def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)

def get_active_window_info() -> ActiveWindowInfo:
    hwnd = user32.GetForegroundWindow()
    minimized = bool(user32.IsIconic(hwnd))

    # title
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value or ""

    # rect
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    l, t, r, b = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    # pid
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pid_i = int(pid.value)

    # process name
    proc_name = ""
    try:
        proc_name = psutil.Process(pid_i).name()
    except Exception:
        proc_name = ""

    return ActiveWindowInfo(
        hwnd=int(hwnd),
        title=title,
        pid=pid_i,
        process_name=proc_name,
        rect=(l, t, r, b),
        minimized=minimized,
    )
