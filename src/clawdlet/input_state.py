"""Global pointer position and user-idle sampling (Milestone 5 cursor awareness & sleep)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class PointerSnapshot:
    """Logical-coordinate cursor position plus seconds since the user's last keyboard/mouse input."""

    x: float
    y: float
    idle_s: float = 0.0


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds_from_ticks(now_tick_ms: int, last_input_tick_ms: int) -> float:
    """Return idle seconds between two 32-bit GetTickCount values, handling the ~49.7 day wrap."""
    elapsed_ms = (int(now_tick_ms) - int(last_input_tick_ms)) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def read_user_idle_s() -> float:
    """Seconds since the last system-wide keyboard or mouse input (GetLastInputInfo)."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now_tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return idle_seconds_from_ticks(now_tick, info.dwTime)
    except Exception:
        return 0.0


def read_pointer_snapshot() -> PointerSnapshot | None:
    """Sample the cursor position (physical == logical under Per-Monitor DPI V2) and user idle time."""
    try:
        import win32api

        cx, cy = win32api.GetCursorPos()
    except Exception:
        # GetCursorPos fails on the secure desktop (lock screen / UAC prompt)
        return None
    return PointerSnapshot(x=float(cx), y=float(cy), idle_s=read_user_idle_s())
