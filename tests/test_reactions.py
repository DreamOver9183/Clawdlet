"""Milestone 5 step B: poke / pet / flail reactions on the floor and on Claude Desktop's top border."""

from __future__ import annotations

import pytest

from clawdlet.app import dispatch_gesture
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.geometry import Rect
from clawdlet.gestures import Gesture
from clawdlet.physics import PhysicsBody

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)


def _floor() -> ClawdletController:
    body = PhysicsBody(x=500.0, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, rng_seed=3)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 999.0
    return ctrl


def _perched() -> ClawdletController:
    ctrl = _floor()
    ctrl.body.x = 600.0
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = SNAP.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    return ctrl


def _run(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None) -> list[PetState]:
    return [ctrl.tick(DT, win).state for _ in range(int(seconds / DT))]


@pytest.mark.parametrize(
    ("trigger", "state", "duration"),
    [
        ("poke", PetState.POKED, ClawdletController.POKED_DURATION_S),
        ("flail", PetState.FLAIL, ClawdletController.FLAIL_DURATION_S),
        ("pet", PetState.PETTED, ClawdletController.PET_LINGER_S),
    ],
)
def test_reactions_play_on_floor_then_return_to_idle(trigger: str, state: PetState, duration: float) -> None:
    ctrl = _floor()
    start_x = ctrl.body.x
    assert getattr(ctrl, trigger)() is True
    states = _run(ctrl, duration + 0.2, None)
    assert states[0] == state
    assert states.count(state) == pytest.approx(duration / DT, abs=2)
    assert ctrl.state == PetState.IDLE
    assert ctrl.body.x == pytest.approx(start_x)
    assert ctrl.body.bottom == pytest.approx(WORK_AREA.bottom)


@pytest.mark.parametrize("trigger", ["poke", "flail", "pet"])
def test_reactions_stay_pinned_to_moving_claude_window(trigger: str) -> None:
    ctrl = _perched()
    offset = ctrl.body.x - WIN_RECT.left
    assert getattr(ctrl, trigger)() is True
    for i in range(1, 30):
        moved = Rect(WIN_RECT.left + i * 3.0, WIN_RECT.top + i, WIN_RECT.right + i * 3.0, WIN_RECT.bottom)
        ctrl.tick(DT, WindowSnapshot(hwnd=SNAP.hwnd, rect=moved))
        assert ctrl.body.bottom == pytest.approx(moved.top)
        assert ctrl.body.x - moved.left == pytest.approx(offset)
    _run(ctrl, 2.0, WindowSnapshot(hwnd=SNAP.hwnd, rect=moved))
    assert ctrl.state in (PetState.PERCH_CLAUDE, PetState.BLOB, PetState.WANDER, PetState.CHASE_CURSOR)


def test_stroking_refreshes_petting() -> None:
    ctrl = _floor()
    ctrl.pet()
    for _ in range(4):
        _run(ctrl, 0.6, None)
        assert ctrl.pet() is True
    assert ctrl.state == PetState.PETTED


def test_priorities_between_reactions() -> None:
    ctrl = _floor()
    ctrl.pet()
    assert ctrl.poke() is False  # poke does not interrupt petting
    assert ctrl.flail() is True  # tantrum beats everything
    assert ctrl.pet() is False
    assert ctrl.poke() is False


def test_typing_glances_up_on_poke_but_ignores_tantrums_and_petting() -> None:
    ctrl = _floor()
    ctrl.toggle_typing()
    assert ctrl.state == PetState.TYPING
    assert ctrl.flail() is False
    assert ctrl.pet() is False
    assert ctrl.state == PetState.TYPING
    assert ctrl.poke() is True  # A single click only makes Clawd look up from the laptop
    assert ctrl.state == PetState.WORK_GLANCE
    assert ctrl.flail() is False
    assert ctrl.pet() is False
    frames = [ctrl.tick(DT, None) for _ in range(int((ClawdletController.WORK_GLANCE_POKE_S + 0.2) / DT))]
    glance = [f for f in frames if f.state == PetState.WORK_GLANCE]
    assert len(glance) == pytest.approx(ClawdletController.WORK_GLANCE_POKE_S / DT, abs=2)
    assert glance[0].animation_name == "work_glance"
    assert frames[-1].state == PetState.TYPING
    assert frames[-1].animation_name == "typing_loop"  # Laptop already out: no intro replay
    assert ctrl.manual_typing is True


def test_airborne_or_held_pet_ignores_reactions() -> None:
    ctrl = _floor()
    ctrl.hop()
    ctrl.tick(DT, None)
    assert ctrl.poke() is False
    ctrl.begin_drag(560.0, 700.0)
    assert ctrl.flail() is False
    assert ctrl.state == PetState.DRAGGED


def test_claude_thinking_does_not_cut_a_reaction_short() -> None:
    ctrl = _floor()
    ctrl.flail()
    thinking = WindowSnapshot(hwnd=0x1, rect=Rect(1300.0, 100.0, 1500.0, 300.0), is_thinking=True)
    states = _run(ctrl, ClawdletController.FLAIL_DURATION_S - 0.1, thinking)
    assert set(states) == {PetState.FLAIL}
    states = _run(ctrl, 0.3, thinking)
    assert states[-1] == PetState.TYPING


def test_dispatch_gesture_routes_to_controller() -> None:
    ctrl = _floor()
    dispatch_gesture(Gesture.POKE, ctrl)
    assert ctrl.state == PetState.POKED
    dispatch_gesture(Gesture.COMBO, ctrl)
    assert ctrl.state == PetState.FLAIL
    ctrl = _floor()
    dispatch_gesture(Gesture.PET, ctrl)
    assert ctrl.state == PetState.PETTED
    ctrl = _floor()
    dispatch_gesture(Gesture.DOUBLE, ctrl)
    assert ctrl.manual_typing is True
    assert ctrl.state == PetState.TYPING
