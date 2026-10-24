"""Unit tests for Claude Desktop window filtering based on real machine process data."""

from __future__ import annotations

from clawdlet.config import TrackerConfig
from clawdlet.geometry import Rect
from clawdlet.window_tracker import WS_EX_TOOLWINDOW, WindowCandidateInfo, is_claude_desktop_window


def _make_candidate(**overrides: object) -> WindowCandidateInfo:
    defaults: dict[str, object] = {
        "hwnd": 0x1001,
        "pid": 14084,
        "exe_name": "claude.exe",
        "exe_path": r"C:\Program Files\WindowsApps\Claude_3.6556.2.0_x64__pzs8sxrjxfjjc\app\claude.exe",
        "cmdline": (
            r"C:\Program Files\WindowsApps\Claude_3.6556.2.0_x64__pzs8sxrjxfjjc\app\claude.exe",
        ),
        "class_name": "Chrome_WidgetWin_1",
        "title": "Claude",
        "visible": True,
        "minimized": False,
        "cloaked": 0,
        "ex_style": 0,
        "rect": Rect(120.0, 80.0, 1320.0, 860.0),
    }
    defaults.update(overrides)
    return WindowCandidateInfo(**defaults)  # type: ignore[arg-type]


def test_accepts_real_msix_claude_desktop_main_window() -> None:
    cfg = TrackerConfig()
    main_win = _make_candidate()
    assert is_claude_desktop_window(main_win, cfg) is True


def test_accepts_standard_squirrel_installer_claude_desktop_window() -> None:
    cfg = TrackerConfig()
    squirrel_win = _make_candidate(
        exe_path=r"C:\Users\cytsa\AppData\Local\AnthropicClaude\app-0.7.8\claude.exe",
        cmdline=(r"C:\Users\cytsa\AppData\Local\AnthropicClaude\app-0.7.8\claude.exe",),
    )
    assert is_claude_desktop_window(squirrel_win, cfg) is True


def test_rejects_claude_code_cli_processes() -> None:
    cfg = TrackerConfig()
    cli_win = _make_candidate(
        pid=7284,
        exe_path=r"C:\Users\cytsa\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\2.1.308\c17a819afb40\claude.exe",
    )
    assert is_claude_desktop_window(cli_win, cfg) is False


def test_rejects_electron_helper_and_ole_windows() -> None:
    cfg = TrackerConfig()

    # Utility child process with --type=utility
    utility_win = _make_candidate(
        pid=4204,
        cmdline=(
            r"C:\Program Files\WindowsApps\Claude_3.6556.2.0_x64__pzs8sxrjxfjjc\app\claude.exe",
            "--type=utility",
        ),
    )
    assert is_claude_desktop_window(utility_win, cfg) is False

    # Hidden OleMainThreadWndName window
    ole_win = _make_candidate(
        pid=10628,
        class_name="OleMainThreadWndClass",
        title="OleMainThreadWndName",
    )
    assert is_claude_desktop_window(ole_win, cfg) is False


def test_rejects_minimized_cloaked_tool_or_tiny_windows() -> None:
    cfg = TrackerConfig()
    assert is_claude_desktop_window(_make_candidate(minimized=True), cfg) is False
    assert is_claude_desktop_window(_make_candidate(visible=False), cfg) is False
    assert is_claude_desktop_window(_make_candidate(cloaked=1), cfg) is False
    assert is_claude_desktop_window(_make_candidate(ex_style=WS_EX_TOOLWINDOW), cfg) is False
    assert is_claude_desktop_window(_make_candidate(rect=Rect(0, 0, 120, 80)), cfg) is False
