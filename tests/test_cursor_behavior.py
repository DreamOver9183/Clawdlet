"""Milestone 5 step A: cursor awareness — gaze, chase, and Hyper-mode pounce."""

from __future__ import annotations

from dataclasses import replace

import pytest

from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.config import ACTIVITY_PROFILES, ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)


def _make(mode: ActivityMode = ActivityMode.NORMAL, x: float = 500.0) -> ClawdletController:
    body = PhysicsBody(x=x, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=7)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 999.0
    return ctrl


@pytest.fixture
def always_chase(monkeypatch: pytest.MonkeyPatch) -> None:
    for mode in ActivityMode:
        monkeypatch.setitem(ACTIVITY_PROFILES, mode, replace(ACTIVITY_PROFILES[mode], chase_chance=1.0))


def _eye_level(ctrl: ClawdletController) -> float:
    return ctrl.body.y + ctrl.body.height * 0.36


def test_gaze_follows_cursor_direction_and_radius() -> None:
    ctrl = _make()
    eye_y = _eye_level(ctrl)
    frame = ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x + 500.0, eye_y))
    assert frame.state == PetState.IDLE
    assert frame.animation_name == "gaze"
    assert frame.frame_index == 5  # look right
    assert frame.flip_x is False

    frame = ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x - 300.0, eye_y - 300.0))
    assert frame.frame_index == 0  # look up-left

    frame = ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x, eye_y - 400.0))
    assert frame.frame_index == 1  # straight up

    frame = ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x + 700.0, eye_y))
    assert frame.animation_name == "idle_breathe"
    assert frame.frame_index is None


def test_gaze_blinks_occasionally() -> None:
    ctrl = _make()
    target = PointerSnapshot(ctrl.body.center_x + 400.0, _eye_level(ctrl))
    indices = [ctrl.tick(DT, None, target).frame_index for _ in range(6 * 60)]
    assert 9 in indices
    assert indices.count(9) < 30


def test_perched_gaze_yields_to_look_down_when_cursor_is_inside_window() -> None:
    ctrl = _make()
    snap = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
    ctrl.body.x = 600.0
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = snap.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    inside = ctrl.tick(DT, snap, PointerSnapshot(700.0, WIN_RECT.top + 200.0))
    assert inside.animation_name == "perch_look_down"
    above = ctrl.tick(DT, snap, PointerSnapshot(900.0, WIN_RECT.top - 60.0))
    assert above.animation_name == "gaze"


def test_floor_chase_walks_to_cursor_then_rests(always_chase: None) -> None:
    ctrl = _make()
    ctrl._decision_duration = 0.0
    cursor = PointerSnapshot(ctrl.body.center_x + 400.0, 300.0)
    ctrl.tick(DT, None, cursor)
    assert ctrl.state == PetState.CHASE_CURSOR
    start_x = ctrl.body.x
    xs = []
    for _ in range(int(3.8 / DT)):
        frame = ctrl.tick(DT, None, cursor)
        xs.append(frame.x)
        if ctrl.state != PetState.CHASE_CURSOR:
            break
    assert ctrl.state == PetState.IDLE
    assert xs[-1] > start_x + 300.0
    assert abs(ctrl.body.center_x - cursor.x) < ctrl.body.width * 0.3


def test_chase_ignores_cursor_on_taskbar_and_far_away(always_chase: None) -> None:
    ctrl = _make()
    ctrl._decision_duration = 0.0
    ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x + 300.0, WORK_AREA.bottom + 20.0))
    assert ctrl.state != PetState.CHASE_CURSOR
    ctrl = _make()
    ctrl._decision_duration = 0.0
    ctrl.tick(DT, None, PointerSnapshot(ctrl.body.center_x + 900.0, 300.0))
    assert ctrl.state != PetState.CHASE_CURSOR


def test_perched_chase_stays_within_window_width(always_chase: None) -> None:
    ctrl = _make()
    snap = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
    ctrl.body.x = 700.0
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = snap.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    ctrl._decision_duration = 0.0
    cursor = PointerSnapshot(WIN_RECT.right - 10.0, 200.0)
    ctrl.tick(DT, snap, cursor)
    assert ctrl.state == PetState.CHASE_CURSOR
    for _ in range(int(4.5 / DT)):
        ctrl.tick(DT, snap, cursor)
        assert WIN_RECT.left <= ctrl.body.x <= WIN_RECT.right - ctrl.body.width
        assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)
    assert ctrl.state != PetState.CHASE_CURSOR
    assert ctrl.body.x == pytest.approx(WIN_RECT.right - ctrl.body.width)


def test_hyper_pounce_leaps_at_resting_cursor_without_dizzy() -> None:
    ctrl = _make(ActivityMode.HYPER)
    cursor = PointerSnapshot(ctrl.body.center_x + 60.0, ctrl.body.y - 150.0, idle_s=1.3)
    states = []
    min_vy = 0.0
    for _ in range(int(3.0 / DT)):
        frame = ctrl.tick(DT, None, cursor)
        states.append(frame.state)
        min_vy = min(min_vy, frame.vy)
    assert PetState.POUNCE in states
    pounce_at = states.index(PetState.POUNCE)
    assert pounce_at * DT >= ctrl.interaction_cfg.pounce_still_s - DT
    assert min_vy < -800.0
    assert PetState.LAND_SQUISH in states[pounce_at:]
    assert PetState.DIZZY not in states


def test_pounce_requires_hyper_and_respects_cooldown() -> None:
    normal = _make(ActivityMode.NORMAL)
    cursor = PointerSnapshot(normal.body.center_x, normal.body.y - 150.0, idle_s=1.3)
    states = {normal.tick(DT, None, cursor).state for _ in range(int(3.0 / DT))}
    assert PetState.POUNCE not in states

    hyper = _make(ActivityMode.HYPER)
    cursor = PointerSnapshot(hyper.body.center_x, hyper.body.y - 150.0, idle_s=1.3)
    pounces = 0
    prev = None
    for _ in range(int(6.0 / DT)):
        state = hyper.tick(DT, None, cursor).state
        if state == PetState.POUNCE and prev != PetState.POUNCE:
            pounces += 1
        prev = state
    assert pounces == 1  # 8 s cooldown
