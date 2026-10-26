"""Win32 API window discovery and 60Hz DWM frame bounds tracker for Claude Desktop."""

from __future__ import annotations

import ctypes
from collections import deque
from collections.abc import Sequence
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


ROLE_MAIN = "main"        # The process owning the Claude Desktop window (Electron browser process)
ROLE_NETWORK = "network"  # Its network-service utility child: where a streamed reply arrives

_FAILED_INFO_TTL_S = 10.0   # Failed process queries are retried after this long
_INFO_CACHE_IDLE_S = 30.0   # Cached process info not looked up for this long is dropped


@dataclass(frozen=True)
class ProcessIoSample:
    """I/O rate of one sampled process in the Claude window's tree (for --debug output)."""

    pid: int
    role: str
    io_rate: float             # Bytes per second since the previous sample of this process
    first_sample: bool = False  # Baseline only: the process was just seen and contributes 0


@dataclass(frozen=True)
class ThinkingSample:
    """Most recent CPU/IO sample used by the Claude 'thinking' heuristic (for --debug output)."""

    proc_count: int
    cpu_rate: float  # CPU seconds per second across sampled Claude processes
    io_rate: float   # Bytes per second across sampled Claude processes
    is_thinking: bool
    active_samples: int = 0  # Active samples inside the sliding window
    main_pid: int | None = None  # Owner of the locked Claude window (None: nothing sampled)
    procs: tuple[ProcessIoSample, ...] = ()
    skipped: int = 0         # Direct children whose command line could not be read (fail closed)


@dataclass
class _ProcessInfo:
    name: str
    exe: str
    cmdline: tuple[str, ...] | None  # None: the command line could not be read
    failed: bool                     # Any part of the query failed: cached briefly only
    cached_at: float
    last_used: float


def classify_child_process(
    cmdline: Sequence[str] | None,
    utility_subtypes: Sequence[str] = ("network.mojom.NetworkService",),
) -> str | None:
    """Role of a direct child of the Claude window owner, or None if its I/O must not be sampled.

    Only network-service utilities count: renderers and the GPU process trade compositor frames
    whenever anything animates, and node/MCP utilities do their own disk and pipe I/O. An
    unreadable command line (None) is never sampled.
    """
    if not cmdline or "--type=utility" not in cmdline:
        return None
    prefix = "--utility-sub-type="
    for arg in cmdline:
        if arg.startswith(prefix):
            return ROLE_NETWORK if arg[len(prefix):] in utility_subtypes else None
    return None


