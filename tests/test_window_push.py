"""Milestone 5 step E: opt-in Hyper-mode window push — safety policy and FSM behavior."""

from __future__ import annotations

from typing import Any

import pytest

from clawdlet.app import dispatch_tray_action, parse_args
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody
from clawdlet.tray import CMD_TOGGLE_WINDOW_PUSH, TrayMenuState
from clawdlet.window_push import (
    PUSH_COOLDOWN_S,
    PUSH_MAX_DISTANCE_PX,
    PushPlan,
    plan_window_push,
)

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
# A window whose lower part reaches down beside the pet standing on the taskbar floor
LOW_WIN = Rect(600.0, 300.0, 1200.0, 800.0)
FLOOR_Y = WORK_AREA.bottom - 128.0


def _plan(**overrides: Any) -> PushPlan | None:
    kwargs: dict[str, Any] = dict(
        mode=ActivityMode.HYPER,
        enabled=True,
        perched=False,
        win_rect=LOW_WIN,
        win_maximized=False,
        body_x=300.0,
        body_y=FLOOR_Y,
        body_w=128.0,
        body_h=128.0,
        work_area=WORK_AREA,
        idle_s=25.0,
        last_push_t=None,
        now=1000.0,
    )
    kwargs.update(overrides)
    return plan_window_push(**kwargs)


def test_plan_pushes_away_from_the_pet() -> None:
    assert _plan() == PushPlan(stand_x=LOW_WIN.left - 128.0, direction=1)
    assert _plan(body_x=1300.0) == PushPlan(stand_x=LOW_WIN.right, direction=-1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": ActivityMode.NORMAL},
        {"mode": ActivityMode.QUIET},
        {"enabled": False},
        {"perched": True},
        {"win_rect": None},
        {"win_maximized": True},
        {"idle_s": 5.0},           # user actively working
        {"idle_s": None},          # no input sample
        {"last_push_t": 1000.0 - PUSH_COOLDOWN_S + 1.0},  # cooldown
        {"win_rect": Rect(0.0, 0.0, 1500.0, 800.0)},      # fills the work area
        {"win_rect": Rect(600.0, 100.0, 1200.0, 500.0)},  # window does not reach the pet
        {"win_rect": Rect(600.0, 300.0, 1520.0, 800.0)},  # would be pushed off screen
    ],
)
def test_plan_refuses_unsafe_situations(overrides: dict[str, Any]) -> None:
    assert _plan(**overrides) is None


def test_cooldown_expires() -> None:
    assert _plan(last_push_t=1000.0 - PUSH_COOLDOWN_S - 1.0) is not None


class _FakeDesktop:
    """Records nudges and moves the fake window like SetWindowPos would."""

    def __init__(self, rect: Rect) -> None:
        self.rect = rect
        self.nudges: list[tuple[int, int]] = []

    def nudge(self, hwnd: int, dx: int) -> None:
        self.nudges.append((hwnd, dx))
        r = self.rect
        self.rect = Rect(r.left + dx, r.top, r.right + dx, r.bottom)

    def snap(self) -> WindowSnapshot:
        return WindowSnapshot(hwnd=0x77, rect=self.rect)


def _hyper_pet(desktop: _FakeDesktop, enabled: bool = True) -> ClawdletController:
    body = PhysicsBody(x=300.0, y=FLOOR_Y, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=ActivityMode.HYPER, rng_seed=2)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 0.0
    ctrl.push_window_enabled = enabled
    ctrl.window_nudger = desktop.nudge
    return ctrl


def test_hyper_pet_walks_over_and_pushes_window_a_little() -> None:
    desktop = _FakeDesktop(LOW_WIN)
    ctrl = _hyper_pet(desktop)
    idle = PointerSnapshot(50.0, 50.0, idle_s=40.0)
    states = [ctrl.tick(DT, desktop.snap(), idle).state for _ in range(int(6.0 / DT))]
    assert PetState.PUSH_WINDOW in states
    total = sum(dx for _, dx in desktop.nudges)
    assert 0 < total <= PUSH_MAX_DISTANCE_PX
    assert all(dx > 0 and hwnd == 0x77 for hwnd, dx in desktop.nudges)
    assert desktop.rect.right <= WORK_AREA.right
    assert states.count(PetState.PUSH_WINDOW) <= int(1.2 / DT) + 2
    assert ctrl.state != PetState.PUSH_WINDOW


def test_push_stops_the_moment_the_user_moves() -> None:
    desktop = _FakeDesktop(LOW_WIN)
    ctrl = _hyper_pet(desktop)
    idle = PointerSnapshot(50.0, 50.0, idle_s=40.0)
    for _ in range(int(6.0 / DT)):
        ctrl.tick(DT, desktop.snap(), idle)
        if ctrl.state == PetState.PUSH_WINDOW and desktop.nudges:
            break
    assert ctrl.state == PetState.PUSH_WINDOW
    before = len(desktop.nudges)
    ctrl.tick(DT, desktop.snap(), PointerSnapshot(60.0, 50.0, idle_s=0.0))
    assert ctrl.state == PetState.IDLE
    assert len(desktop.nudges) == before


def test_push_disabled_by_default_never_touches_window() -> None:
    desktop = _FakeDesktop(LOW_WIN)
    ctrl = _hyper_pet(desktop, enabled=False)
    idle = PointerSnapshot(50.0, 50.0, idle_s=40.0)
    states = {ctrl.tick(DT, desktop.snap(), idle).state for _ in range(int(8.0 / DT))}
    assert PetState.PUSH_WINDOW not in states
    assert desktop.nudges == []
    assert parse_args([]).allow_window_push is False


def test_maximized_window_is_never_pushed() -> None:
    desktop = _FakeDesktop(LOW_WIN)
    ctrl = _hyper_pet(desktop)
    idle = PointerSnapshot(50.0, 50.0, idle_s=40.0)
    for _ in range(int(6.0 / DT)):
        ctrl.tick(DT, WindowSnapshot(hwnd=0x77, rect=desktop.rect, maximized=True), idle)
    assert desktop.nudges == []


def test_tray_toggle_window_push() -> None:
    desktop = _FakeDesktop(LOW_WIN)
    ctrl = _hyper_pet(desktop, enabled=False)
    tray = TrayMenuState()
    dispatch_tray_action(CMD_TOGGLE_WINDOW_PUSH, ctrl, overlay=None, tray_state=tray, latest_win=None)  # type: ignore[arg-type]
    assert ctrl.push_window_enabled is True and tray.window_push is True
    dispatch_tray_action(CMD_TOGGLE_WINDOW_PUSH, ctrl, overlay=None, tray_state=tray, latest_win=None)  # type: ignore[arg-type]
    assert ctrl.push_window_enabled is False and tray.window_push is False
