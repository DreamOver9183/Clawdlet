"""Milestone 6 phase 4: Claude-activity-aware work actions — breaks, juggling, molting, alert and error escalation."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from clawdlet.agent_events import AgentEvent
from clawdlet.behavior import (
    _WORK_BREAK_STATES,
    RENDER_OVERRIDE_ANIMATIONS,
    ClawdletController,
    PetState,
    ProjectileRenderState,
    RenderFrameState,
    WindowSnapshot,
)
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.physics import PhysicsBody
from clawdlet.work_session import (
    PHASE_JUGGLE,
    WorkConfig,
    WorkDirective,
    WorkSession,
    WorkSignal,
)

REPO = Path(__file__).resolve().parents[1]
DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
CFG = WorkConfig()
SHIFT_PX = 8.0 * 128.0 / 64.0  # molt step-out at 2x scale


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


def _frames(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None = None) -> list[RenderFrameState]:
    return [ctrl.tick(DT, win) for _ in range(int(round(seconds / DT)))]


def _states(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None = None) -> list[PetState]:
    return [f.state for f in _frames(ctrl, seconds, win)]


# --- Work breaks ----------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["floor", "perch"])
def test_long_hook_turn_takes_varied_table_breaks_and_resumes_with_the_loop(where: str) -> None:
    ctrl = _floor() if where == "floor" else _perched()
    win = None if where == "floor" else SNAP
    before = ctrl._rng.getstate()
    frames: list[RenderFrameState] = []
    for _ in range(18):  # 90 s, a hook event every 5 s
        ctrl.handle_agent_event(AgentEvent.BUSY, "s")
        frames += _frames(ctrl, 5.0, win)
    states = [f.state for f in frames]
    assert ctrl._rng.getstate() == before
    breaks = {b for a, b in zip(states, states[1:]) if a == PetState.TYPING and b in _WORK_BREAK_STATES}
    assert len(breaks) >= 2
    table_states = {PetState[row.state] for row in ctrl.behavior_table.work_breaks}
    assert breaks <= table_states
    assert set(states) <= {PetState.TYPING, *_WORK_BREAK_STATES}
    resumed = [f for f, prev in zip(frames[1:], states) if prev in _WORK_BREAK_STATES and f.state == PetState.TYPING]
    assert resumed and all(f.animation_name == "typing_loop" for f in resumed)
    # Each break lasts its table row's duration_s (the state is the row's sprite, unflipped)
    rows = {PetState[row.state]: row for row in ctrl.behavior_table.work_breaks}
    run_state, run = states[0], 0
    for st in states[1:] + [None]:
        if st == run_state:
            run += 1
            continue
        if run_state in _WORK_BREAK_STATES:
            lo, hi = rows[run_state].duration_s
            assert lo - 2 * DT <= (run + 1) * DT <= hi + 2 * DT, run_state
        run_state, run = st, 0


def test_one_shot_break_strips_hold_their_last_frame() -> None:
    # Breaks are never shorter than their strips, so the clamped last (resting typing) frame shows
    manifest = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))
    for row in _floor().behavior_table.work_breaks:
        anim = manifest["animations"][PetState[row.state].animation_name]
        if not anim["loop"]:
            assert row.duration_s[0] >= anim["frames"] / anim["fps"], row.id


# --- Juggling -------------------------------------------------------------------------------------


def test_work_session_alternates_typing_and_juggling_while_subagents_run() -> None:
    session = WorkSession(CFG, random.Random(1))
    busy = WorkSignal(heuristic=False, agent_busy=True)
    juggling = WorkSignal(heuristic=False, agent_busy=True, subagents=2)
    now = 0.0
    assert session.update(DT, busy, now, ActivityMode.NORMAL) == WorkDirective.START
    seen: list[tuple[float, WorkDirective]] = []
    for _ in range(int(60.0 / DT)):
        now += DT
        d = session.update(DT, juggling, now, ActivityMode.NORMAL)
        if d != WorkDirective.NONE:
            seen.append((now, d))
    kinds = [d for _, d in seen]
    assert kinds[0] == WorkDirective.JUGGLE and seen[0][0] <= CFG.juggle_lead_s + 0.05
    assert WorkDirective.BREAK not in kinds  # Juggling replaces breaks
    assert kinds == [WorkDirective.JUGGLE, WorkDirective.RESUME] * (len(kinds) // 2) + kinds[len(kinds) // 2 * 2:]
    gaps = [b - a for (a, _), (b, _) in zip(seen, seen[1:])]
    lo, hi = CFG.juggle_segment_s
    assert all(lo - 0.05 <= g <= hi + 0.05 for g in gaps)
    while session.phase != PHASE_JUGGLE:
        now += DT
        session.update(DT, juggling, now, ActivityMode.NORMAL)
    now += DT
    assert session.update(DT, busy, now, ActivityMode.NORMAL) == WorkDirective.RESUME  # Last subagent done
    lo, hi = CFG.segment_s[ActivityMode.NORMAL]
    assert lo <= session.phase_len <= hi  # Back to a normal-length segment


@pytest.mark.parametrize("where", ["floor", "perch"])
def test_subagents_make_clawd_juggle_then_type_again(where: str) -> None:
    ctrl = _floor() if where == "floor" else _perched()
    win = None if where == "floor" else SNAP
    ctrl.facing = -1
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert _states(ctrl, 0.5, win)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.SUBAGENT_START, "s")
    ctrl.handle_agent_event(AgentEvent.SUBAGENT_START, "s")
    frames: list[RenderFrameState] = []
    for _ in range(6):  # 30 s of subagents with hook traffic
        ctrl.handle_agent_event(AgentEvent.BUSY, "s")
        frames += _frames(ctrl, 5.0, win)
    states = [f.state for f in frames]
    first = states.index(PetState.JUGGLE)
    assert first * DT <= CFG.juggle_lead_s  # Within the current segment, right after the intro
    assert set(states) == {PetState.TYPING, PetState.JUGGLE}  # Alternating, no breaks
    switches = sum(1 for a, b in zip(states, states[1:]) if a != b)
    assert switches >= 3
    assert all(f.animation_name == "juggle" and not f.flip_x for f in frames if f.state == PetState.JUGGLE)
    back = [f for f, prev in zip(frames[1:], states) if prev == PetState.JUGGLE and f.state == PetState.TYPING]
    assert back and all(f.animation_name == "typing_loop" for f in back)

    while ctrl.state != PetState.JUGGLE:
        ctrl.tick(DT, win)
    ctrl.handle_agent_event(AgentEvent.SUBAGENT_STOP, "s")
    assert ctrl.tick(DT, win).state == PetState.JUGGLE  # One still running
    ctrl.handle_agent_event(AgentEvent.SUBAGENT_STOP, "s")
    frame = ctrl.tick(DT, win)
    assert frame.state == PetState.TYPING and frame.animation_name == "typing_loop"
    if where == "perch":
        assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)


def test_done_while_juggling_puts_the_laptop_away_and_celebrates() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.SUBAGENT_START, "s")
    states = _states(ctrl, 3.0)
    assert states[-1] == PetState.JUGGLE  # Already running at the start: intro, then juggle
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    assert ctrl.state == PetState.PUT_AWAY_LAPTOP
    assert ctrl.agent.subagents(ctrl.clock) == 0
    assert PetState.CELEBRATE in _states(ctrl, 3.0)


# --- Molting --------------------------------------------------------------------------------------


@pytest.mark.parametrize("facing", [1, -1])
def test_compact_molts_and_leaves_the_shell_behind(facing: int) -> None:
    ctrl = _floor()
    ctrl.facing = facing
    x0, y0 = ctrl.body.x, ctrl.body.y
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    assert ctrl.state == PetState.MOLT
    assert ctrl.poke() is False and ctrl.pet() is False and ctrl.flail() is False  # Let it finish
    frames = _frames(ctrl, ClawdletController.MOLT_DURATION_S + 0.1)
    molt = [f for f in frames if f.state == PetState.MOLT]
    assert len(molt) == pytest.approx(ClawdletController.MOLT_DURATION_S / DT, abs=2)
    assert all(f.animation_name == "molt" and f.flip_x == (facing < 0) and f.shell is None for f in molt)
    assert all(f.x == round(x0) for f in molt)  # The strip itself draws the step-out
    after = frames[len(molt)]
    assert after.state == PetState.IDLE  # Not busy: back to rest
    assert after.x == round(x0 + facing * SHIFT_PX)
    assert after.shell is not None
    assert (after.shell.x, after.shell.y) == (round(x0), round(y0))
    assert after.shell.animation_name == "molt_shell" and after.shell.flip_x == (facing < 0)

    shells: list[ProjectileRenderState | None] = [f.shell for f in frames[len(molt):]]
    shells += [f.shell for f in _frames(ctrl, 4.5)]
    present = [sh for sh in shells if sh is not None]
    assert len(present) * DT == pytest.approx(ClawdletController.SHELL_LINGER_S, abs=3 * DT)
    assert all((sh.x, sh.y) == (round(x0), round(y0)) for sh in present)
    assert present[0].elapsed_s == 0.0 and present[-1].elapsed_s == pytest.approx(ClawdletController.SHELL_STRIP_S, abs=3 * DT)
    assert shells[-1] is None


def test_molt_mid_work_returns_to_typing() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert _states(ctrl, 0.5)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    assert ctrl.state == PetState.MOLT
    states = _states(ctrl, ClawdletController.MOLT_DURATION_S + 0.2)
    assert states[-1] == PetState.TYPING
    assert "s" in ctrl.agent.sessions  # Compaction keeps the turn running


def test_molt_on_the_title_bar_leaves_a_shell_that_rides_the_window() -> None:
    ctrl = _perched()
    x0 = ctrl.body.x
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    _frames(ctrl, ClawdletController.MOLT_DURATION_S + DT, SNAP)
    assert ctrl.state == PetState.PERCH_CLAUDE
    assert ctrl.body.x == pytest.approx(x0 + SHIFT_PX)
    assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)
    moved = WindowSnapshot(hwnd=SNAP.hwnd, rect=Rect(340.0, 400.0, 1240.0, 820.0))
    frame = ctrl.tick(DT, moved)
    assert frame.shell is not None and (frame.shell.x, frame.shell.y) == (round(x0 + 40.0), round(400.0 - 128.0))
    frame = ctrl.tick(DT, WindowSnapshot(hwnd=SNAP.hwnd, rect=moved.rect, minimized=True))
    assert frame.shell is None  # The window went away and took the shell with it


def test_alert_cutting_a_molt_short_still_sheds_once_split() -> None:
    ctrl = _floor()
    x0 = ctrl.body.x
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    _frames(ctrl, 0.4)
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")  # Before frame 4: nothing shed yet
    assert ctrl.state == PetState.ALERT and ctrl.tick(DT, None).shell is None
    assert ctrl.body.x == x0
    ctrl._set_state(PetState.IDLE)
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    _frames(ctrl, 1.0)
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")  # After frame 4: the shell is out
    frame = ctrl.tick(DT, None)
    assert frame.state == PetState.ALERT and frame.shell is not None
    assert ctrl.body.x == pytest.approx(x0 + SHIFT_PX)


def test_compact_ignored_while_airborne_or_alerting() -> None:
    ctrl = _floor()
    ctrl.hop()
    ctrl.tick(DT, None)
    ctrl.handle_agent_event(AgentEvent.COMPACT, "s")
    assert ctrl.state == PetState.FALLING
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "a")
    ctrl.handle_agent_event(AgentEvent.COMPACT, "b")
    assert ctrl.state == PetState.ALERT


# --- Alert escalation -----------------------------------------------------------------------------


def _alert_names(ctrl: ClawdletController, win: WindowSnapshot | None) -> dict[float, tuple[PetState, str]]:
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    out: dict[float, tuple[PetState, str]] = {}
    checkpoints = (9.9, 10.5, 11.9, 12.5, 15.9, 16.5)
    for i in range(1, int(round(17.0 / DT)) + 1):
        frame = ctrl.tick(DT, win)
        t = round(i * DT, 4)
        assert frame.state == PetState.ALERT
        for c in checkpoints:
            if abs(t - c) < DT / 2:
                out[c] = (frame.state, frame.animation_name)
    return out


@pytest.mark.parametrize(("where", "wave"), [("floor", "alert_wave"), ("perch", "alert_tap")])
def test_unanswered_alert_escalates_to_waving(where: str, wave: str) -> None:
    ctrl = _floor() if where == "floor" else _perched()
    win = None if where == "floor" else SNAP
    y0 = ctrl.body.y
    names = _alert_names(ctrl, win)
    assert names[9.9] == (PetState.ALERT, "alert")
    assert names[10.5] == (PetState.ALERT, wave)
    assert names[11.9] == (PetState.ALERT, wave)
    assert names[12.5] == (PetState.ALERT, "alert")  # 2 s wave, then 4 s plain '!'
    assert names[15.9] == (PetState.ALERT, "alert")
    assert names[16.5] == (PetState.ALERT, wave)
    assert ctrl.body.y == y0 and ctrl.body.grounded  # The hop is drawn in the sprite only
    assert ctrl.poke() is True and ctrl.state == PetState.POKED  # Still cleared by a click


# --- Error escalation -----------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["floor", "perch"])
def test_error_streak_escalates_confused_sweat_sad(where: str) -> None:
    ctrl = _floor() if where == "floor" else _perched()
    win = None if where == "floor" else SNAP
    rest = PetState.IDLE if where == "floor" else PetState.PERCH_CLAUDE
    expected = [
        (PetState.CONFUSED, ClawdletController.CONFUSED_DURATION_S, "confused"),
        (PetState.ERROR_SWEAT, ClawdletController.ERROR_SWEAT_DURATION_S, "error_sweat"),
        (PetState.SAD, ClawdletController.SAD_DURATION_S, "sad"),
        (PetState.SAD, ClawdletController.SAD_DURATION_S, "sad"),
    ]
    for state, duration, anim in expected:
        ctrl.handle_agent_event(AgentEvent.ERROR, "s")
        frames = _frames(ctrl, duration + 0.2, win)
        played = [f for f in frames if f.state == state]
        assert frames[0].state == state and frames[0].animation_name == anim
        assert len(played) == pytest.approx(duration / DT, abs=2)  # No fallback cut it short
        assert frames[-1].state in (rest, PetState.TYPING)
        if where == "perch":
            assert ctrl.body.bottom == pytest.approx(WIN_RECT.top)


def test_error_streak_expires_resets_on_done_and_failed_goes_straight_to_sad() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")
    assert ctrl.state == PetState.CONFUSED
    _states(ctrl, 2.0)
    ctrl._clock += ClawdletController.ERROR_STREAK_WINDOW_S + 1.0
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")
    assert ctrl.state == PetState.CONFUSED  # Too long ago: a new streak
    _states(ctrl, 2.0)
    ctrl.handle_agent_event(AgentEvent.ERROR, "other")
    assert ctrl.state == PetState.CONFUSED  # Streaks are per session
    _states(ctrl, 2.0)
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")
    assert ctrl.state == PetState.ERROR_SWEAT
    _states(ctrl, 2.0)
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    _states(ctrl, 4.0)
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")
    assert ctrl.state == PetState.CONFUSED  # A finished turn starts over
    _states(ctrl, 2.0)
    ctrl.handle_agent_event(AgentEvent.BUSY, "f")
    _states(ctrl, 0.5)
    ctrl.handle_agent_event(AgentEvent.FAILED, "f")
    states = _states(ctrl, ClawdletController.SAD_DURATION_S + 0.2)
    assert states[0] == PetState.SAD and PetState.CELEBRATE not in states
    assert states.count(PetState.SAD) == pytest.approx(ClawdletController.SAD_DURATION_S / DT, abs=2)


def test_sad_pet_can_be_comforted() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.FAILED, "s")
    assert ctrl.state == PetState.SAD
    assert ctrl.pet() is True and ctrl.state == PetState.PETTED


# --- Sprites --------------------------------------------------------------------------------------


def test_every_state_and_render_override_has_a_sprite() -> None:
    animations = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))["animations"]
    for state in PetState:
        assert state.animation_name in animations, state
    for name in RENDER_OVERRIDE_ANIMATIONS:
        assert name in animations, name
    # Every literal the controller renders by name is listed above
    source = (REPO / "src" / "clawdlet" / "behavior.py").read_text(encoding="utf-8")
    used = set(re.findall(r'(?:animation_name|proj_anim)\s*=\s*"([a-z0-9_]+)"', source))
    for a, b in re.findall(r'(?:return|proj_anim =) "([a-z0-9_]+)" if .* else "([a-z0-9_]+)"', source):
        used |= {a, b}
    assert used and used <= set(RENDER_OVERRIDE_ANIMATIONS)


# --- Overlay prop windows (no display: Tk objects are recorders) ------------------------------------


class _Recorder:
    """Stands in for a Tk widget: records every method call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, *args, *kwargs.items()))

        return record

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _headless_overlay(monkeypatch):
    from clawdlet import overlay as overlay_mod
    from clawdlet.sprites import SpriteSheetBank

    monkeypatch.setattr(overlay_mod.ImageTk, "PhotoImage", lambda image, master=None: ("photo", image))
    ov = overlay_mod.ClawdletOverlay.__new__(overlay_mod.ClawdletOverlay)
    ov.root = _Recorder()
    ov.canvas = _Recorder()
    ov.sprite_bank = SpriteSheetBank(scale=1)
    ov.width = ov.height = 64
    ov.debug = False
    ov.click_through = False
    ov._image_item = 1
    ov._anim_name = "idle_breathe"
    ov._anim_elapsed_s = 0.0
    ov._photo_image = None
    ov._last_pos = None
    ov._last_render_key = None
    made: list = []

    def create(title: str, below_pet: bool = False):
        slot = overlay_mod._PropSlot(_Recorder(), _Recorder(), 7, below_pet)
        made.append((title, slot))
        return slot

    ov._create_prop_window = create
    ov._proj_slot = create("ClawdletProjectile")
    ov._shell_slot = None
    return ov, made


