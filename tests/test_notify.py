"""Notifications: plan reviews, questions, permission prompts and the transcript's "maybe waiting" hint."""

from __future__ import annotations

from dataclasses import replace

import pytest

from clawdlet.agent_events import AgentEvent, AttentionKind
from clawdlet.behavior import ClawdletController, MailState, PetState, WindowSnapshot
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from tests.test_entrance_mail_listen import DT, _floor, _run

A = AgentEvent.ATTENTION
PLAN, QUESTION, PERMISSION, HINT = (
    AttentionKind.PLAN, AttentionKind.QUESTION, AttentionKind.PERMISSION, AttentionKind.HINT,
)
NOTIFY_STATES = {PetState.NOTIFY, PetState.NOTIFY_SEEK, PetState.NOTIFY_WAIT}
# Maximized Claude window (floor tests stay on the floor), in the background / in front
BG = WindowSnapshot(hwnd=0xABC, rect=Rect(0.0, 0.0, 1536.0, 816.0), maximized=True)
FG = replace(BG, foreground=True)
CURSOR = PointerSnapshot(1300.0, 700.0, idle_s=0.5)  # Far to the right of Clawd (x = 500)
ESCALATE_S = 10.0


def _anim(ctrl: ClawdletController, win: WindowSnapshot | None = BG, pointer: PointerSnapshot | None = CURSOR) -> str:
    return ctrl.tick(DT, win, pointer).animation_name


def _notified(kind: AttentionKind = PLAN, session: str = "s", source: str = "hook") -> ClawdletController:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)  # Remember Claude's window
    ctrl.handle_agent_event(A, session, source, kind)
    return ctrl


class _Focuser:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, hwnd: int) -> bool:
        self.calls.append(hwnd)
        return True


# --- Showing the right sign -------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "strip"), [(PLAN, "notify_plan"), (QUESTION, "notify_question"),
                                             (PERMISSION, "notify_permission")])
def test_wait_shows_its_own_sign_at_once(kind, strip) -> None:
    ctrl = _notified(kind)
    assert ctrl.state == PetState.NOTIFY
    assert _anim(ctrl) == strip
    assert "s plan hook" in _notified(PLAN).describe_notify()


def test_unanswered_sign_is_carried_to_the_cursor_and_follows_it() -> None:
    ctrl = _notified(QUESTION)
    states = _run(ctrl, ESCALATE_S - 0.3, BG, CURSOR)
    assert set(states) == {PetState.NOTIFY}
    states = _run(ctrl, 1.0, BG, CURSOR)
    assert PetState.NOTIFY_SEEK in states
    assert _anim(ctrl) == "walk_notify_question"
    states = _run(ctrl, 20.0, BG, CURSOR, until=lambda c: c.state == PetState.NOTIFY_WAIT)
    assert states[-1] == PetState.NOTIFY_WAIT
    assert _anim(ctrl) == "notify_question"
    # The cursor moves far away: carry the sign over again (no new 10 s wait)
    away = PointerSnapshot(100.0, 700.0, idle_s=0.5)
    assert PetState.NOTIFY_SEEK in _run(ctrl, 0.2, BG, away)


def test_no_escalation_while_claude_desktop_is_in_front() -> None:
    ctrl = _notified(PERMISSION)
    assert set(_run(ctrl, 60.0, FG, CURSOR)) == {PetState.NOTIFY}
    ctrl = _notified(PERMISSION)
    _run(ctrl, 15.0, FG, CURSOR)
    assert set(_run(ctrl, ESCALATE_S - 0.3, BG, CURSOR)) == {PetState.NOTIFY}  # Timer starts on leaving
    assert PetState.NOTIFY_SEEK in _run(ctrl, 1.0, BG, CURSOR)


def test_switching_to_claude_while_carrying_the_sign_stops_following() -> None:
    ctrl = _notified(PLAN)
    _run(ctrl, ESCALATE_S + 1.0, BG, CURSOR)
    assert ctrl.state == PetState.NOTIFY_SEEK
    _run(ctrl, 0.1, FG, CURSOR)
    assert ctrl.state == PetState.NOTIFY
    assert ctrl._notify["s"].acknowledged is False


# --- Clicking ---------------------------------------------------------------------------------------


