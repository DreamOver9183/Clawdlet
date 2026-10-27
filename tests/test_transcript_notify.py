"""Transcript-side notifications: plan reviews, questions and permission hints."""

from __future__ import annotations

from pathlib import Path
import queue

import pytest

from clawdlet.agent_events import AgentEvent, AgentSignal, AttentionKind
from clawdlet.transcript_watch import (
    DESKTOP_ENTRYPOINT,
    PERMISSION_HINT_S,
    TOOL_WAIT_S,
    TranscriptWatcher,
    interactive_kind,
    pending_tool,
    session_entrypoint,
)
from tests.test_transcript_watch import (
    API_REQUEST,
    END_TURN,
    INTERRUPT_TOOL,
    METADATA,
    PROMPT,
    REJECTED,
    TOOL_RESULT,
    TOOL_USE,
    _append,
    _Clock,
    _drain,
    _drain_signals,
    _lines,
)


def _tool_use(message_id: str, *names: str, stop: str | None = "tool_use", **extra: object) -> dict:
    return {"type": "assistant", "uuid": f"u-{message_id}", **extra,
            "message": {"id": message_id, "role": "assistant", "stop_reason": stop,
                        "content": [{"type": "tool_use", "name": n, "input": {"secret": "never read"}}
                                    for n in names]}}


@pytest.fixture
def project(tmp_path: Path):
    proj = tmp_path / "projects" / "D--some-project"
    proj.mkdir(parents=True)
    clock = _Clock(1_000_000.0)
    q: queue.Queue = queue.Queue()
    watcher = TranscriptWatcher(events=q, projects_dir=tmp_path / "projects", clock=clock)
    assert watcher.hint_enabled is True  # On by default
    return proj, clock, q, watcher


def _start_turn(path: Path, clock: _Clock, watcher: TranscriptWatcher, *records: dict) -> None:
    _append(path, clock, END_TURN)
    watcher.poll_once()
    clock.t += 1
    _append(path, clock, PROMPT, API_REQUEST, *records)
    watcher.poll_once()


def test_pending_tool_reads_names_of_the_trailing_message_only() -> None:
    lines = _lines(_tool_use("m0", "Read"), TOOL_RESULT, _tool_use("m1", "Bash"),
                   _tool_use("m1", "ExitPlanMode"), *METADATA)
    assert pending_tool(lines) == ("m1", ("Bash", "ExitPlanMode"))
    assert interactive_kind(pending_tool(lines)[1]) == AttentionKind.PLAN
    assert pending_tool(_lines(_tool_use("m1", "Bash"), TOOL_RESULT)) == (None, ())
    assert pending_tool(_lines(TOOL_USE)) == (None, ())  # No id / uuid and no names
    assert interactive_kind(("Bash", "Read")) is None


def test_session_entrypoint_and_desktop_flag(project) -> None:
    proj, clock, q, watcher = project
    assert session_entrypoint(_lines(PROMPT, {**END_TURN, "entrypoint": "cli"}, *METADATA)) == "cli"
    path = proj / "desk.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "AskUserQuestion", entrypoint=DESKTOP_ENTRYPOINT))
    signals = _drain_signals(q)
    assert signals[-1] == AgentSignal(AgentEvent.ATTENTION, "desk", "transcript", AttentionKind.QUESTION, True)


@pytest.mark.parametrize(("tool", "kind"), [("ExitPlanMode", AttentionKind.PLAN),
                                            ("AskUserQuestion", AttentionKind.QUESTION)])
def test_interactive_tool_notifies_at_once_and_the_answer_resumes(project, tool, kind) -> None:
    proj, clock, q, watcher = project
    path = proj / "plan.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", tool))
    assert _drain_signals(q) == [
        AgentSignal(AgentEvent.BUSY, "plan", "transcript"),
        AgentSignal(AgentEvent.ATTENTION, "plan", "transcript", kind),
    ]
    clock.t += PERMISSION_HINT_S + 1
    watcher.poll_once()
    assert _drain(q) == []  # Already notified: no hint on top
    clock.t += 1
    _append(path, clock, *METADATA)  # Progress records while the user reads: still the same wait
    watcher.poll_once()
    assert _drain(q) == []
    clock.t += 1
    _append(path, clock, TOOL_RESULT)  # Answered
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "plan", "transcript")]


