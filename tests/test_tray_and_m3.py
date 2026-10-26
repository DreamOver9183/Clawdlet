"""Unit and integration tests for Milestone 3: System Tray, ActivityMode, Scale, Click-through."""

from __future__ import annotations

from pathlib import Path
import queue
import tkinter as tk
import pytest
import win32con
import win32gui

from clawdlet.app import dispatch_tray_action, sync_tray_state
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.overlay import ClawdletOverlay
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import SpriteSheetBank
from clawdlet.tools.gen_sprites import generate_all_sprites
from clawdlet.tray import (
    CMD_EXIT,
    CMD_MODE_HYPER,
    CMD_MODE_NORMAL,
    CMD_MODE_QUIET,
    CMD_SCALE_1X,
    CMD_SCALE_3X,
    CMD_SUMMON_CENTER,
    CMD_SUMMON_CLAUDE,
    CMD_TOGGLE_CLICK_THROUGH,
    CMD_TOGGLE_DEBUG,
    TrayAction,
    TrayMenuState,
    Win32TrayIcon,
)


def test_ico_generated_and_activity_modes_scale_speeds(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    ico_path = tmp_path / "clawdlet.ico"
    assert ico_path.exists()
    assert ico_path.stat().st_size > 100

    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    ctrl = ClawdletController(
        body=PhysicsBody(x=500.0, y=500.0, width=128.0, height=128.0),
        work_area=work_area,
    )
    normal_walk = ctrl.effective_walk_speed

    ctrl.set_activity_mode(ActivityMode.QUIET)
    assert ctrl.effective_walk_speed < normal_walk
    assert ctrl.effective_wall_climb_chance == 0.0

    ctrl.set_activity_mode(ActivityMode.HYPER)
    assert ctrl.effective_walk_speed > normal_walk
    assert ctrl.effective_wall_climb_chance > 0.80


def test_resize_body_preserves_feet_anchor() -> None:
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    body = PhysicsBody(x=600.0, y=400.0 - 128.0, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=work_area)

    old_center_x = ctrl.body.center_x
    old_bottom = ctrl.body.bottom

    # Resize to 3x (192x192)
    ctrl.resize_body(192.0, 192.0)
    assert ctrl.body.width == 192.0
    assert ctrl.body.height == 192.0
    assert ctrl.body.center_x == pytest.approx(old_center_x)
    assert ctrl.body.bottom == pytest.approx(old_bottom)

    # Resize to 1x (64x64)
    ctrl.resize_body(64.0, 64.0)
    assert ctrl.body.center_x == pytest.approx(old_center_x)
    assert ctrl.body.bottom == pytest.approx(old_bottom)


def test_tray_dispatcher_and_click_through_exstyle(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    bank = SpriteSheetBank(assets_dir=tmp_path, scale=2)

    root = tk.Tk()
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    body = PhysicsBody(x=400.0, y=300.0, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=work_area)
    overlay = ClawdletOverlay(root=root, controller=ctrl, sprite_bank=bank)
    tray_state = TrayMenuState()
    win_snap = WindowSnapshot(hwnd=0x14084, rect=Rect(300.0, 350.0, 1200.0, 800.0))

    # 1. Activity mode switches
    dispatch_tray_action(CMD_MODE_QUIET, ctrl, overlay, tray_state, win_snap)
    assert ctrl.activity_mode == ActivityMode.QUIET
    assert tray_state.activity_mode == ActivityMode.QUIET

    dispatch_tray_action(CMD_MODE_HYPER, ctrl, overlay, tray_state, win_snap)
    assert ctrl.activity_mode == ActivityMode.HYPER

    dispatch_tray_action(CMD_MODE_NORMAL, ctrl, overlay, tray_state, win_snap)
    assert ctrl.activity_mode == ActivityMode.NORMAL

    # 2. Dynamic scaling (1x -> 64px, 3x -> 192px)
    dispatch_tray_action(CMD_SCALE_1X, ctrl, overlay, tray_state, win_snap)
    assert overlay.scale == 1
    assert ctrl.body.width == 64.0
    assert tray_state.scale == 1

    dispatch_tray_action(CMD_SCALE_3X, ctrl, overlay, tray_state, win_snap)
    assert overlay.scale == 3
    assert ctrl.body.width == 192.0
    assert tray_state.scale == 3

    # 3. Click-through lock mode toggles WS_EX_TRANSPARENT on outer TkTopLevel HWND,
    # while inner TkChild HWND never has WS_EX_LAYERED set (preventing black-box bug)
    child_hwnd = int(root.winfo_id())
    top_hwnd = overlay.toplevel_hwnd
    assert child_hwnd != top_hwnd
    child_ex = win32gui.GetWindowLong(child_hwnd, win32con.GWL_EXSTYLE)
    assert bool(child_ex & win32con.WS_EX_LAYERED) is False

    dispatch_tray_action(CMD_TOGGLE_CLICK_THROUGH, ctrl, overlay, tray_state, win_snap)
    assert overlay.click_through is True
    assert tray_state.click_through is True
    ex_style_locked = win32gui.GetWindowLong(top_hwnd, win32con.GWL_EXSTYLE)
    assert bool(ex_style_locked & win32con.WS_EX_TRANSPARENT) is True
    cr_key, alpha, flags = win32gui.GetLayeredWindowAttributes(top_hwnd)
    assert cr_key == (1 | (2 << 8) | (3 << 16))
    assert alpha == 255
    assert bool(flags & win32con.LWA_COLORKEY) is True

    dispatch_tray_action(CMD_TOGGLE_CLICK_THROUGH, ctrl, overlay, tray_state, win_snap)
    assert overlay.click_through is False
    ex_style_unlocked = win32gui.GetWindowLong(top_hwnd, win32con.GWL_EXSTYLE)
    assert bool(ex_style_unlocked & win32con.WS_EX_TRANSPARENT) is False
    _, alpha_unlocked, flags_unlocked = win32gui.GetLayeredWindowAttributes(top_hwnd)
    assert alpha_unlocked == 255
    assert bool(flags_unlocked & win32con.LWA_COLORKEY) is True

    # 4. Debug HUD toggle & Summon commands
    dispatch_tray_action(CMD_TOGGLE_DEBUG, ctrl, overlay, tray_state, win_snap)
    assert overlay.debug is True

    dispatch_tray_action(CMD_SUMMON_CLAUDE, ctrl, overlay, tray_state, win_snap)
    assert ctrl.state == PetState.FALLING
    assert ctrl.body.center_x == pytest.approx(win_snap.rect.center_x)

    dispatch_tray_action(CMD_SUMMON_CENTER, ctrl, overlay, tray_state, win_snap)
    assert ctrl.state in (PetState.FALLING, PetState.ENTER_WALK)  # A random entrance, never onto Claude

    # 5. Exit command returns True
    should_exit = dispatch_tray_action(CMD_EXIT, ctrl, overlay, tray_state, win_snap)
    assert should_exit is True

    root.destroy()


def test_win32_tray_icon_thread_lifecycle(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    q: queue.Queue[TrayAction] = queue.Queue()
    state = TrayMenuState()
    tray = Win32TrayIcon(icon_path=tmp_path / "clawdlet.ico", state=state, action_queue=q)
    tray.start()
    assert tray._hwnd is not None
    tray.stop()


def test_sync_tray_state_mirrors_controller_manual_typing() -> None:
    body = PhysicsBody(x=500.0, y=688.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=Rect(0.0, 0.0, 1536.0, 816.0))
    tray_state = TrayMenuState()

    ctrl.toggle_typing()  # e.g. double-click on the pet, not via the tray menu
    sync_tray_state(tray_state, ctrl)
    assert tray_state.manual_typing is True

    ctrl.hop()  # right-click hop cancels typing
    sync_tray_state(tray_state, ctrl)
    assert tray_state.manual_typing is False