def test_click_brings_claude_forward_and_the_same_wait_stays_quiet() -> None:
    ctrl = _notified(PLAN)
    focus = _Focuser()
    ctrl.window_focuser = focus
    assert ctrl.poke() is True
    assert ctrl.state == PetState.POKED
    assert focus.calls == [BG.hwnd]
    assert ctrl._notify["s"].acknowledged is True
    assert "focus 0xabc -> True" in ctrl.debug_events[-1]
    # Same wait, ExitPlanMode's own permission prompt: no second showing
    ctrl.handle_agent_event(A, "s", "hook", PERMISSION)
    assert not set(_run(ctrl, 3.0, BG, CURSOR)) & NOTIFY_STATES
    assert ctrl._notify["s"].kind == PLAN


def test_click_without_a_known_claude_window_still_acknowledges() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(A, "s", "hook", QUESTION)
    focus = _Focuser()
    ctrl.window_focuser = focus
    assert ctrl.poke() is True
    assert focus.calls == []
    assert ctrl._notify["s"].acknowledged is True
    assert "no Claude window" in ctrl.debug_events[-1]


def test_flail_takes_the_sign_and_petting_is_ignored() -> None:
    ctrl = _notified(PLAN)
    assert ctrl.pet() is False and ctrl.state == PetState.NOTIFY
    assert ctrl.flail() is True and ctrl.state == PetState.POKED


# --- Merging one session's wait ---------------------------------------------------------------------


def test_plan_or_question_wins_over_a_permission_prompt_in_either_order() -> None:
    ctrl = _notified(PLAN)
    ctrl.handle_agent_event(A, "s", "hook", PERMISSION)
    _run(ctrl, 0.1, BG, CURSOR)
    assert _anim(ctrl) == "notify_plan"

    ctrl = _notified(PERMISSION)
    ctrl.handle_agent_event(A, "s", "transcript", PLAN)  # The transcript spots the plan afterwards
    _run(ctrl, 0.1, BG, CURSOR)
    assert _anim(ctrl) == "notify_plan"
    assert ctrl._notify["s"].origin == "hook"


def test_acknowledged_hint_turning_into_a_permission_shows_again() -> None:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)
    ctrl.handle_agent_event(AgentEvent.HINT, "s", "transcript", HINT)
    assert ctrl.state == PetState.NOTIFY and _anim(ctrl) == "notify_hint"
    ctrl.poke()
    ctrl.handle_agent_event(A, "s", "hook", PERMISSION)
    assert ctrl.state == PetState.NOTIFY
    assert _anim(ctrl) == "notify_permission"


def test_hint_upgraded_later_restarts_the_escalation_timer() -> None:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)
    ctrl.handle_agent_event(AgentEvent.HINT, "s", "transcript", HINT)
    _run(ctrl, 12.0, BG, CURSOR)
    assert ctrl.state == PetState.NOTIFY  # A hint never escalates
    ctrl.handle_agent_event(A, "s", "hook", PERMISSION)
    assert set(_run(ctrl, ESCALATE_S - 0.3, BG, CURSOR)) == {PetState.NOTIFY}
    assert PetState.NOTIFY_SEEK in _run(ctrl, 1.0, BG, CURSOR)


# --- The "maybe waiting" hint -----------------------------------------------------------------------


def test_hint_while_typing_keeps_typing_with_a_bubble() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "t", "transcript")
    _run(ctrl, 1.0, BG, CURSOR)
    assert ctrl.state == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.HINT, "t", "transcript", HINT)
    assert set(_run(ctrl, 2.0, BG, CURSOR)) == {PetState.TYPING}
    assert _anim(ctrl) == "typing_hint"
    focus = _Focuser()
    ctrl.window_focuser = focus
    assert ctrl.poke() is True
    assert focus.calls == [BG.hwnd]
    assert ctrl.state == PetState.TYPING
    assert _anim(ctrl) != "typing_hint"


def test_hint_at_rest_never_walks_and_fades_after_its_timeout() -> None:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)
    ctrl.handle_agent_event(AgentEvent.HINT, "t", "transcript", HINT)
    states = _run(ctrl, ClawdletController.NOTIFY_HINT_TIMEOUT_S - 0.5, BG, CURSOR)
    assert set(states) == {PetState.NOTIFY}
    assert _run(ctrl, 1.0, BG, CURSOR)[-1] not in NOTIFY_STATES
    assert ctrl._notify == {}


@pytest.mark.parametrize("state", [PetState.ALERT, PetState.SLEEP])
def test_hint_does_not_replace_an_alert_or_wake_clawd(state) -> None:
    ctrl = _floor()
    ctrl._set_state(state)
    ctrl.handle_agent_event(AgentEvent.HINT, "t", "transcript", HINT)
    assert ctrl.state == state