def test_streamed_stop_reason_is_still_caught(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "stream.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "ExitPlanMode", stop=None))
    assert _drain(q) == [(AgentEvent.BUSY, "stream", "transcript")]  # Still generating
    clock.t += 1
    _append(path, clock, _tool_use("m1", "ExitPlanMode"))
    watcher.poll_once()
    assert _drain_signals(q) == [AgentSignal(AgentEvent.ATTENTION, "stream", "transcript", AttentionKind.PLAN)]


def test_huge_plan_record_is_read_with_a_wider_tail(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "huge.jsonl"
    record = _tool_use("m1", "ExitPlanMode")
    record["message"]["content"][0]["input"] = {"plan": "x" * 200_000}
    _start_turn(path, clock, watcher, record)
    assert AgentSignal(AgentEvent.ATTENTION, "huge", "transcript", AttentionKind.PLAN) in _drain_signals(q)


def test_quiet_tool_hints_then_waits(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "perm.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "Bash"))
    assert _drain(q) == [(AgentEvent.BUSY, "perm", "transcript")]
    clock.t += PERMISSION_HINT_S - 1
    watcher.poll_once()
    assert _drain(q) == []
    clock.t += 1
    watcher.poll_once()
    assert _drain_signals(q) == [AgentSignal(AgentEvent.HINT, "perm", "transcript", AttentionKind.HINT)]
    clock.t += TOOL_WAIT_S - PERMISSION_HINT_S
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.WAITING, "perm", "transcript")]
    clock.t += 1
    _append(path, clock, TOOL_RESULT)
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "perm", "transcript")]


def test_answer_between_hint_and_waiting_resumes(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "quick.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "Bash"))
    clock.t += PERMISSION_HINT_S + 1
    watcher.poll_once()
    assert (AgentEvent.HINT, "quick", "transcript") in _drain(q)
    clock.t += 1
    _append(path, clock, TOOL_RESULT)
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "quick", "transcript")]


def test_hint_disabled_keeps_the_old_behaviour(project) -> None:
    proj, clock, q, watcher = project
    watcher.hint_enabled = False
    path = proj / "off.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "Bash"))
    _drain(q)
    clock.t += TOOL_WAIT_S - 1
    watcher.poll_once()
    assert _drain(q) == []
    clock.t += 1
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.WAITING, "off", "transcript")]


def test_wait_ends_on_end_turn_or_interrupt_and_the_next_question_still_notifies(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "q.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "AskUserQuestion"))
    _drain(q)
    clock.t += 1
    _append(path, clock, TOOL_RESULT, API_REQUEST, END_TURN)  # Answered and done within one poll
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.DONE, "q", "transcript")]
    clock.t += 1
    _append(path, clock, PROMPT, _tool_use("m2", "AskUserQuestion"))
    watcher.poll_once()
    assert _drain_signals(q)[-1] == AgentSignal(AgentEvent.ATTENTION, "q", "transcript", AttentionKind.QUESTION)
    clock.t += 1
    _append(path, clock, REJECTED, INTERRUPT_TOOL)  # Rejected: interrupted
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.INTERRUPTED, "q", "transcript")]


def test_answer_then_next_question_within_one_poll(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "two.jsonl"
    _start_turn(path, clock, watcher, _tool_use("m1", "AskUserQuestion"))
    _drain(q)
    clock.t += 1
    _append(path, clock, TOOL_RESULT, API_REQUEST, _tool_use("m2", "AskUserQuestion"))
    watcher.poll_once()
    assert _drain_signals(q) == [
        AgentSignal(AgentEvent.BUSY, "two", "transcript"),
        AgentSignal(AgentEvent.ATTENTION, "two", "transcript", AttentionKind.QUESTION),
    ]


def test_plan_waiting_at_startup_is_reported_even_if_old(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "old.jsonl"
    _append(path, clock, PROMPT, _tool_use("m1", "ExitPlanMode"))
    clock.t += 300  # Waiting for review for five minutes before Clawdlet started
    watcher.poll_once()
    assert _drain_signals(q) == [
        AgentSignal(AgentEvent.ATTENTION, "old", "transcript", AttentionKind.PLAN),
        AgentSignal(AgentEvent.WAITING, "old", "transcript"),  # Long quiet: not busy either
    ]
