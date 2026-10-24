"""Diagnostic CLI to inspect Claude processes and top-level Win32 windows."""

from __future__ import annotations

import ctypes
import sys

import psutil

from clawdlet.config import TrackerConfig
from clawdlet.geometry import PixelMapper
from clawdlet.window_tracker import (
    Win32ClaudeTracker,
    get_physical_work_area,
    is_claude_desktop_window,
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    cfg = TrackerConfig()
    mapper = PixelMapper(dpr=1.0)
    tracker = Win32ClaudeTracker(mapper=mapper, cfg=cfg)

    phys_work = get_physical_work_area()
    print("=== System Display Summary ===")
    print(f"Physical Work Area (SPI_GETWORKAREA): {phys_work}")
    print()

    print("=== Running claude.exe Processes ===")
    proc_count = 0
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        name = proc.info.get("name") or ""
        if name.lower() == cfg.target_exe_name.lower():
            proc_count += 1
            cmd = proc.info.get("cmdline") or []
            ptype = next((c for c in cmd if c.startswith("--type=")), "MAIN/ROOT")
            print(f"  PID={proc.pid:<6} type={ptype:<22} exe={proc.info.get('exe')}")
    if proc_count == 0:
        print("  (No claude.exe processes found)")
    print()

    print("=== EnumWindows Candidates for claude.exe ===")
    candidates = tracker.scan_candidates(only_claude_exe=True)
    if not candidates:
        print(
            "  (No top-level windows enumerated in this session/job context, "
            "or Claude Desktop window is closed.)"
        )
    for info in candidates:
        matched = is_claude_desktop_window(info, cfg)
        status = "MATCH [Claude Desktop GUI]" if matched else "SKIP"
        print(
            f"  [{status}] HWND={hex(info.hwnd)} PID={info.pid} "
            f"class={info.class_name!r} title={info.title!r} "
            f"vis={info.visible} min={info.minimized} cloaked={info.cloaked} "
            f"rect=({info.rect.left:.0f}, {info.rect.top:.0f}, {info.rect.right:.0f}, {info.rect.bottom:.0f})"
        )

    best = tracker.find_best_claude_window()
    print()
    print(f"Selected Target Window: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
