"""Application entrypoint, 60 FPS simulation loop, and System Tray dispatcher."""

from __future__ import annotations

import argparse
import ctypes
import queue
import signal
import time
import tkinter as tk

from clawdlet.behavior import ClawdletController, WindowSnapshot
from clawdlet.config import ActivityMode, PhysicsConfig, TrackerConfig
from clawdlet.geometry import PixelMapper
from clawdlet.overlay import ClawdletOverlay
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import SpriteSheetBank
from clawdlet.tray import (
    CMD_EXIT,
    CMD_MODE_HYPER,
    CMD_MODE_NORMAL,
    CMD_MODE_QUIET,
    CMD_SCALE_1X,
    CMD_SCALE_2X,
    CMD_SCALE_3X,
    CMD_SUMMON_CENTER,
    CMD_SUMMON_CLAUDE,
    CMD_TOGGLE_CLICK_THROUGH,
    CMD_TOGGLE_DEBUG,
    CMD_TOGGLE_TYPING,
    TrayAction,
    TrayMenuState,
    Win32TrayIcon,
)
from clawdlet.window_tracker import Win32ClaudeTracker, get_physical_work_area


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clawdlet",
        description="Clawdlet — Unofficial physics-based Claude Desktop window companion.",
    )
    parser.add_argument(
        "--scale",
        type=int,
        choices=(1, 2, 3),
        default=2,
        help="Integer pixel-art scaling factor (1=64px, 2=128px, 3=192px; default: 2).",
    )
    parser.add_argument(
        "--mode",
        choices=("quiet", "normal", "hyper"),
        default="normal",
        help="Initial movement activity mode (default: normal).",
    )
    parser.add_argument(
        "--click-through",
        action="store_true",
        help="Start in click-through lock mode (mouse clicks pass through Clawdlet).",
    )
    parser.add_argument(
        "--spawn",
        choices=("above-claude", "center"),
        default="above-claude",
        help="Initial spawn location (default: above-claude if open, else center).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show real-time FSM state and locked HWND overlay banner.",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable the Windows system tray icon.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Optional auto-exit duration in seconds (for automated smoke tests).",
    )
    return parser.parse_args(argv)


