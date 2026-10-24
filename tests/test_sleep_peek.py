"""Milestone 5 step C: sleep schedule (yawn -> sleep -> startled wake) and edge-peek mode."""

from __future__ import annotations

import pytest

from clawdlet.app import dispatch_tray_action
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody
from clawdlet.tray import CMD_TOGGLE_PEEK, TrayMenuState

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
FAR = (1400.0, 50.0)  # Cursor parked far away so gaze/chase never interfere


def _floor(x: float = 500.0) -> ClawdletController:
    body = PhysicsBody(x=x, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, rng_seed=11)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 999.0
    return ctrl


def _idle(seconds: float) -> PointerSnapshot:
    return PointerSnapshot(FAR[0], FAR[1], idle_s=seconds)


def _run(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None, pointer: PointerSnapshot | None):
    return [ctrl.tick(DT, win, pointer).state for _ in range(int(round(seconds / DT)))]


def test_user_idle_dozes_off_then_input_startles_awake() -> None:
    ctrl = _floor()
    assert _run(ctrl, 0.5, None, _idle(60.0))[-1] == PetState.IDLE  # not idle long enough yet

    states = _run(ctrl, ClawdletController.YAWN_DURATION_S + 0.1, None, _idle(95.0))
    assert states[0] == PetState.YAWN
    assert states[-1] == PetState.SLEEP
    assert _run(ctrl, 5.0, None, _idle(100.0))[-1] == PetState.SLEEP

    states = _run(ctrl, ClawdletController.WAKE_DURATION_S + 0.1, None, _idle(0.2))
    assert states[0] == PetState.WAKE_STARTLE
    assert states[-1] == PetState.IDLE


def test_input_during_yawn_cancels_sleep() -> None:
    ctrl = _floor()
    ctrl.tick(DT, None, _idle(95.0))
    assert ctrl.state == PetState.YAWN
    ctrl.tick(DT, None, _idle(0.1))
    assert ctrl.state == PetState.IDLE


def test_no_pointer_sample_never_sleeps_or_wakes() -> None:
    ctrl = _floor()
    assert PetState.YAWN not in _run(ctrl, 1.0, None, None)
    ctrl._set_state(PetState.SLEEP)
    assert _run(ctrl, 1.0, None, None)[-1] == PetState.SLEEP  # e.g. secure desktop / lock screen


def test_claude_thinking_wakes_straight_into_typing() -> None:
    ctrl = _floor()
    ctrl._set_state(PetState.SLEEP)
    thinking = WindowSnapshot(hwnd=0x1, rect=Rect(1300.0, 100.0, 1500.0, 300.0), is_thinking=True)
    assert _run(ctrl, 0.2, thinking, _idle(200.0))[-1] == PetState.TYPING


def test_poke_wakes_a_sleeping_pet() -> None:
    ctrl = _floor()
    ctrl._set_state(PetState.SLEEP)
    assert ctrl.poke() is True
    assert ctrl.state == PetState.WAKE_STARTLE


def test_sleeping_on_claude_window_follows_slow_drag() -> None:
    ctrl = _floor()
    ctrl.body.x = 600.0
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = SNAP.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    _run(ctrl, ClawdletController.YAWN_DURATION_S + 0.1, SNAP, _idle(95.0))
    assert ctrl.state == PetState.SLEEP
    offset = ctrl.body.x - WIN_RECT.left
    for i in range(1, 40):
        moved = Rect(WIN_RECT.left + i * 4.0, WIN_RECT.top, WIN_RECT.right + i * 4.0, WIN_RECT.bottom)
        ctrl.tick(DT, WindowSnapshot(hwnd=SNAP.hwnd, rect=moved), _idle(100.0))
        assert ctrl.state == PetState.SLEEP
        assert ctrl.body.bottom == pytest.approx(moved.top)
        assert ctrl.body.x - moved.left == pytest.approx(offset)


@pytest.mark.parametrize(("start_x", "side"), [(300.0, -1), (1100.0, 1)])
def test_peek_mode_walks_to_nearest_edge_and_hides(start_x: float, side: int) -> None:
    ctrl = _floor(start_x)
    ctrl.set_peek_mode(True)
    ctrl._decision_duration = 0.0
    states = _run(ctrl, 15.0, None, _idle(5.0))
    assert PetState.CLIMB not in states
    assert ctrl.state == PetState.PEEK_EDGE
    expected_x = WORK_AREA.left if side < 0 else WORK_AREA.right - 128.0
    assert ctrl.body.x == pytest.approx(expected_x)
    assert WORK_AREA.left <= ctrl.body.x and ctrl.body.right <= WORK_AREA.right
    frame = ctrl.tick(DT, None, _idle(5.0))
    assert frame.animation_name == "peek_edge"
    assert frame.flip_x is (side > 0)

    # Cursor close by: lean out and wave; still in PEEK_EDGE
    near = PointerSnapshot(ctrl.body.center_x, ctrl.body.y - 60.0, idle_s=0.1)
    frame = ctrl.tick(DT, None, near)
    assert frame.state == PetState.PEEK_EDGE
    assert frame.animation_name == "peek_out"


def test_peek_ignores_claude_thinking_and_sleep() -> None:
    ctrl = _floor(20.0)
    ctrl.body.x = WORK_AREA.left
    ctrl.set_peek_mode(True)
    ctrl._head_to_peek_edge()
    assert ctrl.state == PetState.PEEK_EDGE
    thinking = WindowSnapshot(hwnd=0x1, rect=Rect(1300.0, 100.0, 1500.0, 300.0), is_thinking=True)
    assert set(_run(ctrl, 2.0, thinking, _idle(200.0))) == {PetState.PEEK_EDGE}


def test_disabling_peek_pops_back_out() -> None:
    ctrl = _floor()
    ctrl.body.x = WORK_AREA.left
    ctrl.set_peek_mode(True)
    ctrl._head_to_peek_edge()
    ctrl.set_peek_mode(False)
    assert ctrl.state == PetState.IDLE


def test_peek_mode_hops_off_claude_window_instead_of_perching() -> None:
    ctrl = _floor()
    ctrl.body.x = 600.0
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = SNAP.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    ctrl.set_peek_mode(True)
    ctrl._decision_duration = 0.0
    states = _run(ctrl, 20.0, SNAP, _idle(5.0))
    assert PetState.FALLING in states
    assert PetState.CLIMB not in states
    assert ctrl.state == PetState.PEEK_EDGE
    assert ctrl.body.bottom == pytest.approx(WORK_AREA.bottom)


def test_tray_toggle_peek_mode() -> None:
    ctrl = _floor()
    tray = TrayMenuState()
    dispatch_tray_action(CMD_TOGGLE_PEEK, ctrl, overlay=None, tray_state=tray, latest_win=None)  # type: ignore[arg-type]
    assert ctrl.peek_mode is True and tray.peek_mode is True
    dispatch_tray_action(CMD_TOGGLE_PEEK, ctrl, overlay=None, tray_state=tray, latest_win=None)  # type: ignore[arg-type]
    assert ctrl.peek_mode is False and tray.peek_mode is False
