"""Runtime tests for Win32ClaudeTracker: thinking heuristic sampling and unlocked rescan throttling."""

from __future__ import annotations

from dataclasses import dataclass
import random

import psutil
import pytest

from clawdlet import window_tracker
from clawdlet.app import format_thinking_sample
from clawdlet.config import TrackerConfig
from clawdlet.geometry import Rect
from clawdlet.tools.dump_windows import sampling_role
from clawdlet.window_tracker import (
    ROLE_MAIN,
    ROLE_NETWORK,
    ProcessIoSample,
    ThinkingSample,
    Win32ClaudeTracker,
    classify_child_process,
)

DESKTOP_EXE = r"C:\Program Files\WindowsApps\Claude_3.6556.2.0_x64__pzs8sxrjxfjjc\app\claude.exe"
NATIVE_CLI_EXE = r"C:\Users\dev\.local\bin\claude.exe"
NETWORK_CMD = (DESKTOP_EXE, "--type=utility", "--utility-sub-type=network.mojom.NetworkService",
               "--lang=en-US", "--service-sandbox-type=none")
NODE_CMD = (DESKTOP_EXE, "--type=utility", "--utility-sub-type=node.mojom.NodeService", "--lang=en-US")
RENDERER_CMD = (DESKTOP_EXE, "--type=renderer", "--app-path=x")
GPU_CMD = (DESKTOP_EXE, "--type=gpu-process")


@dataclass
class _CpuTimes:
    user: float
    system: float


@dataclass
class _IoCounters:
    read_bytes: int
    write_bytes: int


class _FakeProc:
    """Just enough of psutil.Process for the tracker: identity, children, cmdline, counters."""

    def __init__(
        self,
        pid: int,
        cmdline: tuple[str, ...] = (DESKTOP_EXE,),
        io_bytes: int = 0,
        cpu_s: float = 0.0,
        create_time: float = 1000.0,
        cmdline_error: bool = False,
        io_error: bool = False,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self.io_bytes = io_bytes
        self.cpu_s = cpu_s
        self._create_time = create_time
        self.cmdline_error = cmdline_error
        self.io_error = io_error
        self.gone = False
        self.kids: list[_FakeProc] = []
        self.cmdline_calls = 0

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = False) -> list[_FakeProc]:
        return [k for k in self.kids if not k.gone]

    def name(self) -> str:
        return "claude.exe"

    def exe(self) -> str:
        return self._cmdline[0]

    def cmdline(self) -> list[str]:
        self.cmdline_calls += 1
        if self.cmdline_error:
            raise psutil.AccessDenied(pid=self.pid)
        return list(self._cmdline)

    def cpu_times(self) -> _CpuTimes:
        return _CpuTimes(user=self.cpu_s, system=0.0)

    def io_counters(self) -> _IoCounters:
        if self.gone:
            raise psutil.NoSuchProcess(pid=self.pid)
        if self.io_error:
            raise psutil.AccessDenied(pid=self.pid)
        return _IoCounters(read_bytes=self.io_bytes, write_bytes=0)


class _FakeSystem:
    def __init__(self) -> None:
        self.procs: dict[int, _FakeProc] = {}

    def add(self, proc: _FakeProc, parent: _FakeProc | None = None) -> _FakeProc:
        self.procs[proc.pid] = proc
        if parent is not None:
            parent.kids.append(proc)
        return proc

    def process(self, pid: int) -> _FakeProc:
        proc = self.procs.get(pid)
        if proc is None or proc.gone:
            raise psutil.NoSuchProcess(pid=pid)
        return proc


@pytest.fixture
def system(monkeypatch: pytest.MonkeyPatch) -> _FakeSystem:
    fake = _FakeSystem()
    monkeypatch.setattr(window_tracker.psutil, "Process", fake.process)
    return fake


