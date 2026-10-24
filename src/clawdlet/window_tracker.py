"""Win32 API window discovery and 60Hz DWM frame bounds tracker for Claude Desktop."""

from __future__ import annotations

import ctypes
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
import time

import psutil
import win32con
import win32gui
import win32process

from clawdlet.behavior import WindowSnapshot
from clawdlet.config import TrackerConfig
from clawdlet.geometry import PixelMapper, Rect

WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SPI_GETWORKAREA = 0x0030


@dataclass(frozen=True)
class WindowCandidateInfo:
    """Metadata of a top-level Win32 window evaluated by the filter."""

    hwnd: int
    pid: int
    exe_name: str
    exe_path: str
    cmdline: tuple[str, ...]
    class_name: str
    title: str
    visible: bool
    minimized: bool
    cloaked: int
    ex_style: int
    rect: Rect


@dataclass(frozen=True)
class ThinkingSample:
    """Most recent CPU/IO sample used by the Claude 'thinking' heuristic (for --debug output)."""

    proc_count: int
    cpu_rate: float  # CPU seconds per second across sampled Claude processes
    io_rate: float   # Bytes per second across sampled Claude processes
    is_thinking: bool
    active_samples: int = 0  # Active samples inside the sliding window


# Chromium helper roles whose I/O never reflects a response streaming in: the GPU process and
# renderers exchange compositor frames continuously while anything on screen animates.
_IGNORED_PROCESS_TYPES = ("--type=gpu-process", "--type=renderer", "--type=crashpad")


class StreamingActivityDetector:
    """Debounced "is Claude streaming?" decision from periodic I/O-rate samples.

    A sample is active when its I/O rate reaches ``threshold``. Thinking starts once at least
    ``min_active`` active samples fall inside the last ``window_s`` seconds, so isolated bursts
    (session saves, settings writes) never trigger it, and holds for ``hold_s`` afterwards.
    """

    def __init__(
        self,
        threshold: float = 3072.0,
        window_s: float = 3.0,
        min_active: int = 3,
        hold_s: float = 1.5,
    ) -> None:
        self.threshold = threshold
        self.window_s = window_s
        self.min_active = min_active
        self.hold_s = hold_s
        self._active_times: deque[float] = deque()
        self._hold_until = float("-inf")

    @property
    def active_samples(self) -> int:
        return len(self._active_times)

    def update(self, now_s: float, io_rate: float) -> bool:
        if io_rate >= self.threshold:
            self._active_times.append(now_s)
        cutoff = now_s - self.window_s
        while self._active_times and self._active_times[0] < cutoff:
            self._active_times.popleft()
        if len(self._active_times) >= self.min_active:
            self._hold_until = now_s + self.hold_s
        return now_s < self._hold_until

    def is_active(self, now_s: float) -> bool:
        return now_s < self._hold_until


def is_claude_desktop_window(info: WindowCandidateInfo, cfg: TrackerConfig) -> bool:
    """Pure predicate to determine if a window belongs to the main Claude Desktop GUI."""
    if not info.visible or info.minimized or info.cloaked != 0:
        return False
    if info.ex_style & WS_EX_TOOLWINDOW:
        return False
    if info.exe_name.lower() != cfg.target_exe_name.lower():
        return False

    exe_path_lower = info.exe_path.lower()
    if any(kw.lower() in exe_path_lower for kw in cfg.excluded_path_keywords):
        return False

    # Exclude Electron child processes (--type=renderer, --type=utility, --type=gpu-process, etc.)
    if any(arg.startswith("--type=") for arg in info.cmdline):
        return False

    if not info.title or info.title in cfg.excluded_titles:
        return False

    if cfg.allowed_classes and info.class_name not in cfg.allowed_classes:
        return False

    if info.rect.width < cfg.min_width or info.rect.height < cfg.min_height:
        return False

    return True


def get_dwm_extended_frame_rect(hwnd: int) -> Rect:
    """Return the visible window bounds (excluding invisible Win10/11 resize borders)."""
    try:
        dwmapi = ctypes.windll.dwmapi
        rect = wintypes.RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0 and (rect.right > rect.left) and (rect.bottom > rect.top):
            return Rect(
                left=float(rect.left),
                top=float(rect.top),
                right=float(rect.right),
                bottom=float(rect.bottom),
            )
    except Exception:
        pass

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return Rect(left=float(l), top=float(t), right=float(r), bottom=float(b))


def get_window_cloaked(hwnd: int) -> int:
    """Return non-zero if the window is cloaked by DWM (e.g., on another virtual desktop)."""
    try:
        dwmapi = ctypes.windll.dwmapi
        cloaked = wintypes.DWORD(0)
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_CLOAKED),
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        if hr == 0:
            return int(cloaked.value)
    except Exception:
        pass
    return 0


