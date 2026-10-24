"""Application entrypoint, 60 FPS simulation loop, and System Tray dispatcher."""

from __future__ import annotations

import argparse
import ctypes
import queue
import sys
import signal
import time
import tkinter as tk

from clawdlet.agent_events import (
    DEFAULT_HOOK_PORT,
    AgentEvent,
    HookServer,
    format_hook_settings,
    load_or_create_token,
)
from clawdlet.behavior import ClawdletController, WindowSnapshot, drop_y_above_window
from clawdlet.config import ActivityMode, PhysicsConfig, TrackerConfig
from clawdlet.geometry import PixelMapper
from clawdlet.gestures import Gesture
from clawdlet.input_state import read_pointer_snapshot
from clawdlet.overlay import ClawdletOverlay
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import SpriteSheetBank
from clawdlet.tray import (
    CMD_COPY_HOOK_CONFIG,
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
    CMD_TOGGLE_PEEK,
    CMD_TOGGLE_TYPING,
    CMD_TOGGLE_WINDOW_PUSH,
    TrayAction,
    TrayMenuState,
    Win32TrayIcon,
    copy_text_to_clipboard,
)
from clawdlet.window_tracker import Win32ClaudeTracker, get_physical_work_area, nudge_window


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
        "--allow-window-push",
        action="store_true",
        help="Let Hyper mode occasionally nudge the Claude window a few pixels (off by default).",
    )
    parser.add_argument(
        "--hook-port",
        type=int,
        default=DEFAULT_HOOK_PORT,
        help=f"Localhost port for Claude Code http hooks (default: {DEFAULT_HOOK_PORT}).",
    )
    parser.add_argument(
        "--no-hooks",
        action="store_true",
        help="Do not start the Claude Code hook receiver.",
    )
    parser.add_argument(
        "--print-hook-config",
        action="store_true",
        help="Print the hooks block for ~/.claude/settings.json and exit.",
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


def sync_tray_state(tray_state: TrayMenuState, controller: ClawdletController) -> None:
    """Mirror controller-owned toggles (changed by double-click, hop, drag, auto put-away) into the tray menu."""
    tray_state.manual_typing = controller.manual_typing


def dispatch_gesture(gesture: Gesture, controller: ClawdletController) -> None:
    """Route a settled overlay mouse gesture to the matching controller reaction."""
    if gesture == Gesture.POKE:
        controller.poke()
    elif gesture == Gesture.DOUBLE:
        controller.toggle_typing()
    elif gesture == Gesture.COMBO:
        controller.flail()
    elif gesture == Gesture.PET:
        controller.pet()


def dispatch_tray_action(
    cmd_id: int,
    controller: ClawdletController,
    overlay: ClawdletOverlay,
    tray_state: TrayMenuState,
    latest_win: WindowSnapshot | None,
    hook_config: str | None = None,
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
    elif cmd_id == CMD_TOGGLE_WINDOW_PUSH:
        controller.push_window_enabled = not controller.push_window_enabled
        tray_state.window_push = controller.push_window_enabled
    elif cmd_id == CMD_TOGGLE_PEEK:
        controller.set_peek_mode(not controller.peek_mode)
        tray_state.peek_mode = controller.peek_mode
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
    elif cmd_id == CMD_COPY_HOOK_CONFIG:
        if hook_config is not None and copy_text_to_clipboard(hook_config):
            print("[Clawdlet] Claude Code hook settings copied to the clipboard.")
    elif cmd_id == CMD_EXIT:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.print_hook_config:
        print(format_hook_settings(args.hook_port, load_or_create_token()))
        return 0
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
        spawn_y = drop_y_above_window(initial_win.rect, pet_h, work_area, 140.0)
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
    controller.window_nudger = nudge_window
    controller.push_window_enabled = args.allow_window_push
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
        window_push=args.allow_window_push,
    )
    hook_queue: queue.Queue[tuple[AgentEvent, str]] = queue.Queue()
    hook_server: HookServer | None = None
    hook_config: str | None = None
    if not args.no_hooks:
        token = load_or_create_token()
        hook_server = HookServer(token=token, events=hook_queue, port=args.hook_port)
        if hook_server.start():
            hook_config = format_hook_settings(int(hook_server.port or args.hook_port), token)
            tray_state.hooks_status = f"Claude Code Hooks：監聽中 127.0.0.1:{hook_server.port}"
        else:
            print(
                f"[Clawdlet] Port {args.hook_port} is busy; Claude Code hooks disabled.",
                file=sys.stderr,
            )
            hook_server = None

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
        if hook_server is not None:
            hook_server.stop()
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
    last_sample_log_time = start_time
    work_area_refresh_counter = 0

    def _on_tick() -> None:
        nonlocal last_time, accumulator, last_logged_state, last_sample_log_time
        nonlocal work_area_refresh_counter
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
        pointer = read_pointer_snapshot()

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
                hook_config=hook_config,
            )
            if should_exit:
                _stop()
                return

        for gesture in overlay.poll_gestures(now):
            dispatch_gesture(gesture, controller)

        while True:
            try:
                agent_event, session_id = hook_queue.get_nowait()
            except queue.Empty:
                break
            if overlay.debug:
                print(f"[Clawdlet] hook event={agent_event.value}")
            controller.handle_agent_event(agent_event, session_id)

        render_state = None
        while accumulator >= fixed_dt:
            render_state = controller.tick(fixed_dt, win_snap, pointer)
            accumulator -= fixed_dt

        if render_state is None:
            render_state = controller.tick(fixed_dt, win_snap, pointer)

        sync_tray_state(tray_state, controller)
        overlay.sync_frame(render_state, frame_dt)

        if overlay.debug and render_state.state != last_logged_state:
            last_logged_state = render_state.state
            print(
                f"[Clawdlet] state={render_state.state.name} "
                f"pos=({render_state.x}, {render_state.y}) "
                f"locked_hwnd={hex(render_state.locked_hwnd) if render_state.locked_hwnd else None}"
            )

        sample = tracker.last_thinking_sample
        if overlay.debug and sample is not None and (now - last_sample_log_time) >= 2.0:
            last_sample_log_time = now
            print(
                f"[Clawdlet] thinking-sample procs={sample.proc_count} "
                f"cpu={sample.cpu_rate:.2f} io={sample.io_rate / 1024.0:.1f}KB/s "
                f"active={sample.active_samples} thinking={sample.is_thinking}"
            )

        root.after(12, _on_tick)

    root.after(12, _on_tick)
    try:
        root.mainloop()
    finally:
        if tray_icon is not None:
            tray_icon.stop()
        if hook_server is not None:
            hook_server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