def _enable_per_monitor_dpi_awareness() -> None:
    """Enable Per-Monitor DPI V2 so Win32 DWM bounds and Tk geometry share 1:1 pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dispatch_tray_action(
    cmd_id: int,
    controller: ClawdletController,
    overlay: ClawdletOverlay,
    tray_state: TrayMenuState,
    latest_win: WindowSnapshot | None,
) -> bool:
    """Apply a single tray menu command. Return True if application should exit."""
    if cmd_id == CMD_SUMMON_CENTER:
        controller.summon_to_center()
        tray_state.manual_typing = False
    elif cmd_id == CMD_SUMMON_CLAUDE:
        controller.summon_to_claude(latest_win)
        tray_state.manual_typing = False
    elif cmd_id == CMD_TOGGLE_TYPING:
        tray_state.manual_typing = controller.toggle_typing()
    elif cmd_id == CMD_MODE_QUIET:
        controller.set_activity_mode(ActivityMode.QUIET)
        tray_state.activity_mode = ActivityMode.QUIET
    elif cmd_id == CMD_MODE_NORMAL:
        controller.set_activity_mode(ActivityMode.NORMAL)
        tray_state.activity_mode = ActivityMode.NORMAL
    elif cmd_id == CMD_MODE_HYPER:
        controller.set_activity_mode(ActivityMode.HYPER)
        tray_state.activity_mode = ActivityMode.HYPER
    elif cmd_id in (CMD_SCALE_1X, CMD_SCALE_2X, CMD_SCALE_3X):
        new_scale = {CMD_SCALE_1X: 1, CMD_SCALE_2X: 2, CMD_SCALE_3X: 3}[cmd_id]
        overlay.set_scale(new_scale)
        tray_state.scale = new_scale
    elif cmd_id == CMD_TOGGLE_CLICK_THROUGH:
        new_ct = not overlay.click_through
        overlay.set_click_through(new_ct)
        tray_state.click_through = new_ct
    elif cmd_id == CMD_TOGGLE_DEBUG:
        new_dbg = not overlay.debug
        overlay.set_debug(new_dbg)
        tray_state.debug = new_dbg
    elif cmd_id == CMD_EXIT:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _enable_per_monitor_dpi_awareness()

    root = tk.Tk()
    mapper = PixelMapper(dpr=1.0)
    work_area = mapper.to_logical_rect(get_physical_work_area())

    physics_cfg = PhysicsConfig()
    tracker_cfg = TrackerConfig()
    tracker = Win32ClaudeTracker(mapper=mapper, cfg=tracker_cfg)

    sprite_bank = SpriteSheetBank(scale=args.scale)
    pet_w = float(sprite_bank.scaled_width)
    pet_h = float(sprite_bank.scaled_height)

    initial_win = tracker.poll()
    if (
        args.spawn == "above-claude"
        and initial_win is not None
        and initial_win.is_perchable
    ):
        spawn_x = initial_win.rect.clamp_x(
            initial_win.rect.center_x - pet_w * 0.5,
            pet_w,
        )
        spawn_y = max(work_area.top, initial_win.rect.top - pet_h - 140.0)
    else:
        spawn_x = work_area.center_x - pet_w * 0.5
        spawn_y = max(work_area.top + 40.0, work_area.center_y - 200.0)

    initial_mode = ActivityMode(args.mode)
    body = PhysicsBody(x=spawn_x, y=spawn_y, width=pet_w, height=pet_h)
    controller = ClawdletController(
        body=body,
        work_area=work_area,
        physics_cfg=physics_cfg,
        tracker_cfg=tracker_cfg,
        activity_mode=initial_mode,
    )
    overlay = ClawdletOverlay(
        root=root,
        controller=controller,
        sprite_bank=sprite_bank,
        debug=args.debug,
        click_through=args.click_through,
    )
    if args.click_through:
        overlay.set_click_through(True)

    tray_queue: queue.Queue[TrayAction] = queue.Queue()
    tray_state = TrayMenuState(
        activity_mode=initial_mode,
        scale=args.scale,
        click_through=args.click_through,
        debug=args.debug,
    )
    tray_icon: Win32TrayIcon | None = None
    if not args.no_tray:
        icon_path = sprite_bank.assets_dir / "clawdlet.ico"
        tray_icon = Win32TrayIcon(
            icon_path=icon_path,
            state=tray_state,
            action_queue=tray_queue,
        )
        tray_icon.start()

    if args.debug:
        print(
            f"[Clawdlet] work_area={work_area}, mode={initial_mode.value}, "
            f"scale={args.scale}x, initial_claude_window={initial_win}"
        )

    running = True

    def _stop(*_: object) -> None:
        nonlocal running
        if not running:
            return
        running = False
        if tray_icon is not None:
            tray_icon.stop()
        try:
            root.quit()
            root.destroy()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _stop)

    fixed_dt = physics_cfg.dt
    start_time = time.monotonic()
    last_time = start_time
    accumulator = 0.0
    last_logged_state = None
    work_area_refresh_counter = 0

    def _on_tick() -> None:
        nonlocal last_time, accumulator, last_logged_state, work_area_refresh_counter
        if not running:
            return

        now = time.monotonic()
        if args.duration > 0.0 and (now - start_time) >= args.duration:
            _stop()
            return

        frame_dt = min(0.10, max(0.0, now - last_time))
        last_time = now
        accumulator += frame_dt

        work_area_refresh_counter += 1
        if work_area_refresh_counter >= 60:
            work_area_refresh_counter = 0
            controller.set_work_area(mapper.to_logical_rect(get_physical_work_area()))

        win_snap = tracker.poll(now)

        # Process any pending System Tray commands
        while True:
            try:
                action = tray_queue.get_nowait()
            except queue.Empty:
                break
            should_exit = dispatch_tray_action(
                cmd_id=action.command_id,
                controller=controller,
                overlay=overlay,
                tray_state=tray_state,
                latest_win=win_snap,
            )
            if should_exit:
                _stop()
                return

        render_state = None
        while accumulator >= fixed_dt:
            render_state = controller.tick(fixed_dt, win_snap)
            accumulator -= fixed_dt

        if render_state is None:
            render_state = controller.tick(fixed_dt, win_snap)

        overlay.sync_frame(render_state, frame_dt)

        if overlay.debug and render_state.state != last_logged_state:
            last_logged_state = render_state.state
            print(
                f"[Clawdlet] state={render_state.state.name} "
                f"pos=({render_state.x}, {render_state.y}) "
                f"locked_hwnd={hex(render_state.locked_hwnd) if render_state.locked_hwnd else None}"
            )

        root.after(12, _on_tick)

    root.after(12, _on_tick)
    try:
        root.mainloop()
    finally:
        if tray_icon is not None:
            tray_icon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