def is_window_maximized(hwnd: int) -> bool:
    try:
        return bool(ctypes.windll.user32.IsZoomed(wintypes.HWND(hwnd)))
    except Exception:
        return False


def nudge_window(hwnd: int, dx: int) -> None:
    """Move a top-level window horizontally by ``dx`` px without resizing, re-stacking or focusing it."""
    if dx == 0 or not win32gui.IsWindow(hwnd) or is_window_maximized(hwnd):
        return
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    flags = (
        win32con.SWP_NOSIZE
        | win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE
        | win32con.SWP_NOOWNERZORDER
    )
    win32gui.SetWindowPos(hwnd, 0, left + int(dx), top, 0, 0, flags)


def get_physical_work_area() -> Rect:
    """Query primary monitor work area via SPI_GETWORKAREA in physical pixels."""
    rect = wintypes.RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    if ok and rect.right > rect.left and rect.bottom > rect.top:
        return Rect(
            left=float(rect.left),
            top=float(rect.top),
            right=float(rect.right),
            bottom=float(rect.bottom),
        )
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    return Rect(0.0, 0.0, float(w), float(h))


class Win32ClaudeTracker:
    """Tracks the active Claude Desktop window with PID caching and DWM bounds."""

    def __init__(
        self,
        mapper: PixelMapper | None = None,
        cfg: TrackerConfig | None = None,
    ) -> None:
        self.mapper = mapper or PixelMapper(1.0)
        self.cfg = cfg or TrackerConfig()
        self._locked_hwnd: int | None = None
        self._last_scan_time: float = -999.0
        self._pid_cache: dict[int, tuple[str, str, tuple[str, ...]]] = {}
        self._last_thinking_sample_time: float = -999.0
        self._prev_proc_totals: tuple[float, int] | None = None
        self._thinking = StreamingActivityDetector(
            threshold=self.cfg.thinking_io_threshold,
            window_s=self.cfg.thinking_window_s,
            min_active=self.cfg.thinking_min_active,
            hold_s=self.cfg.thinking_hold_s,
        )
        self._is_thinking: bool = False
        self.last_thinking_sample: ThinkingSample | None = None

    @property
    def locked_hwnd(self) -> int | None:
        return self._locked_hwnd

    def poll_thinking_status(self, now_s: float) -> bool:
        """Detect whether Claude Desktop / Claude Code is actively thinking or streaming."""
        if (now_s - self._last_thinking_sample_time) < 0.45:
            return self._thinking.is_active(now_s)

        dt = now_s - self._last_thinking_sample_time
        self._last_thinking_sample_time = now_s

        total_cpu = 0.0
        total_io = 0
        proc_count = 0
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                # A single vanished/protected process must not zero out the whole sample
                try:
                    name = proc.info.get("name") or ""
                    if name.lower() != self.cfg.target_exe_name.lower():
                        continue
                    pid = int(proc.info.get("pid") or 0)
                    _, exe_path, cmdline = self._query_process_info(pid)
                    # Exclude Claude Code CLI children and compositor/crash helper processes
                    exe_path_lower = exe_path.lower()
                    if any(kw.lower() in exe_path_lower for kw in self.cfg.excluded_path_keywords):
                        continue
                    if any(arg.startswith(_IGNORED_PROCESS_TYPES) for arg in cmdline):
                        continue
                    cpu = proc.cpu_times()
                    io = proc.io_counters()
                    total_cpu += float(cpu.user + cpu.system)
                    total_io += int(io.read_bytes + io.write_bytes)
                    proc_count += 1
                except (psutil.Error, OSError):
                    continue
        except Exception:
            pass

        cpu_rate = 0.0
        io_rate = 0.0
        if self._prev_proc_totals is not None and 0.1 <= dt <= 5.0:
            prev_cpu, prev_io = self._prev_proc_totals
            d_cpu = max(0.0, total_cpu - prev_cpu)
            d_io = max(0, total_io - prev_io)
            cpu_rate = d_cpu / dt
            io_rate = d_io / dt

        self._prev_proc_totals = (total_cpu, total_io)
        self._is_thinking = self._thinking.update(now_s, io_rate)
        self.last_thinking_sample = ThinkingSample(
            proc_count=proc_count,
            cpu_rate=cpu_rate,
            io_rate=io_rate,
            is_thinking=self._is_thinking,
            active_samples=self._thinking.active_samples,
        )
        return self._is_thinking

    def _query_process_info(self, pid: int) -> tuple[str, str, tuple[str, ...]]:
        cached = self._pid_cache.get(pid)
        if cached is not None:
            return cached
        try:
            proc = psutil.Process(pid)
            name = proc.name() or ""
            exe = proc.exe() or ""
            try:
                cmd = tuple(proc.cmdline() or ())
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cmd = ()
            info = (name, exe, cmd)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
            info = ("", "", ())
        self._pid_cache[pid] = info
        return info

    def inspect_hwnd(self, hwnd: int) -> WindowCandidateInfo | None:
        """Build a WindowCandidateInfo for ``hwnd``, or return None if invalid."""
        if not win32gui.IsWindow(hwnd):
            return None
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe_name, exe_path, cmdline = self._query_process_info(pid)
            visible = bool(win32gui.IsWindowVisible(hwnd))
            minimized = bool(win32gui.IsIconic(hwnd))
            cloaked = get_window_cloaked(hwnd)
            class_name = win32gui.GetClassName(hwnd) or ""
            title = win32gui.GetWindowText(hwnd) or ""
            ex_style = int(win32gui.GetWindowLong(hwnd, GWL_EXSTYLE))
            phys_rect = get_dwm_extended_frame_rect(hwnd)
            logical_rect = self.mapper.to_logical_rect(phys_rect)
            return WindowCandidateInfo(
                hwnd=hwnd,
                pid=pid,
                exe_name=exe_name,
                exe_path=exe_path,
                cmdline=cmdline,
                class_name=class_name,
                title=title,
                visible=visible,
                minimized=minimized,
                cloaked=cloaked,
                ex_style=ex_style,
                rect=logical_rect,
            )
        except Exception:
            return None

    def scan_candidates(self, only_claude_exe: bool = False) -> list[WindowCandidateInfo]:
        """Enumerate top-level windows and return candidate metadata."""
        results: list[WindowCandidateInfo] = []

        def _enum_cb(hwnd: int, _: object) -> bool:
            info = self.inspect_hwnd(hwnd)
            if info is not None:
                if not only_claude_exe or info.exe_name.lower() == self.cfg.target_exe_name.lower():
                    results.append(info)
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass
        finally:
            ctypes.windll.kernel32.SetLastError(0)
        return results

    def find_best_claude_window(self) -> WindowSnapshot | None:
        """Scan all top-level windows and pick the best matching Claude Desktop window."""
        candidates = [
            info
            for info in self.scan_candidates(only_claude_exe=True)
            if is_claude_desktop_window(info, self.cfg)
        ]
        if not candidates:
            return None

        fg_hwnd = 0
        try:
            fg_hwnd = int(win32gui.GetForegroundWindow())
        except Exception:
            pass

        # Prefer foreground Claude window; otherwise choose the largest visible window
        candidates.sort(
            key=lambda c: (1 if c.hwnd == fg_hwnd else 0, c.rect.width * c.rect.height),
            reverse=True,
        )
        best = candidates[0]
        return WindowSnapshot(
            hwnd=best.hwnd,
            rect=best.rect,
            minimized=best.minimized,
            visible=best.visible,
        )

    def poll(self, now_s: float | None = None) -> WindowSnapshot | None:
        """Poll locked window at 60Hz and rescan at 2Hz when needed."""
        now = time.monotonic() if now_s is None else now_s

        if self._locked_hwnd is not None:
            if not win32gui.IsWindow(self._locked_hwnd):
                self._locked_hwnd = None
            else:
                try:
                    minimized = bool(win32gui.IsIconic(self._locked_hwnd))
                    visible = bool(win32gui.IsWindowVisible(self._locked_hwnd))
                    cloaked = get_window_cloaked(self._locked_hwnd)
                    if minimized or not visible or cloaked != 0:
                        # Window was minimized or hidden; report non-perchable snapshot once and unlock
                        phys_rect = get_dwm_extended_frame_rect(self._locked_hwnd)
                        snap = WindowSnapshot(
                            hwnd=self._locked_hwnd,
                            rect=self.mapper.to_logical_rect(phys_rect),
                            minimized=True,
                            visible=False,
                        )
                        self._locked_hwnd = None
                        self._last_scan_time = now
                        return snap

                    if (now - self._last_scan_time) < self.cfg.rescan_interval_s:
                        phys_rect = get_dwm_extended_frame_rect(self._locked_hwnd)
                        thinking = self.poll_thinking_status(now)
                        return WindowSnapshot(
                            hwnd=self._locked_hwnd,
                            rect=self.mapper.to_logical_rect(phys_rect),
                            minimized=False,
                            visible=True,
                            is_thinking=thinking,
                            maximized=is_window_maximized(self._locked_hwnd),
                        )
                except Exception:
                    self._locked_hwnd = None

        # Rescan at 2 Hz whether locked (verify) or unlocked (Claude not open), never every frame
        if (now - self._last_scan_time) >= self.cfg.rescan_interval_s:
            self._last_scan_time = now
            snap = self.find_best_claude_window()
            self._locked_hwnd = snap.hwnd if snap is not None else None
            if snap is not None:
                thinking = self.poll_thinking_status(now)
                return WindowSnapshot(
                    hwnd=snap.hwnd,
                    rect=snap.rect,
                    minimized=snap.minimized,
                    visible=snap.visible,
                    is_thinking=thinking,
                    maximized=is_window_maximized(snap.hwnd),
                )
            return None

        return None
