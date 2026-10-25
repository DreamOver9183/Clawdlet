"""Zero-config Claude Code activity from session transcripts (fallback when hooks aren't installed).

Claude Code (terminal or Claude Desktop's Code tab) appends every turn to
``<config>/projects/<project>/<session_id>.jsonl``. Only the record *types* near the end of the
file are read — never their content — to tell whether a session is generating, running a tool,
or has finished its turn. The format is not a public API, so anything unrecognized yields nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Callable

from clawdlet.agent_events import AgentEvent

TAIL_BYTES = 64 * 1024
TRACK_WINDOW_S = 600.0      # Only sessions written within the last 10 minutes are tracked
RESCAN_INTERVAL_S = 5.0
POLL_INTERVAL_S = 1.0
BUSY_REFRESH_S = 60.0       # Re-announce BUSY so AgentActivity doesn't expire mid-turn
BUSY_FRESH_S = 180.0        # ...but only while the transcript itself is still being written
STARTUP_FRESH_S = 30.0      # On first sight, only a just-written busy session counts
SOURCE = "transcript"

_IDLE_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "max_tokens", "refusal"})


class TurnPhase(str, Enum):
    GENERATING = "generating"
    TOOL = "tool"
    IDLE = "idle"

    @property
    def busy(self) -> bool:
        return self is not TurnPhase.IDLE


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> list[bytes]:
    """Return complete JSONL lines from the end of ``path`` (partial first/last lines dropped)."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]   # Cut mid-line by the tail window
    lines = lines[:-1]      # Last chunk is either empty or a line still being written
    return [line for line in lines if line.strip()]


def classify_record(record: object) -> TurnPhase | None:
    """Map one transcript record to a turn phase, or None for metadata / unknown records."""
    if not isinstance(record, dict) or record.get("isSidechain") is True:
        return None
    kind = record.get("type")
    if kind == "api-request":
        return TurnPhase.GENERATING
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    if kind == "user":
        return TurnPhase.GENERATING  # A prompt or a tool result: the next request is on its way
    if kind == "assistant":
        stop = message.get("stop_reason")
        if stop == "tool_use":
            return TurnPhase.TOOL
        if stop in _IDLE_STOP_REASONS:
            return TurnPhase.IDLE
        if stop is None:
            return TurnPhase.GENERATING
    return None


def classify_tail(lines: list[bytes]) -> TurnPhase | None:
    """Phase of the most recent conversation record in a transcript tail."""
    for raw in reversed(lines):
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        phase = classify_record(record)
        if phase is not None:
            return phase
    return None


@dataclass
class _Tracked:
    size: int
    mtime: float
    phase: TurnPhase | None
    last_busy_emit: float


class TranscriptWatcher:
    """Polls recently active transcripts and emits (AgentEvent, session_id, "transcript")."""

    def __init__(
        self,
        events: queue.Queue,
        projects_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.events = events
        self.projects_dir = projects_dir or (claude_config_dir() / "projects")
        self._clock = clock
        self._tracked: dict[Path, _Tracked] = {}
        self._last_rescan = float("-inf")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _emit(self, event: AgentEvent, path: Path) -> None:
        self.events.put((event, path.stem, SOURCE))

    def _recent_transcripts(self, now: float) -> list[Path]:
        found: list[Path] = []
        try:
            projects = list(os.scandir(self.projects_dir))
        except OSError:
            return found
        for project in projects:
            if not project.is_dir():
                continue
            try:
                entries = list(os.scandir(project.path))
            except OSError:
                continue
            for entry in entries:
                if not entry.name.endswith(".jsonl") or not entry.is_file():
                    continue
                try:
                    if now - entry.stat().st_mtime <= TRACK_WINDOW_S:
                        found.append(Path(entry.path))
                except OSError:
                    continue
        return found

    def poll_once(self) -> None:
        """One polling pass (the thread calls this every second; tests call it directly)."""
        now = self._clock()
        if now - self._last_rescan >= RESCAN_INTERVAL_S:
            self._last_rescan = now
            for path in self._recent_transcripts(now):
                if path not in self._tracked:
                    self._first_sight(path, now)

        for path, st in list(self._tracked.items()):
            try:
                stat = path.stat()
            except OSError:
                del self._tracked[path]
                continue
            if now - stat.st_mtime > TRACK_WINDOW_S:
                del self._tracked[path]
                continue
            if stat.st_size != st.size or stat.st_mtime != st.mtime:
                st.size, st.mtime = stat.st_size, stat.st_mtime
                phase = classify_tail(read_tail(path))
                if phase is None:
                    continue
                was_busy = st.phase is not None and st.phase.busy
                if phase.busy and not was_busy:
                    self._emit(AgentEvent.BUSY, path)
                    st.last_busy_emit = now
                elif not phase.busy and was_busy:
                    self._emit(AgentEvent.DONE, path)
                st.phase = phase
            if (
                st.phase is not None
                and st.phase.busy
                and now - st.mtime <= BUSY_FRESH_S
                and now - st.last_busy_emit >= BUSY_REFRESH_S
            ):
                self._emit(AgentEvent.BUSY, path)
                st.last_busy_emit = now

    def _first_sight(self, path: Path, now: float) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        phase = classify_tail(read_tail(path))
        st = _Tracked(size=stat.st_size, mtime=stat.st_mtime, phase=phase, last_busy_emit=float("-inf"))
        if phase is not None and phase.busy and now - stat.st_mtime <= STARTUP_FRESH_S:
            self._emit(AgentEvent.BUSY, path)
            st.last_busy_emit = now
        elif phase is not None and phase.busy:
            st.phase = None  # Stale busy session: wait for a fresh write before reacting
        self._tracked[path] = st

    def _run(self) -> None:
        while not self._stop.wait(POLL_INTERVAL_S):
            try:
                self.poll_once()
            except Exception:
                continue  # Never let a transcript quirk kill the watcher thread

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ClawdletTranscriptWatch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
