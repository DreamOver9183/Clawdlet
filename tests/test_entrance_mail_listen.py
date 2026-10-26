"""Milestone 6 phase 6: entrances, the goodbye wave, mail delivery and listening."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import json
from pathlib import Path
import random

import pytest

from clawdlet.agent_events import AgentEvent
from clawdlet.app import ExitSequencer, dispatch_tray_action, open_entrance_sides, parse_args
from clawdlet.behavior import (
    ENTRANCE_DROP_ON_CLAUDE,
    ENTRANCE_DROP_TOP,
    ENTRANCE_WALK_IN_SIDE,
    ClawdletController,
    MailState,
    PetState,
    WindowSnapshot,
    choose_entrance,
)
from clawdlet.behavior_table import build_behavior_table, load_bundled_behavior_table
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody
from clawdlet.tray import CMD_SUMMON_CENTER, CMD_TOGGLE_LISTEN, CMD_TOGGLE_MAIL, TrayMenuState

REPO = Path(__file__).resolve().parents[1]
DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
# Maximized: perchable (so its hwnd is remembered) but never climbed, which keeps floor tests on the floor
MAX_WIN = WindowSnapshot(hwnd=0xABC, rect=Rect(0.0, 0.0, 1536.0, 816.0), maximized=True)
NEW_STATES = ("ENTER_WALK", "GOODBYE", "MAIL_CARRY", "MAIL_WAIT", "MAIL_GIVE", "LISTEN")


def _floor(x: float = 500.0, mode: ActivityMode = ActivityMode.NORMAL, seed: int = 5) -> ClawdletController:
    body = PhysicsBody(x=x, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=seed)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 999.0
    return ctrl


def _perched(x: float = 600.0) -> ClawdletController:
    ctrl = _floor()
    ctrl.body.x = x
    ctrl.body.y = WIN_RECT.top - 128.0
    ctrl._perched_hwnd = SNAP.hwnd
    ctrl._perch_offset_x = ctrl.body.x - WIN_RECT.left
    ctrl.state = PetState.PERCH_CLAUDE
    return ctrl


def _run(
    ctrl: ClawdletController,
    seconds: float,
    win: WindowSnapshot | None,
    pointer: Callable[[float], PointerSnapshot | None] | PointerSnapshot | None = None,
    until: Callable[[ClawdletController], bool] | None = None,
) -> list[PetState]:
    states: list[PetState] = []
    t0 = ctrl.clock
    for _ in range(int(round(seconds / DT))):
        p = pointer(ctrl.clock - t0) if callable(pointer) else pointer
        states.append(ctrl.tick(DT, win, p).state)
        assert ctrl.unhandled_state_resets == 0
        if until is not None and until(ctrl):
            break
    return states


# --- Entrances ------------------------------------------------------------------------------------


def test_bundled_entrance_table() -> None:
    table = load_bundled_behavior_table()
    assert table.warnings == ()
    weights = {row.kind: row.weight for row in table.entrances}
    assert weights == {ENTRANCE_DROP_ON_CLAUDE: 5.0, ENTRANCE_DROP_TOP: 3.0, ENTRANCE_WALK_IN_SIDE: 2.0}
    drop_on_claude = next(r for r in table.entrances if r.kind == ENTRANCE_DROP_ON_CLAUDE)
    assert drop_on_claude.when.claude_visible is True


def test_entrance_distribution_and_claude_gating() -> None:
    table = load_bundled_behavior_table()
    rng = random.Random(1)
    n = 6000
    with_claude = Counter(choose_entrance(table, SNAP, rng) for _ in range(n))
    assert with_claude[ENTRANCE_DROP_ON_CLAUDE] / n == pytest.approx(0.5, abs=0.03)
    assert with_claude[ENTRANCE_DROP_TOP] / n == pytest.approx(0.3, abs=0.03)
    assert with_claude[ENTRANCE_WALK_IN_SIDE] / n == pytest.approx(0.2, abs=0.03)

    hidden = WindowSnapshot(hwnd=1, rect=WIN_RECT, minimized=True)
    for win in (None, hidden):
        picks = Counter(choose_entrance(table, win, rng) for _ in range(n))
        assert ENTRANCE_DROP_ON_CLAUDE not in picks
        assert picks[ENTRANCE_DROP_TOP] / n == pytest.approx(0.6, abs=0.03)


def test_entrance_exclusions_sides_and_fallback() -> None:
    table = load_bundled_behavior_table()
    rng = random.Random(2)
    summon = {choose_entrance(table, SNAP, rng, exclude=(ENTRANCE_DROP_ON_CLAUDE,)) for _ in range(300)}
    assert summon == {ENTRANCE_DROP_TOP, ENTRANCE_WALK_IN_SIDE}
    no_sides = {choose_entrance(table, None, rng, sides=()) for _ in range(300)}
    assert no_sides == {ENTRANCE_DROP_TOP}
    # A user table that ignores the gating still never drops onto a missing window
    custom = build_behavior_table({"version": 1, "entrances": [{"id": "c", "kind": "drop_on_claude"}]})
    assert choose_entrance(custom, None, rng) == ENTRANCE_DROP_TOP
    off = build_behavior_table({"version": 1, "entrances": [{"id": "t", "kind": "drop_top", "enabled": False}]})
    assert choose_entrance(off, SNAP, rng) == ENTRANCE_DROP_TOP  # Nothing eligible: fall from the top


def test_entrances_never_draw_from_the_fsm_rng() -> None:
    ctrl = _floor()
    before = ctrl._rng.getstate()
    for _ in range(20):
        ctrl.enter_random(SNAP)
    assert ctrl._rng.getstate() == before


def test_constructor_keeps_the_spawn_position() -> None:
    body = PhysicsBody(x=321.0, y=123.0, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, rng_seed=3)
    assert (ctrl.body.x, ctrl.body.y, ctrl.state) == (321.0, 123.0, PetState.FALLING)


def test_drop_top_falls_from_the_work_area_top() -> None:
    ctrl = _floor()
    ctrl.enter(ENTRANCE_DROP_TOP)
    assert ctrl.state == PetState.FALLING
    assert ctrl.body.y == WORK_AREA.top
    assert WORK_AREA.left <= ctrl.body.x <= WORK_AREA.right - ctrl.body.width
    _run(ctrl, 3.0, None, until=lambda c: c.state == PetState.IDLE)
    assert ctrl.body.bottom == pytest.approx(WORK_AREA.bottom)


def test_drop_on_claude_lands_on_the_title_bar() -> None:
    ctrl = _floor()
    ctrl.enter(ENTRANCE_DROP_ON_CLAUDE, SNAP)
    assert ctrl.state == PetState.FALLING
    assert ctrl.body.center_x == pytest.approx(WIN_RECT.center_x)
    _run(ctrl, 3.0, SNAP, until=lambda c: c.state == PetState.PERCH_CLAUDE)
    assert ctrl.state == PetState.PERCH_CLAUDE
    assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)
    # Without a window it falls from the top instead
    ctrl.enter(ENTRANCE_DROP_ON_CLAUDE, None)
    assert ctrl.state == PetState.FALLING and ctrl.body.y == WORK_AREA.top


@pytest.mark.parametrize("side", [-1, 1])
def test_walk_in_starts_off_screen_and_skips_the_wall_clamp(side: int) -> None:
    ctrl = _floor()
    ctrl.enter(ENTRANCE_WALK_IN_SIDE, None, sides=(side,))
    assert ctrl.state == PetState.ENTER_WALK
    assert ctrl.body.bottom == pytest.approx(WORK_AREA.bottom)
    xs = []
    for _ in range(10):
        frame = ctrl.tick(DT, None, None)
        xs.append(ctrl.body.x)
        assert frame.animation_name == "walk_horizontal"
        assert frame.flip_x is (side > 0)  # Facing in
    if side < 0:
        assert all(x < WORK_AREA.left for x in xs)  # Physics would have clamped this to 0
        assert xs == sorted(xs)
    else:
        assert all(x + ctrl.body.width > WORK_AREA.right for x in xs)
        assert xs == sorted(xs, reverse=True)
    assert ctrl.poke() is False  # Unreactive while walking in
    states = _run(ctrl, 3.0, None, until=lambda c: c.state != PetState.ENTER_WALK)
    assert ctrl.state == PetState.WANDER
    assert WORK_AREA.left <= ctrl.body.x <= WORK_AREA.right - ctrl.body.width
    assert ctrl.facing == -side
    assert 1.0 <= ctrl._decision_duration <= 2.0
    assert len(states) < 3.0 / DT
    _run(ctrl, 3.0, None)  # Strolls on and settles without falling through any branch


def test_walk_in_picks_an_open_side() -> None:
    sides = set()
    for seed in range(12):
        ctrl = _floor(seed=seed)
        ctrl.enter(ENTRANCE_WALK_IN_SIDE, None)
        sides.add(ctrl._enter_side)
    assert sides == {-1, 1}
    ctrl = _floor()
    ctrl.enter(ENTRANCE_WALK_IN_SIDE, None, sides=())
    assert ctrl.state == PetState.FALLING and ctrl.body.y == WORK_AREA.top


def test_open_entrance_sides_skip_sides_bordering_another_monitor() -> None:
    primary = Rect(0.0, 0.0, 1920.0, 1080.0)
    assert open_entrance_sides(primary, primary) == (-1, 1)
    assert open_entrance_sides(primary, Rect(-1920.0, 0.0, 1920.0, 1080.0)) == (1,)
    assert open_entrance_sides(primary, Rect(0.0, -200.0, 4480.0, 1440.0)) == (-1,)
    assert open_entrance_sides(primary, Rect(-1280.0, 0.0, 3840.0, 1080.0)) == ()
    assert open_entrance_sides(primary, Rect(0.0, -1080.0, 1920.0, 1080.0)) == (-1, 1)  # Stacked above
    assert open_entrance_sides(None, None) == (-1, 1)


def test_summon_to_center_tray_uses_an_entrance_but_never_drops_on_claude() -> None:
    for seed in range(8):
        ctrl = _floor(seed=seed)
        dispatch_tray_action(CMD_SUMMON_CENTER, ctrl, None, TrayMenuState(), SNAP)  # type: ignore[arg-type]
        assert ctrl.state in (PetState.FALLING, PetState.ENTER_WALK)
        if ctrl.state == PetState.FALLING:
            assert ctrl.body.y == WORK_AREA.top
    ctrl = _floor()
    dispatch_tray_action(CMD_SUMMON_CENTER, ctrl, None, TrayMenuState(), SNAP, entrance_sides=())  # type: ignore[arg-type]
    assert ctrl.state == PetState.FALLING and ctrl.body.y == WORK_AREA.top


def test_spawn_flag_defaults_to_an_entrance_and_new_flags_parse() -> None:
    args = parse_args([])
    assert args.spawn is None and args.no_mail is False and args.no_listen is False
    args = parse_args(["--spawn", "center", "--no-mail", "--no-listen"])
    assert args.spawn == "center" and args.no_mail and args.no_listen


# --- Goodbye -------------------------------------------------------------------------------------


def test_goodbye_waves_on_the_floor_and_ignores_clicks() -> None:
    ctrl = _floor()
    assert ctrl.begin_goodbye() == pytest.approx(1.2)
    assert ctrl.state == PetState.GOODBYE
    frame = ctrl.tick(DT, None, None)
    assert frame.animation_name == "wave_goodbye"
    assert ctrl.poke() is False and ctrl.pet() is False
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    states = _run(ctrl, 1.5, None)
    assert set(states) == {PetState.GOODBYE}


def test_goodbye_while_perched_stays_on_the_title_bar() -> None:
    ctrl = _perched()
    assert ctrl.begin_goodbye() > 0.0
    states = _run(ctrl, 1.5, SNAP)
    assert set(states) == {PetState.GOODBYE}
    assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)


def test_goodbye_is_skipped_when_airborne_held_or_climbing() -> None:
    ctrl = _floor()
    ctrl.hop()
    assert ctrl.begin_goodbye() == 0.0 and ctrl.state == PetState.FALLING
    ctrl = _floor()
    ctrl.begin_drag(560.0, 700.0)
    assert ctrl.begin_goodbye() == 0.0 and ctrl.state == PetState.DRAGGED
    ctrl = _floor()
    ctrl._start_climb_screen_wall(-1)
    assert ctrl.begin_goodbye() == 0.0 and ctrl.state == PetState.CLIMB


def test_exit_sequencer_waits_for_the_wave_at_most_two_seconds() -> None:
    seq = ExitSequencer()
    assert seq.request(10.0, 1.2) is False
    assert seq.pending and not seq.due(11.19) and seq.due(11.2)
    long_wave = ExitSequencer()
    assert long_wave.request(10.0, 5.0) is False
    assert long_wave.deadline == pytest.approx(12.0)
    assert ExitSequencer().request(10.0, 0.0) is True  # Airborne: no wave, close now
    assert seq.request(10.5, 1.2) is True  # Exit chosen again while waving: close now
    assert not ExitSequencer().due(1e9)


# --- Mail delivery --------------------------------------------------------------------------------


def _away(t: float) -> PointerSnapshot:
    return PointerSnapshot(1000.0, 500.0, idle_s=40.0 + t)


BACK = PointerSnapshot(1000.0, 500.0, idle_s=0.2)


def _busy_then_done(ctrl: ClawdletController, win: WindowSnapshot | None, session: str = "s") -> None:
    ctrl.handle_agent_event(AgentEvent.BUSY, session)
    _run(ctrl, 1.0, win, _away)
    ctrl.handle_agent_event(AgentEvent.DONE, session)


def _deliver(ctrl: ClawdletController, win: WindowSnapshot) -> list[PetState]:
    return _run(ctrl, 10.0, win, BACK, until=lambda c: c.state == PetState.MAIL_WAIT)


def test_mail_when_away_is_delivered_on_return_and_focuses_claude() -> None:
    ctrl = _floor()
    focused: list[int] = []
    ctrl.window_focuser = focused.append
    _busy_then_done(ctrl, MAX_WIN)
    assert ctrl.has_mail
    states = _run(ctrl, 4.0, MAX_WIN, _away)
    assert PetState.CELEBRATE not in states  # Nobody to celebrate with: keep a letter instead
    assert not any(s.name.startswith("MAIL") for s in states)

    states = _deliver(ctrl, MAX_WIN)
    assert ctrl.state == PetState.MAIL_WAIT
    assert PetState.MAIL_CARRY in states
    assert abs(ctrl.body.center_x - BACK.x) <= 8.0
    assert ctrl.tick(DT, MAX_WIN, BACK).animation_name == "hold_mail"

    assert ctrl.poke() is True
    assert ctrl.state == PetState.MAIL_GIVE and not ctrl.has_mail
    states = _run(ctrl, 0.79, MAX_WIN, BACK)
    assert set(states) == {PetState.MAIL_GIVE} and focused == []
    _run(ctrl, 0.1, MAX_WIN, BACK)
    assert focused == [MAX_WIN.hwnd]
    assert ctrl.state == PetState.CELEBRATE


def test_mail_carry_walks_with_the_envelope() -> None:
    ctrl = _floor(x=200.0)
    _busy_then_done(ctrl, MAX_WIN)
    _run(ctrl, 3.0, MAX_WIN, _away)
    frames = []
    for _ in range(int(3.0 / DT)):
        frames.append(ctrl.tick(DT, MAX_WIN, BACK))
        if ctrl.state == PetState.MAIL_WAIT:
            break
    carry = [f for f in frames if f.state == PetState.MAIL_CARRY]
    assert carry and all(f.animation_name == "walk_mail" and f.vx > 0 for f in carry[1:])


def test_mail_on_the_title_bar() -> None:
    ctrl = _perched(x=350.0)
    focused: list[int] = []
    ctrl.window_focuser = focused.append
    _busy_then_done(ctrl, SNAP)
    _run(ctrl, 3.0, SNAP, _away)
    assert ctrl.has_mail
    states = _deliver(ctrl, SNAP)
    assert PetState.MAIL_CARRY in states and ctrl.state == PetState.MAIL_WAIT
    assert ctrl._perched_hwnd == SNAP.hwnd
    assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)
    assert abs(ctrl.body.center_x - BACK.x) <= 8.0
    ctrl.flail()  # Clicking away at it still takes the letter
    assert ctrl.state == PetState.MAIL_GIVE
    _run(ctrl, 1.0, SNAP, BACK)
    assert focused == [SNAP.hwnd] and ctrl.state == PetState.CELEBRATE
    assert ctrl._perched_hwnd == SNAP.hwnd


def test_mail_follows_a_cursor_that_moves_away() -> None:
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN)
    _run(ctrl, 3.0, MAX_WIN, _away)
    _deliver(ctrl, MAX_WIN)
    moved = PointerSnapshot(300.0, 500.0, idle_s=0.1)
    states = _run(ctrl, 10.0, MAX_WIN, moved, until=lambda c: c.state == PetState.MAIL_WAIT)
    assert PetState.MAIL_CARRY in states and ctrl.state == PetState.MAIL_WAIT
    assert abs(ctrl.body.center_x - moved.x) <= 8.0


def test_undelivered_mail_times_out() -> None:
    ctrl = _floor()
    focused: list[int] = []
    ctrl.window_focuser = focused.append
    _busy_then_done(ctrl, MAX_WIN)
    _run(ctrl, 3.0, MAX_WIN, _away)
    _deliver(ctrl, MAX_WIN)
    states = _run(ctrl, ctrl.MAIL_TIMEOUT_S + 1.0, MAX_WIN, BACK)
    assert not ctrl.has_mail
    assert ctrl.state not in (PetState.MAIL_CARRY, PetState.MAIL_WAIT)
    assert PetState.CELEBRATE not in states and PetState.MAIL_GIVE not in states
    assert focused == []


def test_present_user_celebrates_as_before() -> None:
    ctrl = _floor()
    present = PointerSnapshot(1000.0, 500.0, idle_s=2.0)
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    _run(ctrl, 1.0, MAX_WIN, present)
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    states = _run(ctrl, 3.0, MAX_WIN, present)
    assert not ctrl.has_mail and PetState.CELEBRATE in states


def test_mail_can_be_switched_off() -> None:
    ctrl = _floor()
    ctrl.set_mail_enabled(False)
    _busy_then_done(ctrl, MAX_WIN)
    states = _run(ctrl, 3.0, MAX_WIN, _away)
    assert not ctrl.has_mail and PetState.CELEBRATE in states
    # Switching it off mid-delivery drops the letter
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN)
    _run(ctrl, 3.0, MAX_WIN, _away)
    _deliver(ctrl, MAX_WIN)
    ctrl.set_mail_enabled(False)
    assert not ctrl.has_mail and ctrl.state == PetState.IDLE


def test_same_session_busy_drops_the_mail() -> None:
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN, session="a")
    ctrl.handle_agent_event(AgentEvent.BUSY, "b")  # Another session: the letter stays
    assert ctrl.has_mail
    ctrl.handle_agent_event(AgentEvent.BUSY, "a")
    assert not ctrl.has_mail
    # ...also mid-delivery
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN, session="a")
    _run(ctrl, 3.0, MAX_WIN, _away)
    _deliver(ctrl, MAX_WIN)
    ctrl.handle_agent_event(AgentEvent.BUSY, "a")
    assert not ctrl.has_mail and ctrl.state not in (PetState.MAIL_CARRY, PetState.MAIL_WAIT)


def test_mail_beats_typing_for_another_session() -> None:
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN, session="a")
    ctrl.handle_agent_event(AgentEvent.BUSY, "b")
    states = _run(ctrl, 4.0, MAX_WIN, _away)
    assert PetState.TYPING in states and ctrl.has_mail
    states = _deliver(ctrl, MAX_WIN)
    assert ctrl.state == PetState.MAIL_WAIT
    assert states.index(PetState.PUT_AWAY_LAPTOP) < states.index(PetState.MAIL_CARRY)
    # Handed over: the laptop comes back out for session b after the celebration
    ctrl.poke()
    states = _run(ctrl, 4.0, MAX_WIN, BACK)
    assert PetState.CELEBRATE in states and ctrl.state == PetState.TYPING


def test_a_sleeping_pet_wakes_up_before_delivering() -> None:
    ctrl = _floor()
    _busy_then_done(ctrl, MAX_WIN)
    _run(ctrl, 3.0, MAX_WIN, _away)
    ctrl._set_state(PetState.SLEEP)
    states = _deliver(ctrl, MAX_WIN)
    assert states.index(PetState.WAKE_STARTLE) < states.index(PetState.MAIL_CARRY)


# --- Listening -------------------------------------------------------------------------------------


FG = WindowSnapshot(hwnd=0x14084, rect=Rect(250.0, 360.0, 950.0, 780.0), foreground=True)


def _typing(t: float) -> PointerSnapshot:
    """Cursor parked; some input every 0.25 s (all that GetLastInputInfo can tell)."""
    return PointerSnapshot(1300.0, 150.0, idle_s=t % 0.25)


def _listen_floor() -> ClawdletController:
    return _floor(x=1200.0)


def test_listens_while_the_user_types_into_claude() -> None:
    ctrl = _listen_floor()
    states = _run(ctrl, 1.5, FG, _typing)
    assert PetState.LISTEN in states
    assert states.index(PetState.LISTEN) >= int(0.6 / DT)  # Cursor still for 0.6 s first
    frame = ctrl.tick(DT, FG, _typing(1.5))
    assert frame.animation_name == "listen_tilt"
    assert frame.flip_x is True  # Claude's window is to the left: tilt toward it
    states = _run(ctrl, 3.0, FG, _typing)
    assert set(states) == {PetState.LISTEN}

    stopped = lambda t: PointerSnapshot(1300.0, 150.0, idle_s=0.3 + t)  # noqa: E731
    states = _run(ctrl, 3.0, FG, stopped)
    assert ctrl.state != PetState.LISTEN
    first_out = next(i for i, s in enumerate(states) if s != PetState.LISTEN)
    assert 1.2 / DT <= first_out <= 1.6 / DT
    assert PetState.LISTEN not in states[first_out:]  # No flicker back in while old pulses linger


@pytest.mark.parametrize("case", ["moving_cursor", "not_foreground", "agent_busy", "disabled", "mail"])
def test_no_listening_when(case: str) -> None:
    ctrl = _listen_floor()
    win = FG
    pointer: Callable[[float], PointerSnapshot] = _typing
    if case == "moving_cursor":
        pointer = lambda t: PointerSnapshot(1300.0 + 40.0 * (int(t / 0.25) % 2), 150.0, idle_s=t % 0.25)  # noqa: E731
    elif case == "not_foreground":
        win = WindowSnapshot(hwnd=FG.hwnd, rect=FG.rect, foreground=False)
    elif case == "agent_busy":
        ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    elif case == "disabled":
        ctrl.set_listen_enabled(False)
    elif case == "mail":
        ctrl._mail = MailState("s", 0.0)
        pointer = lambda t: PointerSnapshot(1300.0, 150.0, idle_s=5.0 + t % 0.25)  # noqa: E731  Not back yet
    states = _run(ctrl, 3.0, win, pointer)
    assert PetState.LISTEN not in states


def test_listening_ends_when_claude_loses_focus_or_work_starts() -> None:
    ctrl = _listen_floor()
    _run(ctrl, 1.5, FG, _typing, until=lambda c: c.state == PetState.LISTEN)
    assert ctrl.state == PetState.LISTEN
    ctrl.tick(DT, WindowSnapshot(hwnd=FG.hwnd, rect=FG.rect), _typing(0.0))
    assert ctrl.state != PetState.LISTEN

    ctrl = _listen_floor()
    _run(ctrl, 1.5, FG, _typing, until=lambda c: c.state == PetState.LISTEN)
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    states = _run(ctrl, 0.5, FG, _typing)
    assert ctrl.state == PetState.TYPING and PetState.LISTEN not in states[1:]


def test_listening_on_the_title_bar() -> None:
    ctrl = _perched(x=900.0)
    perched_fg = WindowSnapshot(hwnd=SNAP.hwnd, rect=WIN_RECT, foreground=True)
    states = _run(ctrl, 1.5, perched_fg, _typing)
    assert PetState.LISTEN in states and ctrl._perched_hwnd == SNAP.hwnd
    stopped = lambda t: PointerSnapshot(1300.0, 150.0, idle_s=0.3 + t)  # noqa: E731
    _run(ctrl, 2.5, perched_fg, stopped)
    assert ctrl.state != PetState.LISTEN and ctrl._perched_hwnd == SNAP.hwnd


def test_input_pulses_need_a_still_cursor() -> None:
    ctrl = _listen_floor()
    ctrl.tick(DT, None, PointerSnapshot(10.0, 10.0, idle_s=5.0))
    ctrl.tick(DT, None, PointerSnapshot(10.0, 10.0, idle_s=0.0))  # Input, cursor still: a pulse
    ctrl.tick(DT, None, PointerSnapshot(50.0, 10.0, idle_s=0.0))
    ctrl.tick(DT, None, PointerSnapshot(50.0, 10.0, idle_s=0.5))
    ctrl.tick(DT, None, PointerSnapshot(90.0, 10.0, idle_s=0.0))  # Mouse moved: not a pulse
    assert len(ctrl._input_pulses) == 1
    _run(ctrl, 2.1, None, PointerSnapshot(90.0, 10.0, idle_s=0.0))
    assert len(ctrl._input_pulses) == 0  # Forgotten after the 2 s window


# --- Tray, safety net and sprites -------------------------------------------------------------------


def test_tray_toggles_for_mail_and_listening() -> None:
    ctrl = _floor()
    tray = TrayMenuState()
    assert tray.mail_enabled and tray.listen_enabled
    dispatch_tray_action(CMD_TOGGLE_MAIL, ctrl, None, tray, None)  # type: ignore[arg-type]
    assert tray.mail_enabled is False and ctrl.mail_enabled is False
    dispatch_tray_action(CMD_TOGGLE_LISTEN, ctrl, None, tray, None)  # type: ignore[arg-type]
    assert tray.listen_enabled is False and ctrl.listen_enabled is False
    dispatch_tray_action(CMD_TOGGLE_MAIL, ctrl, None, tray, None)  # type: ignore[arg-type]
    dispatch_tray_action(CMD_TOGGLE_LISTEN, ctrl, None, tray, None)  # type: ignore[arg-type]
    assert ctrl.mail_enabled and ctrl.listen_enabled and tray.mail_enabled and tray.listen_enabled


@pytest.mark.parametrize("surface", ["floor", "perch"])
@pytest.mark.parametrize("state", NEW_STATES)
def test_new_states_never_hit_the_tick_fallback(state: str, surface: str) -> None:
    if surface == "floor":
        ctrl, win = _floor(), None
    else:
        ctrl, win = _perched(), SNAP
    ctrl.state = PetState[state]
    ctrl._decision_duration = 1.0
    _run(ctrl, 6.0, win, PointerSnapshot(900.0, 300.0, idle_s=0.2))


def test_manifest_has_every_new_animation() -> None:
    animations = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))["animations"]
    expected = {
        "ENTER_WALK": "walk_horizontal",
        "GOODBYE": "wave_goodbye",
        "MAIL_CARRY": "walk_mail",
        "MAIL_WAIT": "hold_mail",
        "MAIL_GIVE": "give_mail",
        "LISTEN": "listen_tilt",
    }
    for state, name in expected.items():
        assert PetState[state].animation_name == name
        assert name in animations
    assert animations["wave_goodbye"]["frames"] / animations["wave_goodbye"]["fps"] == pytest.approx(
        ClawdletController.GOODBYE_DURATION_S
    )
    assert animations["give_mail"]["frames"] / animations["give_mail"]["fps"] == pytest.approx(
        ClawdletController.MAIL_GIVE_S
    )