def test_clearing_a_hint_while_typing_keeps_typing() -> None:
    ctrl = _floor()
    for s in ("a", "b"):
        ctrl.handle_agent_event(AgentEvent.BUSY, s, "transcript")
        ctrl.handle_agent_event(AgentEvent.HINT, s, "transcript", HINT)
    _run(ctrl, 1.0, BG, CURSOR)
    assert ctrl.state == PetState.TYPING and _anim(ctrl) == "typing_hint"
    ctrl.handle_agent_event(AgentEvent.BUSY, "a", "transcript")
    assert ctrl.state == PetState.TYPING and _anim(ctrl) == "typing_hint"  # b still hints
    ctrl.handle_agent_event(AgentEvent.BUSY, "b", "transcript")
    assert ctrl.state == PetState.TYPING and _anim(ctrl) != "typing_hint"


# --- Entering, deferring, clearing ------------------------------------------------------------------


def test_real_wait_puts_the_laptop_away_first() -> None:
    ctrl = _floor()
    for s in ("a", "b"):
        ctrl.handle_agent_event(AgentEvent.BUSY, s, "hook")
    _run(ctrl, 1.0, BG, CURSOR)
    assert ctrl.state == PetState.TYPING
    ctrl.handle_agent_event(A, "a", "hook", PERMISSION)  # b keeps Claude busy
    states = _run(ctrl, 4.0, BG, CURSOR, until=lambda c: c.state == PetState.NOTIFY)
    assert PetState.PUT_AWAY_LAPTOP in states and states[-1] == PetState.NOTIFY


def test_airborne_wait_is_shown_after_landing_and_beats_a_letter() -> None:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)
    ctrl._mail = MailState("m", ctrl.clock)
    ctrl.hop()
    ctrl.tick(DT, BG, CURSOR)
    ctrl.handle_agent_event(A, "s", "hook", QUESTION)
    assert ctrl.state == PetState.FALLING
    states = _run(ctrl, 5.0, BG, CURSOR, until=lambda c: c.state in NOTIFY_STATES | {PetState.MAIL_CARRY})
    assert states[-1] == PetState.NOTIFY


def test_session_carrying_on_clears_its_sign_and_the_next_one_shows_straight_away() -> None:
    ctrl = _notified(QUESTION, "q1")
    ctrl.handle_agent_event(A, "p1", "hook", PERMISSION)
    _run(ctrl, 0.1, BG, CURSOR)
    assert _anim(ctrl) == "notify_permission"  # Higher priority
    ctrl.handle_agent_event(AgentEvent.BUSY, "p1", "hook")
    assert _run(ctrl, 0.1, BG, CURSOR) == [PetState.NOTIFY] * 6
    assert _anim(ctrl) == "notify_question"
    ctrl.handle_agent_event(AgentEvent.DONE, "q1", "hook")
    assert not set(_run(ctrl, 0.5, BG, CURSOR)) & NOTIFY_STATES


@pytest.mark.parametrize("event", [AgentEvent.BUSY, AgentEvent.DONE, AgentEvent.FAILED,
                                   AgentEvent.INTERRUPTED, AgentEvent.END, AgentEvent.ERROR])
def test_any_follow_up_ends_the_wait(event) -> None:
    ctrl = _notified(PLAN)
    ctrl.handle_agent_event(event, "s", "hook")
    assert "s" not in ctrl._notify


def test_waits_time_out() -> None:
    ctrl = _notified(PERMISSION)
    ctrl._clock += ClawdletController.NOTIFY_TIMEOUT_S
    assert not set(_run(ctrl, 0.5, BG, CURSOR)) & NOTIFY_STATES
    assert ctrl._notify == {}


# --- Hooks and transcript together ------------------------------------------------------------------


def test_hook_session_takes_the_transcripts_plan_and_its_busy_only_clears_that() -> None:
    ctrl = _floor()
    _run(ctrl, 0.1, BG, CURSOR)
    ctrl.handle_agent_event(AgentEvent.BUSY, "h", "hook")
    ctrl.handle_agent_event(A, "h", "transcript", PLAN)  # No PreToolUse hook announced it
    assert ctrl._notify["h"].origin == "transcript"
    ctrl.handle_agent_event(AgentEvent.BUSY, "h", "transcript")
    assert "h" not in ctrl._notify

    ctrl.handle_agent_event(A, "h", "hook", PERMISSION)
    ctrl.handle_agent_event(AgentEvent.BUSY, "h", "transcript")  # A lagging read: not the answer
    assert "h" in ctrl._notify
    ctrl._clock += 3.0
    ctrl.handle_agent_event(AgentEvent.BUSY, "h", "transcript", desktop=True)  # Desktop: no reject hook
    assert "h" not in ctrl._notify


