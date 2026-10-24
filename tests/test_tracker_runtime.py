"""Runtime tests for Win32ClaudeTracker: thinking heuristic sampling and unlocked rescan throttling."""

from __future__ import annotations

from dataclasses import dataclass

import psutil
import pytest

from clawdlet import window_tracker
from clawdlet.window_tracker import Win32ClaudeTracker

DESKTOP_EXE = r"C:\Program Files\WindowsApps\Claude_3.6556.2.0_x64__pzs8sxrjxfjjc\app\claude.exe"
CLI_EXE = r"C:\Users\dev\AppData\Roaming\npm\node_modules\claude-code\bin\claude.exe"


@dataclass
class _CpuTimes:
    user: float
    system: float


@dataclass
class _IoCounters:
    read_bytes: int
    write_bytes: int


class _FakeProc:
    def __init__(self, pid: int, cpu_s: float, io_bytes: int, io_error: bool = False) -> None:
        self.info = {"pid": pid, "name": "claude.exe"}
        self.cpu_s = cpu_s
        self.io_bytes = io_bytes
        self.io_error = io_error

    def cpu_times(self) -> _CpuTimes:
        return _CpuTimes(user=self.cpu_s, system=0.0)

    def io_counters(self) -> _IoCounters:
        if self.io_error:
            raise psutil.AccessDenied(pid=self.info["pid"])
        return _IoCounters(read_bytes=self.io_bytes, write_bytes=0)


def _install_fake_processes(
    monkeypatch: pytest.MonkeyPatch,
    tracker: Win32ClaudeTracker,
    procs: list[_FakeProc],
    exe_by_pid: dict[int, str],
) -> None:
    monkeypatch.setattr(window_tracker.psutil, "process_iter", lambda _attrs=None: iter(procs))
    monkeypatch.setattr(
        tracker,
        "_query_process_info",
        lambda pid: ("claude.exe", exe_by_pid[pid], (exe_by_pid[pid],)),
    )


def test_thinking_detected_from_sustained_desktop_io(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Win32ClaudeTracker()
    desktop = _FakeProc(pid=100, cpu_s=10.0, io_bytes=1_000_000)
    blocked = _FakeProc(pid=101, cpu_s=5.0, io_bytes=0, io_error=True)
    _install_fake_processes(
        monkeypatch, tracker, [desktop, blocked], {100: DESKTOP_EXE, 101: DESKTOP_EXE}
    )

    assert tracker.poll_thinking_status(10.0) is False
    assert tracker.last_thinking_sample is not None
    assert tracker.last_thinking_sample.proc_count == 1  # AccessDenied process skipped, not fatal

    # Streaming reply: ~40 KB/s of main-process I/O across consecutive 0.5 s samples
    results = []
    for i in range(1, 4):
        desktop.cpu_s += 0.1
        desktop.io_bytes += 20 * 1024
        results.append(tracker.poll_thinking_status(10.0 + 0.5 * i))
    assert results == [False, False, True]  # one burst is not enough; sustained I/O is
    sample = tracker.last_thinking_sample
    assert sample.io_rate == pytest.approx(40 * 1024)
    assert sample.active_samples == 3
    assert sample.is_thinking is True

    # Stream ends: thinking holds briefly, then clears
    assert tracker.poll_thinking_status(12.0) is True
    assert tracker.poll_thinking_status(14.0) is False


def test_thinking_ignores_renderer_gpu_compositor_traffic(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Win32ClaudeTracker()
    renderer = _FakeProc(pid=300, cpu_s=1.0, io_bytes=0)
    gpu = _FakeProc(pid=301, cpu_s=1.0, io_bytes=0)
    procs = {300: DESKTOP_EXE + " --type=renderer", 301: DESKTOP_EXE + " --type=gpu-process"}
    monkeypatch.setattr(window_tracker.psutil, "process_iter", lambda _attrs=None: iter([renderer, gpu]))
    monkeypatch.setattr(
        tracker,
        "_query_process_info",
        lambda pid: ("claude.exe", DESKTOP_EXE, tuple(procs[pid].split(" "))),
    )
    tracker.poll_thinking_status(10.0)
    for i in range(1, 10):
        # A spinner animating at 60 FPS: ~4.6 MB/s of frames between renderer and GPU process
        renderer.io_bytes += 2_300_000
        gpu.io_bytes += 2_300_000
        assert tracker.poll_thinking_status(10.0 + 0.5 * i) is False
    assert tracker.last_thinking_sample.proc_count == 0


def test_streaming_detector_replays_recorded_desktop_traces() -> None:
    """KB/s of main+network+node processes, sampled every 0.5 s on a real Claude Desktop."""
    tool_wait = [2390.4, 0.2, 0.2, 0.2, 0.2, 0.2, 1.6, 1.6, 2.8, 1.2, 0.2, 0.2, 0.2, 56.7, 1.9, 0.2,
                 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2]
    streaming = [40.1, 0.2, 45.1, 14.9, 0.2, 0.0, 11.3, 1.1, 0.2, 49.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2,
                 2.8, 54.4, 120.3, 16.2, 1.2, 18.0, 146.5, 16.9, 23.8, 43.7]

    det = window_tracker.StreamingActivityDetector()
    assert not any(det.update(i * 0.5, kbs * 1024) for i, kbs in enumerate(tool_wait))

    det = window_tracker.StreamingActivityDetector()
    states = [det.update(100.0 + i * 0.5, kbs * 1024) for i, kbs in enumerate(streaming)]
    assert sum(states) / len(states) > 0.5
    assert all(states[-6:])


def test_thinking_ignores_claude_code_cli_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Win32ClaudeTracker()
    cli = _FakeProc(pid=200, cpu_s=10.0, io_bytes=1_000_000)
    _install_fake_processes(monkeypatch, tracker, [cli], {200: CLI_EXE})

    tracker.poll_thinking_status(10.0)
    cli.cpu_s += 5.0
    cli.io_bytes += 10_000_000
    assert tracker.poll_thinking_status(11.0) is False
    assert tracker.last_thinking_sample.proc_count == 0


def test_unlocked_rescan_is_throttled_to_rescan_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Win32ClaudeTracker()
    calls: list[float] = []

    def _fake_find() -> None:
        calls.append(0.0)
        return None

    monkeypatch.setattr(tracker, "find_best_claude_window", _fake_find)

    for t in (0.0, 0.016, 0.1, 0.25, 0.45):
        assert tracker.poll(now_s=t) is None
    assert len(calls) == 1

    assert tracker.poll(now_s=0.5) is None
    assert len(calls) == 2
