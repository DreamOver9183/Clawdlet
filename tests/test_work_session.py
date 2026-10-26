"""Milestone 6 phase 2: work sessions — typing segments with breaks, a heuristic cap, no per-frame reset."""

from __future__ import annotations

import random

import pytest

from clawdlet.agent_events import AgentEvent
from clawdlet.behavior import (
    _WORK_BREAK_STATES,
    ClawdletController,
    PetState,
    WindowSnapshot,
)
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody
from clawdlet.work_session import WorkConfig, WorkDirective, WorkSession, WorkSignal

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
# Claude Desktop "thinking" by the I/O heuristic only; its top sits at the screen top, so it is
# never climbed and the pet stays on the floor
DESKTOP_THINKING = WindowSnapshot(hwnd=0x1, rect=Rect(1300.0, 0.0, 1500.0, 300.0), is_thinking=True)
CFG = WorkConfig()
IO = WorkSignal(heuristic=True, agent_busy=False)
HOOK = WorkSignal(heuristic=False, agent_busy=True)
QUIET = WorkSignal(heuristic=False, agent_busy=False)
MANUAL = WorkSignal(heuristic=False, agent_busy=False, manual=True)


def _drive(
    session: WorkSession,
    sig: WorkSignal,
    seconds: float,
    t0: float,
    mode: ActivityMode = ActivityMode.NORMAL,
) -> tuple[list[tuple[float, WorkDirective]], float]:
    """Feed ``sig`` for ``seconds``; return the non-NONE directives with their times and the end time."""
    out: list[tuple[float, WorkDirective]] = []
    now = t0
    for _ in range(int(round(seconds / DT))):
        now += DT
        d = session.update(DT, sig, now, mode)
        if d != WorkDirective.NONE:
            out.append((now, d))
    return out, now


# --- WorkSession unit tests ------------------------------------------------------------------------


def test_start_break_resume_cycle_follows_segment_and_break_lengths() -> None:
    s = WorkSession(CFG, random.Random(1))
    assert s.update(DT, HOOK, DT, ActivityMode.NORMAL) == WorkDirective.START
    lo, hi = CFG.segment_s[ActivityMode.NORMAL]
    segment = s.phase_len
    assert lo <= segment <= hi
    events, _ = _drive(s, HOOK, 2 * hi + 2 * CFG.break_s[1], DT)
    kinds = [d for _, d in events]
    assert kinds[:3] == [WorkDirective.BREAK, WorkDirective.RESUME, WorkDirective.BREAK]
    assert events[0][0] == pytest.approx(DT + segment, abs=2 * DT)
    brk = events[1][0] - events[0][0]
    assert CFG.break_s[0] - 2 * DT <= brk <= CFG.break_s[1] + 2 * DT
    assert WorkDirective.END not in kinds and WorkDirective.CAP not in kinds


@pytest.mark.parametrize("mode", list(ActivityMode))
def test_segment_lengths_follow_activity_mode(mode: ActivityMode) -> None:
    s = WorkSession(CFG, random.Random(2))
    lo, hi = CFG.segment_s[mode]
    for _ in range(20):
        s.begin_segment(mode)
        assert lo <= s.phase_len <= hi


def test_signal_must_stay_quiet_for_the_debounce_before_end() -> None:
    s = WorkSession(CFG, random.Random(3))
    _drive(s, HOOK, 2.0, 0.0)
    events, now = _drive(s, QUIET, CFG.quiet_debounce_s - 0.1, 2.0)
    assert events == [] and s.active
    _drive(s, HOOK, 0.1, now)  # The signal flickers back: the debounce restarts
    events, _ = _drive(s, QUIET, CFG.quiet_debounce_s + 0.1, now + 0.1)
    assert [d for _, d in events] == [WorkDirective.END]
    assert events[0][0] == pytest.approx(now + 0.1 + CFG.quiet_debounce_s, abs=2 * DT)
    assert not s.active and s.last_end == "end"


def test_heuristic_only_session_is_capped_then_cooled_down() -> None:
    s = WorkSession(CFG, random.Random(4))
    events, now = _drive(s, IO, 150.0, 0.0)
    caps = [t for t, d in events if d == WorkDirective.CAP]
    assert caps == [pytest.approx(CFG.heuristic_max_s, abs=2 * DT)]
    assert not [d for t, d in events if t > caps[0]]  # Ignored for the whole cooldown
    assert not s.effective(IO, caps[0] + CFG.heuristic_cooldown_s - 1.0)
    assert s.last_end == "cap"
    assert "last=cap" in s.describe(now)
    events, _ = _drive(s, IO, 2.0, caps[0] + CFG.heuristic_cooldown_s)
    assert events[0][1] == WorkDirective.START