class StreamingActivityDetector:
    """Debounced "is Claude streaming?" decision from periodic I/O-rate samples.

    A sample is active when its I/O rate reaches ``threshold``. Thinking starts once at least
    ``min_active`` active samples fall inside the last ``window_s`` seconds, so isolated bursts
    (session saves, settings writes) never trigger it, and holds for ``hold_s`` afterwards.
    """

    def __init__(
        self,
        threshold: float = 8192.0,
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


def window_owner_pid(hwnd: int) -> int | None:
    """PID of the process that created ``hwnd``, or None if it cannot be resolved."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return None
    return int(pid) if pid else None


def is_foreground_window(hwnd: int) -> bool:
    try:
        return int(win32gui.GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def focus_window(hwnd: int) -> bool:
    """Bring ``hwnd`` to the foreground, restoring it first if minimized. Return True on success."""
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False  # Windows may refuse focus stealing; never let that crash the pet


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
        self._info_cache: dict[tuple[int, float], _ProcessInfo] = {}
        self._scan_memo: dict[int, _ProcessInfo] | None = None
        self._owner_pid: tuple[int, int] | None = None  # (hwnd, pid) of the locked window
        self._last_thinking_sample_time: float = -999.0
        # Sampled process set: (process, (pid, create_time), role), rebuilt periodically
        self._tree: list[tuple[psutil.Process, tuple[int, float], str]] = []
        self._tree_main_pid: int | None = None
        self._tree_built_at: float = float("-inf")
        self._tree_skipped: int = 0
        # Per-process (io_bytes, cpu_s) baselines keyed by (pid, create_time)
        self._io_baselines: dict[tuple[int, float], tuple[int, float]] = {}
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

    def poll_thinking_status(self, now_s: float, main_pid: int | None = None) -> bool:
        """Detect whether Claude Desktop is streaming a reply, from its own process tree's I/O.

        Only ``main_pid`` (the Claude window owner) and its direct network-service children are
        measured. Each process gets its own delta, so a process appearing with megabytes of
        historical I/O (or a reused PID) never shows up as a burst. No ``main_pid`` -> not thinking.
        """
        if (now_s - self._last_thinking_sample_time) < 0.45:
            return self._thinking.is_active(now_s)

        dt = now_s - self._last_thinking_sample_time
        self._last_thinking_sample_time = now_s
        tree_age = now_s - self._tree_built_at
        if main_pid != self._tree_main_pid or tree_age >= self.cfg.thinking_tree_refresh_s:
            self._rebuild_sampled_tree(now_s, main_pid)

        valid_dt = 0.1 <= dt <= 5.0
        total_cpu_rate = 0.0
        total_io_rate = 0.0
        procs: list[ProcessIoSample] = []
        seen: set[tuple[int, float]] = set()
        for proc, key, role in self._tree:
            # A single vanished/protected process must not zero out the whole sample
            try:
                io = proc.io_counters()
                cpu = proc.cpu_times()
            except (psutil.Error, OSError):
                continue
            io_total = int(io.read_bytes + io.write_bytes)
            cpu_total = float(cpu.user + cpu.system)
            seen.add(key)
            prev = self._io_baselines.get(key)
            self._io_baselines[key] = (io_total, cpu_total)
            io_rate = 0.0
            if prev is not None and valid_dt:
                d_io = io_total - prev[0]
                if d_io >= 0:  # A counter going backwards just resets the baseline
                    io_rate = d_io / dt
                total_cpu_rate += max(0.0, cpu_total - prev[1]) / dt
            total_io_rate += io_rate
            procs.append(ProcessIoSample(pid=key[0], role=role, io_rate=io_rate, first_sample=prev is None))
        for key in [k for k in self._io_baselines if k not in seen]:
            del self._io_baselines[key]

        self._is_thinking = self._thinking.update(now_s, total_io_rate)
        self.last_thinking_sample = ThinkingSample(
            proc_count=len(procs),
            cpu_rate=total_cpu_rate,
            io_rate=total_io_rate,
            is_thinking=self._is_thinking,
            active_samples=self._thinking.active_samples,
            main_pid=main_pid,
            procs=tuple(procs),
            skipped=self._tree_skipped,
        )
        return self._is_thinking

    def _rebuild_sampled_tree(self, now_s: float, main_pid: int | None) -> None:
        """Collect the window owner plus its direct network-service children for I/O sampling."""
        self._tree = []
        self._tree_main_pid = main_pid
        self._tree_built_at = now_s
        self._tree_skipped = 0
        if main_pid is None:
            return
        try:
            main = psutil.Process(main_pid)
            self._tree.append((main, (main_pid, float(main.create_time())), ROLE_MAIN))
            children = main.children(recursive=False)
        except (psutil.Error, OSError, ValueError):
            return
        for child in children:
            try:
                key = (int(child.pid), float(child.create_time()))
            except (psutil.Error, OSError):
                continue
            info = self._process_info(key[0], now_s, proc=child, create_time=key[1])
            if info.cmdline is None:
                self._tree_skipped += 1  # Unreadable command line: fail closed
                continue
            role = classify_child_process(info.cmdline, self.cfg.thinking_utility_subtypes)
            if role is not None:
                self._tree.append((child, key, role))

    def _process_info(
        self,
        pid: int,
        now_s: float,
        proc: psutil.Process | None = None,
        create_time: float | None = None,
    ) -> _ProcessInfo:
        """Name, exe and command line of ``pid``, cached per (pid, create_time) so PID reuse misses."""
        if create_time is None:
            try:
                proc = psutil.Process(pid)
                create_time = float(proc.create_time())
            except (psutil.Error, OSError, ValueError):
                proc = None
                create_time = 0.0  # Vanished or protected: cached as a short-lived failure
        key = (pid, create_time)
        cached = self._info_cache.get(key)
        if cached is not None and not (cached.failed and now_s - cached.cached_at >= _FAILED_INFO_TTL_S):
            cached.last_used = now_s
            return cached
        name, exe, cmd, failed = "", "", None, True
        if proc is not None:
            try:
                name, exe = proc.name() or "", proc.exe() or ""
                failed = False
            except (psutil.Error, OSError, ValueError):
                pass
            try:
                cmd = tuple(proc.cmdline() or ())
            except (psutil.Error, OSError, ValueError):
                cmd, failed = None, True
        info = _ProcessInfo(name, exe, cmd, failed, now_s, now_s)
        self._info_cache[key] = info
        return info

    def _prune_info_cache(self, now_s: float) -> None:
        for key in [k for k, v in self._info_cache.items() if now_s - v.last_used > _INFO_CACHE_IDLE_S]:
            del self._info_cache[key]

    def _query_process_info(self, pid: int) -> tuple[str, str, tuple[str, ...]]:
        """(name, exe, cmdline) of ``pid``; an unreadable command line comes back empty."""
        memo = self._scan_memo
        info = memo.get(pid) if memo is not None else None
        if info is None:
            info = self._process_info(pid, time.monotonic())
            if memo is not None:
                memo[pid] = info  # One process lookup per PID per EnumWindows pass
        return info.name, info.exe, info.cmdline or ()

    def _locked_owner_pid(self, hwnd: int) -> int | None:
        """Owner PID of ``hwnd``, cached for as long as the same window stays locked."""
        if self._owner_pid is not None and self._owner_pid[0] == hwnd:
            return self._owner_pid[1]
        pid = window_owner_pid(hwnd)
        self._owner_pid = (hwnd, pid) if pid is not None else None  # Retry unresolved owners
        return pid

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

        self._scan_memo = {}
        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass
        finally:
            self._scan_memo = None
            self._prune_info_cache(time.monotonic())
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
                self._owner_pid = None
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
                        thinking = self.poll_thinking_status(now, self._locked_owner_pid(self._locked_hwnd))
                        return WindowSnapshot(
                            hwnd=self._locked_hwnd,
                            rect=self.mapper.to_logical_rect(phys_rect),
                            minimized=False,
                            visible=True,
                            is_thinking=thinking,
                            maximized=is_window_maximized(self._locked_hwnd),
                            foreground=is_foreground_window(self._locked_hwnd),
                        )
                except Exception:
                    self._locked_hwnd = None

        # Rescan at 2 Hz whether locked (verify) or unlocked (Claude not open), never every frame
        if (now - self._last_scan_time) >= self.cfg.rescan_interval_s:
            self._last_scan_time = now
            snap = self.find_best_claude_window()
            self._locked_hwnd = snap.hwnd if snap is not None else None
            if snap is not None:
                thinking = self.poll_thinking_status(now, self._locked_owner_pid(snap.hwnd))
                return WindowSnapshot(
                    hwnd=snap.hwnd,
                    rect=snap.rect,
                    minimized=snap.minimized,
                    visible=snap.visible,
                    is_thinking=thinking,
                    maximized=is_window_maximized(snap.hwnd),
                    foreground=is_foreground_window(snap.hwnd),
                )
            return None

        return None