def test_transcript_hint_reaches_desktop_hook_sessions_only() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "cli", "hook")
    ctrl.handle_agent_event(AgentEvent.HINT, "cli", "transcript", HINT)
    assert "cli" not in ctrl._notify
    ctrl.handle_agent_event(AgentEvent.BUSY, "desk", "hook")
    ctrl.handle_agent_event(AgentEvent.HINT, "desk", "transcript", HINT, desktop=True)
    assert ctrl._notify["desk"].kind == HINT


# --- Switch and the plain '!' -----------------------------------------------------------------------


def test_switching_notifications_off() -> None:
    ctrl = _notified(PLAN)
    ctrl.set_notify_enabled(False)
    assert ctrl._notify == {} and ctrl.state not in NOTIFY_STATES
    ctrl.handle_agent_event(A, "t", "transcript", QUESTION)
    ctrl.handle_agent_event(AgentEvent.HINT, "t", "transcript", HINT)
    assert ctrl._notify == {} and ctrl.state not in NOTIFY_STATES
    ctrl.handle_agent_event(A, "h", "hook", PERMISSION)  # A hook's prompt: the old '!' alert
    assert ctrl.state == PetState.ALERT


def test_switching_off_while_typing_removes_the_bubble() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "t", "transcript")
    ctrl.handle_agent_event(AgentEvent.HINT, "t", "transcript", HINT)
    _run(ctrl, 1.0, BG, CURSOR)
    assert _anim(ctrl) == "typing_hint"
    ctrl.set_notify_enabled(False)
    assert ctrl.state == PetState.TYPING and _anim(ctrl) != "typing_hint"


def test_plain_alert_does_not_interrupt_a_sign() -> None:
    ctrl = _notified(PLAN)
    ctrl.handle_agent_event(A, "other", "hook")  # Any other notification
    assert ctrl.state == PetState.NOTIFY
    ctrl = _floor()
    ctrl.handle_agent_event(A, "x", "hook")  # No kind: the plain '!' as before
    assert ctrl.state == PetState.ALERT


# --- App, tray, sprites -----------------------------------------------------------------------------


def test_no_notify_flag_and_tray_toggle_keep_controller_and_watcher_in_step(tmp_path) -> None:
    import queue

    from clawdlet.app import dispatch_tray_action, parse_args, set_notify_enabled
    from clawdlet.transcript_watch import TranscriptWatcher
    from clawdlet.tray import CMD_TOGGLE_NOTIFY, TrayMenuState

    assert parse_args([]).no_notify is False and parse_args(["--no-notify"]).no_notify is True
    ctrl = _floor()
    watcher = TranscriptWatcher(events=queue.Queue(), projects_dir=tmp_path)
    set_notify_enabled(ctrl, watcher, False)  # What main() does for --no-notify
    assert ctrl.notify_enabled is False and watcher.hint_enabled is False
    tray = TrayMenuState(notify_enabled=False)
    dispatch_tray_action(CMD_TOGGLE_NOTIFY, ctrl, None, tray, None, transcript_watcher=watcher)  # type: ignore[arg-type]
    assert ctrl.notify_enabled and watcher.hint_enabled and tray.notify_enabled
    dispatch_tray_action(CMD_TOGGLE_NOTIFY, ctrl, None, tray, None, transcript_watcher=watcher)  # type: ignore[arg-type]
    assert not (ctrl.notify_enabled or watcher.hint_enabled or tray.notify_enabled)


@pytest.mark.parametrize("surface", ["floor", "perch"])
@pytest.mark.parametrize("state", ["NOTIFY", "NOTIFY_SEEK", "NOTIFY_WAIT"])
def test_notify_states_never_hit_the_tick_fallback(state: str, surface: str) -> None:
    from tests.test_entrance_mail_listen import SNAP, _perched

    ctrl, win = (_floor(), None) if surface == "floor" else (_perched(), SNAP)
    ctrl.handle_agent_event(A, "s", "hook", PERMISSION)
    ctrl.state = PetState[state]
    _run(ctrl, 15.0, win, PointerSnapshot(900.0, 300.0, idle_s=0.2))


def test_manifest_has_every_notify_animation() -> None:
    import json

    from tests.test_entrance_mail_listen import REPO

    animations = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))["animations"]
    for name in ("notify_plan", "notify_question", "notify_permission", "notify_hint", "typing_hint",
                 "walk_notify_plan", "walk_notify_question", "walk_notify_permission"):
        assert name in animations, name
    assert animations["typing_hint"]["frames"] == animations["typing_loop"]["frames"]