def test_strong_signals_ignore_the_cooldown_and_never_cap() -> None:
    s = WorkSession(CFG, random.Random(5))
    events, now = _drive(s, IO, CFG.heuristic_max_s + 1.0, 0.0)
    assert events[-1][1] == WorkDirective.CAP
    assert s.update(DT, HOOK, now + DT, ActivityMode.NORMAL) == WorkDirective.START
    both = WorkSignal(heuristic=True, agent_busy=True)
    events, _ = _drive(s, both, 150.0, now + DT)
    assert WorkDirective.CAP not in [d for _, d in events]


def test_done_mutes_the_heuristic_tail_only() -> None:
    s = WorkSession(CFG, random.Random(6))
    s.note_done(10.0)
    assert not s.effective(IO, 10.0 + CFG.done_suppress_s - 0.1)
    assert s.effective(IO, 10.0 + CFG.done_suppress_s)
    assert s.effective(HOOK, 10.5)
    events, _ = _drive(s, IO, CFG.done_suppress_s - 0.5, 10.0)
    assert events == []


def test_manual_typing_takes_no_breaks() -> None:
    s = WorkSession(CFG, random.Random(7))
    events, _ = _drive(s, MANUAL, 120.0, 0.0)
    assert [d for _, d in events] == [WorkDirective.START]


def test_describe_reports_phase_progress_and_source() -> None:
    s = WorkSession(CFG, random.Random(8))
    assert s.describe(0.0) == "work=idle src=- cooldown=0s"
    _drive(s, WorkSignal(heuristic=False, agent_busy=True, subagents=2), 1.0, 0.0)
    text = s.describe(1.0)
    assert text.startswith("work=typing seg=1.0/")
    assert "src=agent" in text and "sub=2" in text and "cooldown=0s" in text


# --- Controller integration ------------------------------------------------------------------------


def _floor(mode: ActivityMode = ActivityMode.NORMAL, seed: int = 5) -> ClawdletController:
    body = PhysicsBody(x=500.0, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=seed)
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


def _spy_directives(ctrl: ClawdletController) -> list[tuple[float, WorkDirective]]:
    seen: list[tuple[float, WorkDirective]] = []
    update = ctrl._work.update

    def spy(dt: float, sig: WorkSignal, now: float, mode: ActivityMode) -> WorkDirective:
        d = update(dt, sig, now, mode)
        if d != WorkDirective.NONE:
            seen.append((now, d))
        return d

    ctrl._work.update = spy  # type: ignore[method-assign]
    return seen


def _longest_run(states: list[PetState], target: PetState) -> int:
    best = run = 0
    for st in states:
        run = run + 1 if st == target else 0
        best = max(best, run)
    return best


def test_desktop_heuristic_alone_caps_typing_at_a_minute() -> None:
    ctrl = _floor()
    directives = _spy_directives(ctrl)
    times: list[float] = []
    states: list[PetState] = []
    for _ in range(int(round(165.0 / DT))):
        states.append(ctrl.tick(DT, DESKTOP_THINKING).state)
        times.append(ctrl.clock)

    caps = [t for t, d in directives if d == WorkDirective.CAP]
    assert caps == [pytest.approx(CFG.heuristic_max_s, abs=0.1)]
    assert states[0] == PetState.TYPING
    assert _WORK_BREAK_STATES & set(states[: int(CFG.heuristic_max_s / DT)])  # Breaks happen, too
    cooldown = [s for t, s in zip(times, states) if caps[0] + 2.0 < t < caps[0] + CFG.heuristic_cooldown_s]
    assert PetState.TYPING not in cooldown and not _WORK_BREAK_STATES & set(cooldown)
    assert PetState.PUT_AWAY_LAPTOP in states[int(caps[0] / DT) - 2 :]
    after = [s for t, s in zip(times, states) if t >= caps[0] + CFG.heuristic_cooldown_s]
    assert PetState.TYPING in after  # The heuristic counts again once the cooldown is over


