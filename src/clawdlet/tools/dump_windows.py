"""Diagnostic CLI to inspect Claude processes and top-level Win32 windows."""

from __future__ import annotations

from collections.abc import Sequence
import ctypes
import sys

import psutil

from clawdlet.config import TrackerConfig
from clawdlet.geometry import PixelMapper
from clawdlet.window_tracker import (
    ROLE_MAIN,
    Win32ClaudeTracker,
    classify_child_process,
    get_physical_work_area,
    is_claude_desktop_window,
    window_owner_pid,
)


def sampling_role(
    pid: int,
    ppid: int | None,
    cmdline: Sequence[str] | None,
    main_pid: int | None,
    cfg: TrackerConfig,
) -> str:
    """Role the thinking heuristic gives a claude.exe process: main / network / skipped:<reason>."""
    if main_pid is None:
        return "skipped:no-window"
    if pid == main_pid:
        return ROLE_MAIN
    if ppid != main_pid:
        return "skipped:outside-tree"
    if cmdline is None:
        return "skipped:cmdline-denied"
    role = classify_child_process(cmdline, cfg.thinking_utility_subtypes)
    if role is not None:
        return role
    ptype = next((c[len("--type="):] for c in cmdline if c.startswith("--type=")), "no-type")
    subtype = next((c.split("=", 1)[1] for c in cmdline if c.startswith("--utility-sub-type=")), "")
    return f"skipped:{ptype}" + (f"({subtype})" if subtype else "")


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

    best = tracker.find_best_claude_window()
    main_pid = window_owner_pid(best.hwnd) if best is not None else None
    print("=== Running claude.exe Processes ===")
    print(f"  Claude window owner PID: {main_pid}")
    proc_count = 0
    for proc in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline"]):
        name = proc.info.get("name") or ""
        if name.lower() == cfg.target_exe_name.lower():
            proc_count += 1
            raw_cmd = proc.info.get("cmdline")  # None when access was denied
            cmd = tuple(raw_cmd) if raw_cmd is not None else None
            ptype = next((c for c in cmd or () if c.startswith("--type=")), "MAIN/ROOT")
            role = sampling_role(proc.pid, proc.info.get("ppid"), cmd, main_pid, cfg)
            print(
                f"  PID={proc.pid:<6} type={ptype:<22} role={role:<32} exe={proc.info.get('exe')}"
            )
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

    print()
    print(f"Selected Target Window: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
