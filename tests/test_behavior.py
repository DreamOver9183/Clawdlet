"""Unit tests for the Clawdlet FSM: M1 physics/perch + M2 autonomous wander, climb, and seek."""

from __future__ import annotations

import pytest

from clawdlet.behavior import ActivityMode, ClawdletController, PetState, WindowSnapshot
from clawdlet.config import PhysicsConfig, TrackerConfig
from clawdlet.geometry import Rect
from clawdlet.physics import PhysicsBody


def _make_controller(
    x: float = 500.0,
    y: float = 200.0,
    claude_seek_weight: float = 0.72,
    wall_climb_chance: float = 0.55,
    rng_seed: int = 42,
) -> tuple[ClawdletController, Rect]:
    work_area = Rect(0.0, 0.0, 1536.0, 816.0)
    body = PhysicsBody(x=x, y=y, width=128.0, height=128.0)
    ctrl = ClawdletController(
        body=body,
        work_area=work_area,
        physics_cfg=PhysicsConfig(
            claude_seek_weight=claude_seek_weight,
            wall_climb_chance=wall_climb_chance,
        ),
        tracker_cfg=TrackerConfig(shake_speed_threshold=1500.0),
        rng_seed=rng_seed,
    )
    return ctrl, work_area


def test_perch_on_claude_and_follow_smooth_window_drag() -> None:
    ctrl, _ = _make_controller()
    win_rect = Rect(300.0, 360.0, 1200.0, 780.0)
    snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=False, visible=True)

    dt = 1.0 / 60.0
    for _ in range(90):
        state = ctrl.tick(dt, snap)
        if state.state == PetState.PERCH_CLAUDE:
            break

    assert ctrl.state == PetState.PERCH_CLAUDE
    assert ctrl.body.bottom == pytest.approx(win_rect.top)
    initial_offset = ctrl.body.x - win_rect.left

    # Slowly move the Claude Desktop window right by 60px over 10 frames (360 px/s < threshold)
    for i in range(1, 11):
        moved_rect = Rect(
            300.0 + i * 6.0,
            360.0 + i * 2.0,
            1200.0 + i * 6.0,
            780.0 + i * 2.0,
        )
        moved_snap = WindowSnapshot(hwnd=0x14084, rect=moved_rect, minimized=False, visible=True)
        frame = ctrl.tick(dt, moved_snap)

    assert frame.state == PetState.PERCH_CLAUDE
    assert ctrl.body.x == pytest.approx(moved_rect.left + initial_offset)
    assert ctrl.body.bottom == pytest.approx(moved_rect.top)


def test_window_minimize_triggers_free_fall_to_taskbar() -> None:
    ctrl, work_area = _make_controller()
    win_rect = Rect(300.0, 360.0, 1200.0, 780.0)
    snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=False, visible=True)

    dt = 1.0 / 60.0
    for _ in range(90):
        ctrl.tick(dt, snap)
        if ctrl.state == PetState.PERCH_CLAUDE:
            break
    assert ctrl.state == PetState.PERCH_CLAUDE

    # Minimize the Claude window
    min_snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=True, visible=False)
    frame = ctrl.tick(dt, min_snap)
    assert frame.state == PetState.FALLING

    # Continue ticking with no window until Clawdlet hits the taskbar floor and settles to IDLE
    saw_squish = False
    for _ in range(180):
        frame = ctrl.tick(dt, None)
        if frame.state == PetState.LAND_SQUISH:
            saw_squish = True
        if frame.state == PetState.IDLE and saw_squish:
            break

    assert saw_squish
    assert ctrl.state == PetState.IDLE
    assert ctrl.body.bottom == pytest.approx(work_area.bottom)


def test_violent_window_shake_throws_clawdlet_into_dizzy() -> None:
    ctrl, _ = _make_controller()
    win_rect = Rect(300.0, 360.0, 1200.0, 780.0)
    snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=False, visible=True)

    dt = 1.0 / 60.0
    for _ in range(90):
        ctrl.tick(dt, snap)
        if ctrl.state == PetState.PERCH_CLAUDE:
            break
    assert ctrl.state == PetState.PERCH_CLAUDE

    # Violently yank window by 80px per frame (~4800 px/s > 1500 px/s threshold)
    for i in range(1, 5):
        shaken_rect = Rect(300.0 + i * 80.0, 360.0, 1200.0 + i * 80.0, 780.0)
        shaken_snap = WindowSnapshot(hwnd=0x14084, rect=shaken_rect, minimized=False, visible=True)
        frame = ctrl.tick(dt, shaken_snap)
        if frame.state == PetState.THROWN:
            break

    assert ctrl.state == PetState.THROWN
    assert ctrl.body.vx > 0.0
    assert ctrl.body.vy < 0.0

    # Let Clawdlet fall to the floor (window closed/out of the way) and enter DIZZY
    saw_dizzy = False
    for _ in range(200):
        frame = ctrl.tick(dt, None)
        if frame.state == PetState.DIZZY:
            saw_dizzy = True
            break

    assert saw_dizzy