def _desktop(system: _FakeSystem) -> tuple[_FakeProc, ...]:
    """A Claude Desktop tree: browser process owning the window plus its Electron helpers."""
    main = system.add(_FakeProc(100))
    net = system.add(_FakeProc(101, NETWORK_CMD), parent=main)
    renderer = system.add(_FakeProc(102, RENDERER_CMD), parent=main)
    gpu = system.add(_FakeProc(103, GPU_CMD), parent=main)
    node = system.add(_FakeProc(104, NODE_CMD), parent=main)
    return main, net, renderer, gpu, node


def _run(tracker: Win32ClaudeTracker, start: float, n: int, main_pid: int | None, step=None) -> list[bool]:
    out = []
    for i in range(n):
        if step is not None:
            step(i)
        out.append(tracker.poll_thinking_status(start + 0.5 * i, main_pid))
    return out


def test_thinking_detected_from_sustained_desktop_io(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    main, net, *_ = _desktop(system)
    main.io_bytes, net.io_bytes = 1_000_000, 5_000_000

    assert tracker.poll_thinking_status(10.0, 100) is False
    sample = tracker.last_thinking_sample
    assert sample is not None and sample.main_pid == 100
    assert [(p.pid, p.role, p.first_sample) for p in sample.procs] == [
        (100, ROLE_MAIN, True), (101, ROLE_NETWORK, True)
    ]
    assert sample.io_rate == 0.0  # Existing counters are a baseline, not a burst

    # Streaming reply: ~40 KB/s arriving through the network service across 0.5 s samples
    results = []
    for i in range(1, 4):
        net.cpu_s += 0.1
        net.io_bytes += 18 * 1024
        main.io_bytes += 2 * 1024
        results.append(tracker.poll_thinking_status(10.0 + 0.5 * i, 100))
    assert results == [False, False, True]  # one burst is not enough; sustained I/O is
    sample = tracker.last_thinking_sample
    assert sample.io_rate == pytest.approx(40 * 1024)
    assert [p.io_rate for p in sample.procs] == pytest.approx([4 * 1024, 36 * 1024])
    assert not any(p.first_sample for p in sample.procs)
    assert sample.active_samples == 3
    assert sample.is_thinking is True

    # Stream ends: thinking holds briefly, then clears
    assert tracker.poll_thinking_status(12.0, 100) is True
    assert tracker.poll_thinking_status(14.0, 100) is False


def test_unreadable_counters_skip_one_process_without_failing_the_sample(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    main, net, *_ = _desktop(system)
    net.io_error = True
    tracker.poll_thinking_status(10.0, 100)
    assert tracker.last_thinking_sample.proc_count == 1
    assert tracker.last_thinking_sample.procs[0].role == ROLE_MAIN


def test_thinking_ignores_renderer_gpu_and_node_utility_traffic(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    _main, _net, renderer, gpu, node = _desktop(system)

    def _animate(_: int) -> None:
        # A spinner animating at 60 FPS: ~4.6 MB/s of frames between renderer and GPU process,
        # plus an MCP server busy in the node utility
        renderer.io_bytes += 2_300_000
        gpu.io_bytes += 2_300_000
        node.io_bytes += 500_000

    assert not any(_run(tracker, 10.0, 20, 100, _animate))
    sample = tracker.last_thinking_sample
    assert {p.pid for p in sample.procs} == {100, 101}
    assert sample.io_rate == 0.0


def test_process_appearing_with_existing_io_contributes_nothing(system: _FakeSystem) -> None:
    """Churn: a helper (re)started mid-session already carries megabytes of I/O."""
    tracker = Win32ClaudeTracker()
    main = system.add(_FakeProc(100))
    newcomer = _FakeProc(105, NETWORK_CMD, io_bytes=10_000_000)

    def _spawn(i: int) -> None:
        if i == 4:
            system.add(newcomer, parent=main)

    states = _run(tracker, 10.0, 24, 100, _spawn)  # Tree rebuilt at t=15 picks the newcomer up
    assert not any(states)
    rates = tracker.last_thinking_sample
    assert {p.pid for p in rates.procs} == {100, 105}
    assert rates.io_rate == 0.0


def test_first_sample_of_a_process_is_only_a_baseline(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    system.add(_FakeProc(100, io_bytes=50_000_000))
    tracker.poll_thinking_status(10.0, 100)
    assert tracker.last_thinking_sample.procs == (ProcessIoSample(100, ROLE_MAIN, 0.0, True),)
    tracker.poll_thinking_status(10.5, 100)
    assert tracker.last_thinking_sample.procs == (ProcessIoSample(100, ROLE_MAIN, 0.0, False),)


def test_counter_going_backwards_resets_the_baseline(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    main = system.add(_FakeProc(100, io_bytes=9_000_000))
    tracker.poll_thinking_status(10.0, 100)
    main.io_bytes = 1_000
    tracker.poll_thinking_status(10.5, 100)
    assert tracker.last_thinking_sample.io_rate == 0.0
    main.io_bytes += 4_096
    tracker.poll_thinking_status(11.0, 100)
    assert tracker.last_thinking_sample.io_rate == pytest.approx(8_192)


def test_pid_reuse_with_new_create_time_invalidates_caches(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    old = system.add(_FakeProc(300, NODE_CMD, create_time=1.0))
    assert tracker._process_info(300, 0.0).cmdline == NODE_CMD
    assert tracker._process_info(300, 1.0).cmdline == NODE_CMD
    assert old.cmdline_calls == 1  # Cached while the same process lives

    old.gone = True
    system.add(_FakeProc(300, NETWORK_CMD, create_time=2.0))  # Windows hands the PID to a new process
    assert tracker._process_info(300, 2.0).cmdline == NETWORK_CMD
    assert tracker._query_process_info(300)[2] == NETWORK_CMD

    # The I/O baseline is per (pid, create_time) too: a new main process is a fresh baseline
    main = system.add(_FakeProc(100, io_bytes=1_000, create_time=5.0))
    tracker.poll_thinking_status(10.0, 100)
    main.gone = True
    system.add(_FakeProc(100, io_bytes=80_000_000, create_time=6.0))
    tracker.poll_thinking_status(10.5, 100)  # Old process vanished: sampled set is empty
    assert tracker.last_thinking_sample.procs == ()
    tracker.poll_thinking_status(15.5, 100)  # Rebuilt tree finds the new process
    assert tracker.last_thinking_sample.procs == (ProcessIoSample(100, ROLE_MAIN, 0.0, True),)
    assert list(tracker._io_baselines) == [(100, 6.0)]  # Vanished key pruned


def test_failed_process_queries_are_cached_only_briefly(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    proc = system.add(_FakeProc(400, NETWORK_CMD, cmdline_error=True))
    assert tracker._process_info(400, 0.0).cmdline is None
    proc.cmdline_error = False
    assert tracker._process_info(400, 5.0).cmdline is None  # Within the failure TTL
    assert tracker._process_info(400, 10.5).cmdline == NETWORK_CMD

    assert tracker._process_info(999, 0.0).failed is True  # No such process
    tracker._prune_info_cache(100.0)
    assert tracker._info_cache == {}


def test_native_cli_outside_the_window_tree_is_ignored(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    _desktop(system)
    cli = system.add(_FakeProc(200, (NATIVE_CLI_EXE, "--resume")))

    def _cli_busy(_: int) -> None:
        cli.io_bytes += 5_000_000

    assert not any(_run(tracker, 10.0, 20, 100, _cli_busy))
    assert 200 not in {p.pid for p in tracker.last_thinking_sample.procs}


def test_access_denied_child_is_skipped(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    main = system.add(_FakeProc(100))
    hidden = system.add(_FakeProc(101, NETWORK_CMD, cmdline_error=True), parent=main)

    def _hidden_busy(_: int) -> None:
        hidden.io_bytes += 50_000

    assert not any(_run(tracker, 10.0, 10, 100, _hidden_busy))
    sample = tracker.last_thinking_sample
    assert sample.skipped == 1
    assert [p.pid for p in sample.procs] == [100]


def test_unresolved_main_pid_is_never_thinking(system: _FakeSystem) -> None:
    tracker = Win32ClaudeTracker()
    main, net, *_ = _desktop(system)

    def _busy(_: int) -> None:
        main.io_bytes += 50_000
        net.io_bytes += 50_000

    assert not any(_run(tracker, 10.0, 10, None, _busy))
    sample = tracker.last_thinking_sample
    assert sample is not None and sample.main_pid is None and sample.procs == ()
    assert not any(_run(tracker, 20.0, 10, 4242, _busy))  # Owner PID that no longer exists


def test_background_noise_never_reads_as_thinking(system: _FakeSystem) -> None:
    """30% of samples at 4-7 KB/s (session saves, telemetry) used to flip the old 3 KB/s threshold."""
    rng = random.Random(7)
    rates = [rng.uniform(4.0, 7.0) if rng.random() < 0.3 else 0.2 for _ in range(600)]
    tracker = Win32ClaudeTracker()
    _main, net, *_ = _desktop(system)

    def _noise(i: int) -> None:
        net.io_bytes += int(rates[i] * 1024 * 0.5)

    assert not any(_run(tracker, 10.0, len(rates), 100, _noise))

    old = window_tracker.StreamingActivityDetector(threshold=3072.0)
    assert any(old.update(i * 0.5, kbs * 1024) for i, kbs in enumerate(rates))


def test_streaming_detector_replays_recorded_desktop_traces() -> None:
    """KB/s of main+network+node processes, sampled every 0.5 s on a real Claude Desktop."""
    tool_wait = [2390.4, 0.2, 0.2, 0.2, 0.2, 0.2, 1.6, 1.6, 2.8, 1.2, 0.2, 0.2, 0.2, 56.7, 1.9, 0.2,
                 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2]
    streaming = [40.1, 0.2, 45.1, 14.9, 0.2, 0.0, 11.3, 1.1, 0.2, 49.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2,
                 2.8, 54.4, 120.3, 16.2, 1.2, 18.0, 146.5, 16.9, 23.8, 43.7]
    threshold = TrackerConfig().thinking_io_threshold
    assert threshold == 8192.0

    det = window_tracker.StreamingActivityDetector(threshold=threshold)
    assert not any(det.update(i * 0.5, kbs * 1024) for i, kbs in enumerate(tool_wait))

    det = window_tracker.StreamingActivityDetector(threshold=threshold)
    states = [det.update(100.0 + i * 0.5, kbs * 1024) for i, kbs in enumerate(streaming)]
    assert sum(states) / len(states) > 0.6
    assert all(states[-6:])


@pytest.mark.parametrize(
    ("cmdline", "role"),
    [
        (NETWORK_CMD, ROLE_NETWORK),
        (NODE_CMD, None),
        (RENDERER_CMD, None),
        (GPU_CMD, None),
        ((DESKTOP_EXE, "--type=utility"), None),
        ((DESKTOP_EXE, "--utility-sub-type=network.mojom.NetworkService"), None),  # not a utility
        ((), None),
        (None, None),
    ],
)
def test_classify_child_process(cmdline: tuple[str, ...] | None, role: str | None) -> None:
    assert classify_child_process(cmdline) == role


@pytest.mark.parametrize(
    ("pid", "ppid", "cmdline", "main_pid", "role"),
    [
        (100, 4, (DESKTOP_EXE,), 100, "main"),
        (101, 100, NETWORK_CMD, 100, "network"),
        (102, 100, RENDERER_CMD, 100, "skipped:renderer"),
        (104, 100, NODE_CMD, 100, "skipped:utility(node.mojom.NodeService)"),
        (105, 100, None, 100, "skipped:cmdline-denied"),
        (200, 9, (NATIVE_CLI_EXE,), 100, "skipped:outside-tree"),
        (100, 4, (DESKTOP_EXE,), None, "skipped:no-window"),
    ],
)
def test_dump_windows_sampling_role(pid, ppid, cmdline, main_pid, role) -> None:
    assert sampling_role(pid, ppid, cmdline, main_pid, TrackerConfig()) == role


def test_debug_line_lists_each_sampled_process() -> None:
    sample = ThinkingSample(
        proc_count=2, cpu_rate=0.0, io_rate=9.5 * 1024, is_thinking=False, active_samples=2,
        main_pid=1234,
        procs=(ProcessIoSample(1234, ROLE_MAIN, 0.4 * 1024), ProcessIoSample(5678, ROLE_NETWORK, 9.1 * 1024, True)),
    )
    assert format_thinking_sample(sample, 3) == (
        "io main=1234:0.4KB/s net=5678:9.1KB/s(first) total=9.5KB/s active=2/3 thinking=False"
    )
    empty = ThinkingSample(proc_count=0, cpu_rate=0.0, io_rate=0.0, is_thinking=False, skipped=1)
    assert format_thinking_sample(empty, 3) == "io main=none total=0.0KB/s skipped=1 active=0/3 thinking=False"


def _lock_fake_window(monkeypatch: pytest.MonkeyPatch, tracker: Win32ClaudeTracker, hwnd: int,
                      foreground: int) -> list[int]:
    owner_calls: list[int] = []

    def _owner(h: int) -> tuple[int, int]:
        owner_calls.append(h)
        return (1, 100)

    monkeypatch.setattr(window_tracker.win32gui, "IsWindow", lambda h: True)
    monkeypatch.setattr(window_tracker.win32gui, "IsIconic", lambda h: False)
    monkeypatch.setattr(window_tracker.win32gui, "IsWindowVisible", lambda h: True)
    monkeypatch.setattr(window_tracker.win32gui, "GetForegroundWindow", lambda: foreground)
    monkeypatch.setattr(window_tracker.win32process, "GetWindowThreadProcessId", _owner)
    monkeypatch.setattr(window_tracker, "get_window_cloaked", lambda h: 0)
    monkeypatch.setattr(window_tracker, "is_window_maximized", lambda h: False)
    monkeypatch.setattr(window_tracker, "get_dwm_extended_frame_rect", lambda h: Rect(0, 0, 800, 600))
    tracker._locked_hwnd = hwnd
    tracker._last_scan_time = 100.0
    return owner_calls


def test_poll_samples_the_locked_window_owner_and_reports_foreground(
    monkeypatch: pytest.MonkeyPatch, system: _FakeSystem
) -> None:
    _desktop(system)
    tracker = Win32ClaudeTracker()
    owner_calls = _lock_fake_window(monkeypatch, tracker, 0x1234, foreground=0x1234)
    snap = tracker.poll(now_s=100.1)
    assert snap is not None and snap.foreground is True
    assert tracker.last_thinking_sample.main_pid == 100
    tracker.poll(now_s=100.2)
    tracker.poll(now_s=100.3)
    assert owner_calls == [0x1234]  # Owner PID cached per locked window

    tracker = Win32ClaudeTracker()
    _lock_fake_window(monkeypatch, tracker, 0x1234, foreground=0x9999)
    assert tracker.poll(now_s=100.1).foreground is False


def test_focus_window_restores_then_activates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(window_tracker.win32gui, "IsWindow", lambda h: True)
    monkeypatch.setattr(window_tracker.win32gui, "IsIconic", lambda h: True)
    monkeypatch.setattr(window_tracker.win32gui, "ShowWindow", lambda h, cmd: calls.append("restore"))
    monkeypatch.setattr(window_tracker.win32gui, "SetForegroundWindow", lambda h: calls.append("focus"))
    assert window_tracker.focus_window(0x1234) is True
    assert calls == ["restore", "focus"]

    def _refuse(h: int) -> None:
        raise OSError("SetForegroundWindow refused")

    monkeypatch.setattr(window_tracker.win32gui, "SetForegroundWindow", _refuse)
    assert window_tracker.focus_window(0x1234) is False


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
