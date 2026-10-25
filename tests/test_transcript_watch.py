"""Hook-less Claude Code activity detection from session transcripts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue

import pytest

from clawdlet.agent_events import AgentEvent
from clawdlet.transcript_watch import (
    BUSY_FRESH_S,
    BUSY_REFRESH_S,
    TranscriptWatcher,
    TurnPhase,
    classify_tail,
    claude_config_dir,
    read_tail,
)

# Record shapes as observed in Claude Code 2.1.308 transcripts (content replaced with stubs)
PROMPT = {"type": "user", "message": {"role": "user", "content": "hi"}, "isSidechain": False}
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}}
API_REQUEST = {"type": "api-request", "id": "r1", "sessionId": "s"}
THINKING = {"type": "assistant", "message": {"role": "assistant", "stop_reason": None,
                                             "content": [{"type": "thinking"}]}}
TOOL_USE = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                                             "content": [{"type": "tool_use"}]}}
END_TURN = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                             "content": [{"type": "text"}]}}
METADATA = [{"type": "last-prompt", "lastPrompt": "x"}, {"type": "custom-title", "customTitle": "t"},
            {"type": "attachment", "attachment": {}}, {"type": "bridge-session"}]
SIDECHAIN_END = {**END_TURN, "isSidechain": True}


def _lines(*records: dict) -> list[bytes]:
    return [json.dumps(r).encode() for r in records]


@pytest.mark.parametrize(
    ("records", "phase"),
    [
        ((PROMPT,), TurnPhase.GENERATING),
        ((PROMPT, API_REQUEST), TurnPhase.GENERATING),
        ((API_REQUEST, THINKING), TurnPhase.GENERATING),
        ((THINKING, TOOL_USE), TurnPhase.TOOL),
        ((TOOL_USE, TOOL_RESULT), TurnPhase.GENERATING),
        ((TOOL_RESULT, API_REQUEST, END_TURN), TurnPhase.IDLE),
        ((END_TURN, *METADATA), TurnPhase.IDLE),        # trailing metadata is skipped
        ((TOOL_USE, SIDECHAIN_END), TurnPhase.TOOL),     # subagent records are skipped
        ((), None),
        (tuple(METADATA), None),
    ],
)
def test_classify_tail(records: tuple[dict, ...], phase: TurnPhase | None) -> None:
    assert classify_tail(_lines(*records)) == phase


def test_classify_tail_skips_garbage() -> None:
    assert classify_tail([b"not json", *_lines(TOOL_USE), b"{broken"]) == TurnPhase.TOOL


def test_read_tail_drops_partial_lines(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    body = b"\n".join(_lines(PROMPT, TOOL_USE)) + b"\n" + b'{"type": "assistant", "mess'
    path.write_bytes(body)
    assert classify_tail(read_tail(path)) == TurnPhase.TOOL  # half-written record ignored
    # Tail window starting mid-record: first fragment dropped
    big = b'{"type": "user", "pad": "' + b"x" * 200 + b'"}\n' + b"\n".join(_lines(END_TURN)) + b"\n"
    path.write_bytes(big)
    lines = read_tail(path, max_bytes=len(big) - 20)
    assert classify_tail(lines) == TurnPhase.IDLE
    assert read_tail(tmp_path / "missing.jsonl") == []


def test_claude_config_dir_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_config_dir() == tmp_path
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert claude_config_dir() == Path.home() / ".claude"


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _append(path: Path, clock: _Clock, *records: dict) -> None:
    with open(path, "ab") as f:
        for r in records:
            f.write(json.dumps(r).encode() + b"\n")
    os.utime(path, (clock.t, clock.t))


def _drain(q: queue.Queue) -> list[tuple]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture
def project(tmp_path: Path):
    proj = tmp_path / "projects" / "D--some-project"
    proj.mkdir(parents=True)
    clock = _Clock(1_000_000.0)
    q: queue.Queue = queue.Queue()
    watcher = TranscriptWatcher(events=q, projects_dir=tmp_path / "projects", clock=clock)
    return proj, clock, q, watcher


def test_turn_lifecycle_emits_busy_then_done(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "sess-1.jsonl"
    _append(path, clock, END_TURN)  # an earlier, finished turn
    watcher.poll_once()
    assert _drain(q) == []  # idle session on first sight: nothing to report

    clock.t += 1
    _append(path, clock, PROMPT, API_REQUEST)
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "sess-1", "transcript")]

    for rec in (THINKING, TOOL_USE, TOOL_RESULT, API_REQUEST):
        clock.t += 1
        _append(path, clock, rec)
        watcher.poll_once()
    assert _drain(q) == []  # still the same busy turn

    clock.t += 1
    _append(path, clock, END_TURN, *METADATA)
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.DONE, "sess-1", "transcript")]


def test_startup_ignores_stale_sessions_but_picks_up_fresh_busy_ones(project) -> None:
    proj, clock, q, watcher = project
    stale = proj / "stale.jsonl"
    fresh = proj / "fresh.jsonl"
    old = proj / "ancient.jsonl"
    _append(stale, _Clock(clock.t - 120), PROMPT, TOOL_USE)    # busy but quiet for 2 min
    _append(fresh, _Clock(clock.t - 5), PROMPT, API_REQUEST)    # actively working right now
    _append(old, _Clock(clock.t - 3600), PROMPT, API_REQUEST)   # outside the tracking window
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "fresh", "transcript")]

    clock.t += 1
    _append(stale, clock, TOOL_RESULT)  # the stale session comes back to life
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "stale", "transcript")]


def test_busy_refresh_keeps_long_turns_alive_until_transcript_goes_quiet(project) -> None:
    proj, clock, q, watcher = project
    path = proj / "long.jsonl"
    _append(path, clock, PROMPT)
    watcher.poll_once()
    assert len(_drain(q)) == 1

    clock.t += BUSY_REFRESH_S + 1  # long tool run, transcript written recently enough
    os.utime(path, (clock.t - 30, clock.t - 30))
    watcher._tracked[path].mtime = clock.t - 30
    watcher._tracked[path].size = path.stat().st_size
    watcher.poll_once()
    assert _drain(q) == [(AgentEvent.BUSY, "long", "transcript")]

    clock.t += BUSY_FRESH_S + BUSY_REFRESH_S  # no writes for > 3 minutes: stop vouching for it
    watcher.poll_once()
    assert _drain(q) == []


def test_watcher_survives_missing_projects_dir(tmp_path: Path) -> None:
    q: queue.Queue = queue.Queue()
    watcher = TranscriptWatcher(events=q, projects_dir=tmp_path / "nope")
    watcher.poll_once()
    assert q.empty()