@pytest.mark.parametrize("where", ["floor", "perch"])
def test_long_hook_turn_types_in_segments_with_breaks(where: str) -> None:
    ctrl = _floor() if where == "floor" else _perched()
    win = None if where == "floor" else SNAP
    frames = []
    timers: list[float] = []
    for _ in range(24):  # 120 s, a hook event every 5 s like a busy Claude Code turn
        ctrl.handle_agent_event(AgentEvent.BUSY, "s")
        for _ in range(int(round(5.0 / DT))):
            frames.append(ctrl.tick(DT, win))
            timers.append(ctrl._state_timer)
    states = [f.state for f in frames]

    assert states[0] == PetState.TYPING
    longest = _longest_run(states, PetState.TYPING) * DT
    assert longest <= CFG.segment_s[ActivityMode.NORMAL][1] + 0.1
    breaks = sum(1 for a, b in zip(states, states[1:]) if a == PetState.TYPING and b in _WORK_BREAK_STATES)
    assert breaks >= 120.0 // (CFG.segment_s[ActivityMode.NORMAL][1] + 5.0)  # Longest table break: 5 s
    assert set(states) <= {PetState.TYPING, *_WORK_BREAK_STATES}  # Laptop never put away
    # Back from a break: the loop strip, not the 11-frame laptop intro
    resumed = [f for f, prev in zip(frames[1:], states) if prev in _WORK_BREAK_STATES and f.state == PetState.TYPING]
    assert resumed and all(f.animation_name == "typing_loop" for f in resumed)
    assert frames[0].animation_name == "typing_laptop"
    # Regression: the state timer runs on during a typing segment (no per-frame reset)
    for (a, b), (ta, tb) in zip(zip(states, states[1:]), zip(timers, timers[1:])):
        if a == b == PetState.TYPING:
            assert tb > ta
    if where == "perch":
        assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)


def test_breaks_never_flip_the_laptop() -> None:
    ctrl = _floor()
    ctrl.facing = -1
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    frames = []
    for _ in range(int(round(40.0 / DT))):
        frames.append(ctrl.tick(DT, None))
        if len(frames) % 300 == 0:
            ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert any(f.state in _WORK_BREAK_STATES for f in frames)
    assert all(not f.flip_x for f in frames if f.state in {PetState.TYPING, *_WORK_BREAK_STATES})


def test_heuristic_tail_after_done_does_not_restart_typing() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert [ctrl.tick(DT, DESKTOP_THINKING).state for _ in range(30)][-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.DONE, "s")  # Claude Desktop's I/O keeps going for a bit
    assert ctrl.state == PetState.PUT_AWAY_LAPTOP
    muted = [ctrl.tick(DT, DESKTOP_THINKING).state for _ in range(int((CFG.done_suppress_s - 0.3) / DT))]
    assert PetState.CELEBRATE in muted
    assert PetState.TYPING not in muted
    later = [ctrl.tick(DT, DESKTOP_THINKING).state for _ in range(int(1.0 / DT))]
    assert PetState.TYPING in later  # Still "thinking" after the mute: a real new turn


def test_capped_heuristic_no_longer_blocks_sleep() -> None:
    ctrl = _floor()
    far_idle = PointerSnapshot(1400.0, 50.0, idle_s=200.0)
    states = [ctrl.tick(DT, DESKTOP_THINKING, far_idle).state for _ in range(int(30.0 / DT))]
    assert PetState.YAWN not in states and PetState.SLEEP not in states  # Busy: stays awake
    states = [ctrl.tick(DT, DESKTOP_THINKING, far_idle).state for _ in range(int(40.0 / DT))]
    assert PetState.SLEEP in states
    assert ctrl._work.last_end == "cap"


def test_poke_while_working_glances_then_keeps_typing() -> None:
    ctrl = _perched()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert [ctrl.tick(DT, SNAP).state for _ in range(30)][-1] == PetState.TYPING
    assert ctrl.poke() is True
    assert ctrl.state == PetState.WORK_GLANCE
    assert ctrl.flail() is False and ctrl.pet() is False
    frames = [ctrl.tick(DT, SNAP) for _ in range(int((ClawdletController.WORK_GLANCE_POKE_S + 0.2) / DT))]
    assert frames[0].animation_name == "work_glance"
    assert frames[-1].state == PetState.TYPING and frames[-1].animation_name == "typing_loop"
    assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)


def test_work_rng_is_separate_from_the_fsm_rng() -> None:
    ctrl = _floor(seed=42)
    before = ctrl._rng.getstate()
    states = []
    for _ in range(12):
        ctrl.handle_agent_event(AgentEvent.BUSY, "s")
        states += [ctrl.tick(DT, None).state for _ in range(int(round(5.0 / DT)))]
    assert _WORK_BREAK_STATES & set(states)
    assert ctrl._rng.getstate() == before  # Segments and breaks never draw from _rng


def test_debug_line_includes_work_session() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    ctrl.tick(DT, None)
    assert ctrl.work_description().startswith("work=typing seg=")
    assert "src=agent" in ctrl.work_description()