def test_mouse_drag_and_throw_velocity_clamping() -> None:
    ctrl, _ = _make_controller()
    dt = 1.0 / 60.0

    ctrl.begin_drag(520.0, 220.0)
    assert ctrl.state == PetState.DRAGGED

    for i in range(1, 6):
        ctrl.tick(dt, None)
        ctrl.update_drag(520.0 + i * 90.0, 220.0 - i * 40.0)

    ctrl.end_drag()
    assert ctrl.state == PetState.THROWN
    speed = (ctrl.body.vx ** 2 + ctrl.body.vy ** 2) ** 0.5
    assert speed <= ctrl.physics_cfg.max_throw_speed + 1e-3
    assert ctrl.body.vx > 0.0
    assert ctrl.body.vy < 0.0


def test_autonomous_seek_and_climb_onto_claude_window() -> None:
    """Milestone 2: From taskbar floor IDLE -> WANDER to Claude side -> CLIMB up -> PERCH_CLAUDE."""
    ctrl, _ = _make_controller(
        x=180.0,
        y=816.0 - 128.0,
        claude_seek_weight=1.0,
        rng_seed=7,
    )
    ctrl.body.grounded = True
    ctrl.body.on_platform_id = "floor"
    ctrl.state = PetState.IDLE

    win_rect = Rect(350.0, 400.0, 1150.0, 780.0)
    snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=False, visible=True)

    dt = 1.0 / 60.0
    saw_wander = False
    saw_climb = False
    saw_perch = False

    for _ in range(600):
        frame = ctrl.tick(dt, snap)
        if frame.state == PetState.WANDER:
            saw_wander = True
        elif frame.state == PetState.CLIMB:
            saw_climb = True
        elif frame.state == PetState.PERCH_CLAUDE:
            saw_perch = True
            break

    assert saw_wander, "Expected Clawdlet to wander toward the Claude window edge"
    assert saw_climb, "Expected Clawdlet to climb vertically up the Claude window side"
    assert saw_perch, "Expected Clawdlet to reach the top and enter PERCH_CLAUDE"
    assert ctrl.body.bottom == pytest.approx(win_rect.top)


def test_screen_wall_climb_and_hop_off() -> None:
    """Milestone 2: Walking into the left screen edge triggers vertical wall climb and hop-off."""
    ctrl, _ = _make_controller(
        x=30.0,
        y=816.0 - 128.0,
        wall_climb_chance=1.0,
        rng_seed=11,
    )
    ctrl.body.grounded = True
    ctrl.body.on_platform_id = "floor"
    ctrl.state = PetState.WANDER
    ctrl.facing = -1  # Walking left toward x=0

    dt = 1.0 / 60.0
    saw_climb = False
    saw_hop_fall = False

    for _ in range(400):
        frame = ctrl.tick(dt, None)
        if frame.state == PetState.CLIMB:
            saw_climb = True
            assert frame.flip_x is False  # Left wall climb uses unflipped sprite
        elif saw_climb and frame.state == PetState.FALLING:
            saw_hop_fall = True
            break

    assert saw_climb
    assert saw_hop_fall
    assert ctrl.body.vx > 0.0  # Hopped away from left wall toward screen interior


