"""Milestone 5 step D: Claude Code hook mapping, localhost receiver, and agent-driven reactions."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import queue

import pytest

from clawdlet.agent_events import (
    DEFAULT_HOOK_EVENTS,
    HARD_MAX_BODY_BYTES,
    MAX_BODY_BYTES,
    AgentActivity,
    AgentEvent,
    AgentSignal,
    AttentionKind,
    HookServer,
    build_hook_settings,
    load_or_create_token,
    map_hook_payload,
    scan_payload_head,
)
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.physics import PhysicsBody
from clawdlet.tools.send_hook_event import send_hook_event

DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
TOKEN = "test-token-not-secret"


# --- Mapping & activity ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("UserPromptSubmit", AgentEvent.BUSY),
        ("PreToolUse", AgentEvent.BUSY),
        ("PostToolUse", AgentEvent.BUSY),
        ("SubagentStart", AgentEvent.SUBAGENT_START),
        ("SubagentStop", AgentEvent.SUBAGENT_STOP),
        ("PreCompact", AgentEvent.COMPACT),
        ("PostToolUseFailure", AgentEvent.ERROR),
        ("Notification", AgentEvent.ATTENTION),
        ("PermissionRequest", AgentEvent.ATTENTION),
        ("Stop", AgentEvent.DONE),
        ("StopFailure", AgentEvent.FAILED),
        ("SessionEnd", AgentEvent.END),
    ],
)
def test_map_hook_payload_known_events(name: str, event: AgentEvent) -> None:
    kind = {"Notification": AttentionKind.GENERIC, "PermissionRequest": AttentionKind.PERMISSION}.get(name)
    assert map_hook_payload({"hook_event_name": name, "session_id": "s1"}) == (event, "s1", kind)


def test_map_hook_payload_rejects_unknown_and_malformed() -> None:
    assert map_hook_payload({"hook_event_name": "SessionStart"}) is None
    assert map_hook_payload({"hook_event_name": 42}) is None
    assert map_hook_payload(["Stop"]) is None
    assert map_hook_payload({"hook_event_name": "Stop"}) == (AgentEvent.DONE, "default", None)


@pytest.mark.parametrize(
    ("notification_type", "expected"),
    [
        ("permission_prompt", AttentionKind.PERMISSION),
        ("elicitation_dialog", AttentionKind.QUESTION),
        ("something_new", AttentionKind.GENERIC),  # unknown types keep the old '!' alert
        (None, AttentionKind.GENERIC),
        ("idle_prompt", None),
        ("auth_success", None),
    ],
)
def test_notification_type_filters_quiet_notifications(notification_type, expected) -> None:
    payload = {"hook_event_name": "Notification", "session_id": "s1", "message": "stub"}
    if notification_type is not None:
        payload["notification_type"] = notification_type
    assert map_hook_payload(payload) == (None if expected is None else (AgentEvent.ATTENTION, "s1", expected))
    # Only Notification is filtered; the field means nothing on other events
    assert map_hook_payload({"hook_event_name": "Stop", "notification_type": "idle_prompt"}) == (
        AgentEvent.DONE, "default", None)


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("ExitPlanMode", (AgentEvent.ATTENTION, "s1", AttentionKind.PLAN)),
        ("AskUserQuestion", (AgentEvent.ATTENTION, "s1", AttentionKind.QUESTION)),
        ("Bash", (AgentEvent.BUSY, "s1", None)),
        (None, (AgentEvent.BUSY, "s1", None)),
    ],
)
def test_interactive_tools_map_to_notifications(tool_name, expected) -> None:
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_input": {"plan": "x"}}
    if tool_name is not None:
        payload["tool_name"] = tool_name
    assert map_hook_payload(payload) == expected
    # Only PreToolUse turns into a notification: PostToolUse means the user already answered
    payload["hook_event_name"] = "PostToolUse"
    assert map_hook_payload(payload) == (AgentEvent.BUSY, "s1", None)


def test_agent_activity_busy_lifecycle_and_expiry() -> None:
    act = AgentActivity(busy_expiry_s=180.0)
    assert act.busy(0.0) is False
    assert act.note(AgentEvent.BUSY, "a", 0.0) is False
    assert act.busy(10.0) is True
    act.note(AgentEvent.BUSY, "b", 20.0)
    assert act.note(AgentEvent.DONE, "a", 30.0) is True
    assert act.busy(30.0) is True  # session b still working
    assert act.busy(201.0) is False  # b silent for > 180 s
    act.note(AgentEvent.BUSY, "c", 300.0)
    act.note(AgentEvent.FAILED, "c", 301.0)
    assert act.busy(301.0) is False


def test_agent_activity_waiting_sessions_are_not_busy() -> None:
    act = AgentActivity(busy_expiry_s=180.0)
    act.note(AgentEvent.BUSY, "a", 0.0)
    assert act.note(AgentEvent.ATTENTION, "a", 1.0) is True  # permission prompt: stop typing
    assert act.busy(2.0) is False
    assert act.note(AgentEvent.BUSY, "a", 3.0) is False  # approved: back to work
    assert act.busy(3.0) is True
    act.note(AgentEvent.WAITING, "a", 4.0, "transcript")
    assert act.busy(4.0) is False
    assert act.sessions["a"].phase == "waiting"
    assert act.busy(500.0) is False and act.sessions == {}  # waiting sessions expire too

    act.note(AgentEvent.BUSY, "b", 600.0)
    act.note(AgentEvent.INTERRUPTED, "b", 601.0)
    assert "b" not in act.sessions
    act.note(AgentEvent.ATTENTION, "c", 602.0)
    act.note(AgentEvent.END, "c", 603.0)
    assert act.sessions == {}


def test_agent_activity_describe() -> None:
    act = AgentActivity(busy_expiry_s=180.0)
    assert act.describe(0.0) == "busy=[] waiting=[]"
    act.note(AgentEvent.BUSY, "3f2a91c0-aaaa-bbbb", 0.0)
    act.note(AgentEvent.WAITING, "77e0d1b2-cccc", 40.0, "transcript")
    act.note(AgentEvent.SUBAGENT_START, "subby", 41.0)
    assert act.describe(42.0) == (
        "busy=[3f2a91c0 hook 42s, subby hook 1s sub=1] waiting=[77e0d1b2 transcript 2s]"
    )


def test_hook_settings_snippet_shape() -> None:
    settings = build_hook_settings(47821, TOKEN)["hooks"]
    assert tuple(settings) == DEFAULT_HOOK_EVENTS
    assert "PermissionRequest" not in settings
    handler = settings["Stop"][0]["hooks"][0]
    assert handler == {
        "type": "http",
        "url": "http://127.0.0.1:47821/hook",
        "timeout": 2,
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }
    assert settings["PreToolUse"][0]["matcher"] == "*"
    for name in ("Stop", "SubagentStart", "SubagentStop", "PreCompact"):
        assert "matcher" not in settings[name][0], name
    assert {"SubagentStop", "PreCompact"} <= set(settings)


def test_agent_activity_counts_subagents_per_session() -> None:
    act = AgentActivity(busy_expiry_s=90.0)
    act.note(AgentEvent.BUSY, "a", 0.0)
    act.note(AgentEvent.SUBAGENT_START, "a", 1.0)
    act.note(AgentEvent.SUBAGENT_START, "a", 2.0)
    act.note(AgentEvent.SUBAGENT_START, "b", 3.0)  # Counts as busy on its own
    assert act.busy(3.0) and act.subagents(3.0) == 3
    act.note(AgentEvent.SUBAGENT_STOP, "a", 4.0)
    assert act.subagents(4.0) == 2
    for _ in range(3):  # Never below zero
        act.note(AgentEvent.SUBAGENT_STOP, "b", 5.0)
    assert act.sessions["b"].subagents == 0 and act.subagents(5.0) == 1
    act.note(AgentEvent.WAITING, "a", 6.0)  # Waiting on the user: its subagents do not count
    assert act.subagents(6.0) == 0
    act.note(AgentEvent.BUSY, "a", 7.0)
    assert act.subagents(7.0) == 1
    for event in (AgentEvent.DONE, AgentEvent.FAILED, AgentEvent.END, AgentEvent.INTERRUPTED):
        act.note(AgentEvent.SUBAGENT_START, "c", 8.0)
        act.note(event, "c", 8.5)
        assert "c" not in act.sessions, event
        act.note(AgentEvent.SUBAGENT_STOP, "c", 9.0)  # Late stop after the turn: no phantom session
        assert "c" not in act.sessions, event
    assert act.subagents(7.0 + 91.0) == 0  # Expired with its session


def test_token_is_created_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "Clawdlet" / "hook_token"
    first = load_or_create_token(path)
    assert len(first) >= 24
    assert load_or_create_token(path) == first


# --- HTTP receiver ---------------------------------------------------------------------------------


@pytest.fixture
def server():
    events: queue.Queue = queue.Queue()
    srv = HookServer(token=TOKEN, events=events, port=0)
    assert srv.start()
    yield srv, events
    srv.stop()


def _post(port: int, body: bytes, token: str | None = TOKEN, path: str = "/hook",
          length: int | None = None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.putrequest("POST", path)
    if token is not None:
        conn.putheader("Authorization", f"Bearer {token}")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(len(body) if length is None else length))
    conn.endheaders()
    conn.send(body)
    status = conn.getresponse().status
    conn.close()
    return status


def test_server_accepts_authorized_event(server) -> None:
    srv, events = server
    body = json.dumps({"hook_event_name": "Stop", "session_id": "s9", "tool_input": {"x": 1}}).encode()
    assert _post(srv.port, body) == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.DONE, "s9", "hook")


def test_server_ignores_unknown_events_with_200(server) -> None:
    srv, events = server
    assert _post(srv.port, b'{"hook_event_name": "SessionStart"}') == 200
    body = b'{"hook_event_name": "Notification", "notification_type": "idle_prompt", "message": "x"}'
    assert _post(srv.port, body) == 200
    assert events.empty()


def test_server_rejects_bad_requests(server) -> None:
    srv, events = server
    assert _post(srv.port, b'{"hook_event_name": "Stop"}', token=None) == 401
    assert _post(srv.port, b'{"hook_event_name": "Stop"}', token="wrong") == 401
    # Declared size beyond the hard cap: rejected before any body byte is read
    assert _post(srv.port, b"", length=HARD_MAX_BODY_BYTES + 1) == 413
    assert _post(srv.port, b"not json") == 400
    assert _post(srv.port, b"[1, 2]") == 400
    assert _post(srv.port, b'{"hook_event_name": "Stop"}', path="/other") == 404
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=3)
    conn.request("GET", "/hook")
    assert conn.getresponse().status == 405
    conn.close()
    assert events.empty()


def test_oversized_tool_payload_is_drained_and_still_mapped(server) -> None:
    srv, events = server
    payload = {
        "session_id": "big",
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_response": {"content": "x" * (MAX_BODY_BYTES * 3)},
    }
    assert _post(srv.port, json.dumps(payload).encode()) == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.BUSY, "big", "hook")


def test_oversized_payloads_never_guess_the_tool(server) -> None:
    """Above MAX_BODY_BYTES only the head is scanned and ``tool_name`` is never read from it."""
    srv, events = server
    # A: a normal tool whose huge input (placed first) contains the interactive tool's name
    decoy = '"tool_name": "ExitPlanMode", '
    body = (
        '{"session_id": "a", "hook_event_name": "PreToolUse", "tool_input": {"content": "'
        + decoy.replace('"', '\\"') + "x" * (MAX_BODY_BYTES * 2) + '"}, "tool_name": "Write"}'
    )
    assert _post(srv.port, body.encode()) == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.BUSY, "a", "hook")
    # B: a huge ExitPlanMode: plain BUSY (the transcript watcher spots the plan instead)
    payload = {"session_id": "b", "hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode",
               "tool_input": {"plan": "x" * (MAX_BODY_BYTES * 2)}}
    assert _post(srv.port, json.dumps(payload).encode()) == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.BUSY, "b", "hook")
    # C: a huge PermissionRequest is recognised by its event name alone
    payload = {"session_id": "c", "hook_event_name": "PermissionRequest", "tool_name": "Write",
               "tool_input": {"content": "x" * (MAX_BODY_BYTES * 2)}}
    assert _post(srv.port, json.dumps(payload).encode()) == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.ATTENTION, "c", "hook", AttentionKind.PERMISSION)


def test_scan_payload_head_extracts_common_fields() -> None:
    head = b'{"session_id": "abc-1", "hook_event_name":"Stop", "cwd": "C:/x"'
    assert scan_payload_head(head) == {"session_id": "abc-1", "hook_event_name": "Stop"}
    head = b'{"session_id": "n", "hook_event_name": "Notification", "notification_type": "idle_prompt"'
    assert scan_payload_head(head)["notification_type"] == "idle_prompt"
    assert scan_payload_head(b'{"tool_input": "nothing here"') == {}


def test_second_server_on_same_port_fails_gracefully(server) -> None:
    srv, _ = server
    other = HookServer(token=TOKEN, events=queue.Queue(), port=srv.port)
    assert other.start() is False
    assert other.running is False


def test_send_hook_event_tool_round_trip(server) -> None:
    srv, events = server
    assert send_hook_event("Notification", srv.port, TOKEN, "dev") == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.ATTENTION, "dev", "hook", AttentionKind.GENERIC)
    assert send_hook_event("Stop", srv.port, "bad", "dev") == 401


def test_send_hook_event_tool_notification_type(server, monkeypatch) -> None:
    from clawdlet.tools import send_hook_event as tool

    srv, events = server
    monkeypatch.setattr(tool, "read_token", lambda: TOKEN)
    argv = ["Notification", "--port", str(srv.port), "--session", "cli", "--notification-type", "auth_success"]
    assert tool.main(argv) == 0
    assert tool.main(["SubagentStop", "--port", str(srv.port), "--session", "cli"]) == 0
    assert events.get(timeout=2) == AgentSignal(AgentEvent.SUBAGENT_STOP, "cli", "hook")  # auth_success: dropped
    assert send_hook_event("Notification", srv.port, TOKEN, "dev", notification_type="idle_prompt") == 200
    assert send_hook_event("Notification", srv.port, TOKEN, "dev", "permission_prompt") == 200
    assert events.get(timeout=2) == AgentSignal(  # idle_prompt was dropped
        AgentEvent.ATTENTION, "dev", "hook", AttentionKind.PERMISSION)
    assert events.empty()
    assert send_hook_event("PreCompact", srv.port, TOKEN, "dev") == 200
    assert events.get(timeout=2) == AgentSignal(AgentEvent.COMPACT, "dev", "hook")
    assert tool.main(["PreToolUse", "--port", str(srv.port), "--session", "cli", "--tool-name", "ExitPlanMode"]) == 0
    assert events.get(timeout=2) == AgentSignal(AgentEvent.ATTENTION, "cli", "hook", AttentionKind.PLAN)


# --- Controller reactions --------------------------------------------------------------------------


def _floor(mode: ActivityMode = ActivityMode.NORMAL) -> ClawdletController:
    body = PhysicsBody(x=500.0, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=5)
    ctrl.state = PetState.IDLE
    ctrl._decision_duration = 999.0
    return ctrl


def _run(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None = None) -> list[PetState]:
    return [ctrl.tick(DT, win).state for _ in range(int(round(seconds / DT)))]


def test_busy_session_types_then_done_puts_laptop_away_and_celebrates() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert _run(ctrl, 0.5)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    states = _run(ctrl, ClawdletController.PUT_AWAY_DURATION_S + ClawdletController.CELEBRATE_DURATION_S + 0.5)
    put_away = states.index(PetState.PUT_AWAY_LAPTOP)
    celebrate = states.index(PetState.CELEBRATE)
    assert put_away < celebrate
    assert PetState.TYPING not in states[put_away:]
    assert states[-1] == PetState.IDLE


def test_hyper_done_kicks_laptop_before_celebrating() -> None:
    ctrl = _floor(ActivityMode.HYPER)
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    _run(ctrl, 0.3)
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    states = _run(ctrl, 3.5)
    assert states.index(PetState.KICK_LAPTOP) < states.index(PetState.CELEBRATE)


def test_done_without_prior_busy_does_not_celebrate() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.DONE, "s")
    assert PetState.CELEBRATE not in _run(ctrl, 3.0)


def test_done_while_another_session_is_busy_celebrates_then_keeps_typing() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "a")
    ctrl.handle_agent_event(AgentEvent.BUSY, "b")
    _run(ctrl, 0.3)
    ctrl.handle_agent_event(AgentEvent.DONE, "a")
    states = _run(ctrl, ClawdletController.CELEBRATE_DURATION_S + 0.3)
    assert states[0] == PetState.CELEBRATE
    assert states[-1] == PetState.TYPING


def test_tool_failure_sweats_then_resumes_typing() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    _run(ctrl, 0.3)
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")  # First failure in a streak: puzzled
    states = _run(ctrl, ClawdletController.CONFUSED_DURATION_S + 0.3)
    assert states[0] == PetState.CONFUSED
    assert states.count(PetState.CONFUSED) == pytest.approx(ClawdletController.CONFUSED_DURATION_S / DT, abs=2)
    assert states[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")  # Second: nervous sweat
    states = _run(ctrl, ClawdletController.ERROR_SWEAT_DURATION_S + 0.3)
    assert states[0] == PetState.ERROR_SWEAT
    assert states.count(PetState.ERROR_SWEAT) == pytest.approx(ClawdletController.ERROR_SWEAT_DURATION_S / DT, abs=2)
    assert states[-1] == PetState.TYPING


def test_attention_alert_cleared_by_click_followup_or_timeout() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    assert ctrl.state == PetState.ALERT
    assert ctrl.poke() is True
    assert ctrl.state == PetState.POKED

    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    _run(ctrl, 1.0)
    assert ctrl.state == PetState.ALERT
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")  # user approved: Claude continues
    assert _run(ctrl, 0.3)[-1] == PetState.TYPING

    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    ctrl.handle_agent_event(AgentEvent.BUSY, "other")  # another session does not clear it
    states = _run(ctrl, ctrl.interaction_cfg.alert_timeout_s - 0.5)
    assert set(states) == {PetState.ALERT}
    assert _run(ctrl, 1.0)[-1] == PetState.TYPING  # timed out, "other" still busy


def test_attention_wakes_sleeping_pet_and_pops_out_of_peek() -> None:
    ctrl = _floor()
    ctrl._set_state(PetState.SLEEP)
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    assert ctrl.state == PetState.ALERT

    ctrl = _floor()
    ctrl.body.x = WORK_AREA.left
    ctrl.set_peek_mode(True)
    ctrl._head_to_peek_edge()
    assert ctrl.state == PetState.PEEK_EDGE
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    assert ctrl.state == PetState.ALERT


def test_busy_expires_after_silence() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert _run(ctrl, 1.0)[-1] == PetState.TYPING
    ctrl._clock += ctrl.interaction_cfg.agent_busy_expiry_s  # 3 minutes without hook events
    assert PetState.PUT_AWAY_LAPTOP in _run(ctrl, 2.0)


def test_hooks_take_over_from_transcript_for_the_same_session() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s", "transcript")
    assert _run(ctrl, 0.3)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.BUSY, "s", "hook")
    ctrl.handle_agent_event(AgentEvent.DONE, "s", "hook")
    # A lagging transcript reader still thinks the turn is running, then sees it end
    ctrl.handle_agent_event(AgentEvent.BUSY, "s", "transcript")
    ctrl.handle_agent_event(AgentEvent.DONE, "s", "transcript")
    states = _run(ctrl, 5.0)
    assert states.count(PetState.CELEBRATE) == pytest.approx(ClawdletController.CELEBRATE_DURATION_S / DT, abs=2)
    assert PetState.TYPING not in states[states.index(PetState.CELEBRATE):]


def test_transcript_only_session_celebrates_once() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "t", "transcript")
    _run(ctrl, 0.3)
    ctrl.handle_agent_event(AgentEvent.DONE, "t", "transcript")
    ctrl.handle_agent_event(AgentEvent.DONE, "t", "transcript")
    states = _run(ctrl, 4.0)
    first = states.index(PetState.CELEBRATE)
    assert PetState.CELEBRATE not in states[first + int(ClawdletController.CELEBRATE_DURATION_S / DT) + 2:]


def test_agent_events_ignored_while_airborne() -> None:
    ctrl = _floor()
    ctrl.hop()
    ctrl.tick(DT, None)
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")
    ctrl.handle_agent_event(AgentEvent.ERROR, "s")
    assert ctrl.state == PetState.FALLING


def test_attention_stops_typing_without_celebrating() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s")
    assert _run(ctrl, 0.5)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.ATTENTION, "s")  # permission prompt
    assert ctrl.agent.busy(ctrl.clock) is False
    ctrl._set_state(PetState.IDLE)  # acknowledge the alert
    states = _run(ctrl, 2.0)
    assert PetState.TYPING not in states and PetState.CELEBRATE not in states


def test_transcript_interrupt_is_accepted_for_hook_sessions() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "s", "hook")
    assert _run(ctrl, 0.5)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.BUSY, "s", "transcript")  # other transcript events: dropped
    ctrl.handle_agent_event(AgentEvent.INTERRUPTED, "s", "transcript")  # Esc: no Stop hook fires
    assert ctrl.state == PetState.PUT_AWAY_LAPTOP
    assert ctrl.agent.busy(ctrl.clock) is False
    states = _run(ctrl, ClawdletController.PUT_AWAY_DURATION_S + ClawdletController.CELEBRATE_DURATION_S + 0.5)
    assert PetState.CELEBRATE not in states
    assert PetState.TYPING not in states


def test_transcript_waiting_winds_typing_down_without_celebrating() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "t", "transcript")
    assert _run(ctrl, 0.5)[-1] == PetState.TYPING
    ctrl.handle_agent_event(AgentEvent.WAITING, "t", "transcript")
    states = _run(ctrl, 3.0)
    assert PetState.PUT_AWAY_LAPTOP in states
    assert PetState.CELEBRATE not in states
    ctrl.handle_agent_event(AgentEvent.DONE, "t", "transcript")  # finishing after a wait: no party
    assert PetState.CELEBRATE not in _run(ctrl, 2.0)


def test_compact_from_a_hook_session_ignores_the_transcript_copy() -> None:
    ctrl = _floor()
    ctrl.handle_agent_event(AgentEvent.BUSY, "h", "hook")
    ctrl.handle_agent_event(AgentEvent.COMPACT, "h", "transcript")  # PreCompact covers hook sessions
    assert ctrl.state == PetState.IDLE
    ctrl.handle_agent_event(AgentEvent.COMPACT, "t", "transcript")  # Hook-less session: molt
    assert ctrl.state == PetState.MOLT
    assert "t" not in ctrl.agent.sessions  # Compacting alone does not make a session busy