def test_overlay_syncs_prop_windows_and_creates_the_shell_window_lazily(monkeypatch) -> None:
    ov, made = _headless_overlay(monkeypatch)
    base = RenderFrameState(x=10, y=20, state=PetState.IDLE, animation_name="idle_breathe")
    ov.sync_frame(base, DT)
    proj = made[0][1]
    assert len(made) == 1 and not proj.visible and proj.win.calls == []  # Nothing to show

    shell = ProjectileRenderState(x=100, y=200, animation_name="molt_shell", elapsed_s=0.0, flip_x=True)
    ov.sync_frame(RenderFrameState(x=10, y=20, state=PetState.IDLE, animation_name="idle_breathe", shell=shell), DT)
    assert [t for t, _ in made] == ["ClawdletProjectile", "ClawdletShell"]
    slot = made[1][1]
    assert slot.visible and slot.below_pet
    assert slot.win.names() == ["deiconify", "wm_attributes", "geometry"]
    assert ("geometry", "64x64+100+200") in slot.win.calls
    assert "lift" in ov.root.names()  # The pet stays in front of its old shell
    photo = slot.photo
    flipped = ov.sprite_bank.get_frame("molt_shell", 0.0).render_with_squash(flip_x=True)
    assert photo[1].tobytes() == flipped.tobytes()

    ov.sync_frame(RenderFrameState(x=10, y=20, state=PetState.IDLE, animation_name="idle_breathe", shell=shell), DT)
    assert slot.photo is photo and slot.win.names().count("geometry") == 1  # Unchanged: no redraw
    later = ProjectileRenderState(x=100, y=200, animation_name="molt_shell", elapsed_s=2.6, flip_x=True)
    ov.sync_frame(RenderFrameState(x=10, y=20, state=PetState.IDLE, animation_name="idle_breathe", shell=later), DT)
    assert slot.photo is not photo  # Fading frame
    assert not proj.visible and proj.win.calls == []  # The projectile window is untouched

    ov.sync_frame(base, DT)
    assert not slot.visible and slot.win.names()[-1] == "withdraw"
    assert len(made) == 2  # Reused next time

    kicked = ProjectileRenderState(x=5, y=6, animation_name="laptop_spin", elapsed_s=0.1)
    ov.sync_frame(RenderFrameState(x=10, y=20, state=PetState.IDLE, animation_name="idle_breathe", projectile=kicked), DT)
    assert proj.visible and proj.win.names() == ["deiconify", "wm_attributes", "geometry"]
    assert ov.root.names().count("lift") == 1  # Only the shell re-raises the pet