def test_perched_autonomous_wander_and_blob_on_titlebar() -> None:
    """Milestone 2: While perched on Claude Desktop, transitions between PERCH_CLAUDE, WANDER, and BLOB."""
    ctrl, _ = _make_controller(rng_seed=3)
    win_rect = Rect(300.0, 360.0, 1200.0, 780.0)
    snap = WindowSnapshot(hwnd=0x14084, rect=win_rect, minimized=False, visible=True)

    dt = 1.0 / 60.0
    for _ in range(90):
        ctrl.tick(dt, snap)
        if ctrl.state == PetState.PERCH_CLAUDE:
            break
    assert ctrl.state == PetState.PERCH_CLAUDE

    visited_states: set[PetState] = {ctrl.state}
    for _ in range(900):
        frame = ctrl.tick(dt, snap)
        visited_states.add(frame.state)
        # Must always remain safely within the horizontal bounds of the Claude window
        assert win_rect.left <= ctrl.body.x <= win_rect.right - ctrl.body.width
        assert ctrl.body.bottom == pytest.approx(win_rect.top)
        if {PetState.PERCH_CLAUDE, PetState.WANDER, PetState.BLOB}.issubset(visited_states):
            break

    assert PetState.WANDER in visited_states
    assert PetState.BLOB in visited_states


def test_claude_thinking_triggers_laptop_typing_state() -> None:
    """When Claude Desktop is thinking (is_thinking=True), Clawdlet pulls out its laptop and types."""
    ctrl, _ = _make_controller(rng_seed=5)
    win_rect = Rect(300.0, 360.0, 1200.0, 780.0)
    snap_idle = WindowSnapshot(
        hwnd=0x14084, rect=win_rect, minimized=False, visible=True, is_thinking=False
    )

    dt = 1.0 / 60.0
    for _ in range(90):
        ctrl.tick(dt, snap_idle)
        if ctrl.state == PetState.PERCH_CLAUDE:
            break
    assert ctrl.state == PetState.PERCH_CLAUDE

    # Claude starts thinking -> Clawdlet immediately enters TYPING ("typing_laptop")
    snap_thinking = WindowSnapshot(
        hwnd=0x14084, rect=win_rect, minimized=False, visible=True, is_thinking=True
    )
    frame = ctrl.tick(dt, snap_thinking)
    assert frame.state == PetState.TYPING
    assert frame.animation_name == "typing_laptop"
    assert frame.flip_x is False

    # Manual typing toggle also works on the floor:
    # Standard mode (ActivityMode.NORMAL) plays PUT_AWAY_LAPTOP when stopping typing
    ctrl.summon_to_center()
    for _ in range(120):
        ctrl.tick(dt, None)
        if ctrl.state == PetState.IDLE:
            break
    assert ctrl.toggle_typing() is True
    assert ctrl.state == PetState.TYPING
    assert ctrl.toggle_typing() is False
    assert ctrl.state == PetState.PUT_AWAY_LAPTOP
    for _ in range(90):
        ctrl.tick(dt, None)
        if ctrl.state == PetState.IDLE:
            break
    assert ctrl.state == PetState.IDLE

    # Hyper mode (ActivityMode.HYPER) plays KICK_LAPTOP and launches a projectile to the left screen edge
    ctrl.set_activity_mode(ActivityMode.HYPER)
    assert ctrl.toggle_typing() is True
    assert ctrl.state == PetState.TYPING
    assert ctrl.toggle_typing() is False
    assert ctrl.state == PetState.KICK_LAPTOP
    saw_spin_proj = False
    saw_wall_hit = False
    for _ in range(160):
        frame = ctrl.tick(dt, None)
        if frame.projectile is not None:
            if frame.projectile.animation_name == "laptop_spin":
                saw_spin_proj = True
            elif frame.projectile.animation_name == "laptop_wall_hit":
                saw_wall_hit = True
                assert frame.projectile.x == round(ctrl.work_area.left)
        if ctrl.state != PetState.KICK_LAPTOP and frame.projectile is None:
            break
    assert saw_spin_proj, "Expected kicked laptop projectile to spin across the screen"
    assert saw_wall_hit, "Expected kicked laptop projectile to hit the left screen edge (work_area.left)"
    assert ctrl.state != PetState.KICK_LAPTOP

    # Toggling typing ON also automatically finishes after ~6s, kicks laptop, and returns to autonomous actions
    assert ctrl.toggle_typing() is True
    assert ctrl.state == PetState.TYPING
    saw_kick = False
    for _ in range(480):  # ~8s at 60Hz (6s typing + 1.28s kick)
        frame = ctrl.tick(dt, None)
        if frame.state == PetState.KICK_LAPTOP:
            saw_kick = True
    assert saw_kick
    assert ctrl.manual_typing is False
    assert ctrl.state not in (PetState.TYPING, PetState.KICK_LAPTOP)


