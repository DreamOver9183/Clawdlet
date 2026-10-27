"""Zero-config Claude Code activity from session transcripts (fallback when hooks aren't installed).

Claude Code (terminal or Claude Desktop's Code tab) appends every turn to
``<config>/projects/<project>/<session_id>.jsonl``. Only the structure of the records near the end
of the file is read — their type / subtype, a few boolean flags (``isMeta``, ``isSidechain``,
``isCompactSummary``) and whether a user record *starts with* one of Claude Code's own markers
(``<command-name>``, ``[Request interrupted by user`` ...) — never the conversation itself, which is
neither stored nor logged. From that it tells whether a session is generating, running a tool,
finished, or was interrupted. A tool call whose transcript stays silent for ``TOOL_WAIT_S`` is
reported once as WAITING (most likely a permission prompt) and no longer counts as busy; after
``PERMISSION_HINT_S`` it is already worth a gentle HINT. The *names* of pending ``tool_use``
blocks (never their inputs) tell a plan review or a question apart, and the top-level
``entrypoint`` tells a Claude Desktop session from a terminal one.
The format is not a public API, so anything unrecognized yields nothing.
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

from clawdlet.agent_events import INTERACTIVE_TOOL_KINDS, AgentEvent, AgentSignal, AttentionKind

TAIL_BYTES = 64 * 1024
TRACK_WINDOW_S = 600.0      # Only sessions written within the last 10 minutes are tracked
RESCAN_INTERVAL_S = 5.0
POLL_INTERVAL_S = 1.0
BUSY_REFRESH_S = 60.0       # Re-announce BUSY so AgentActivity doesn't expire mid-turn
BUSY_FRESH_S = 90.0         # ...but only while the transcript itself is still being written
STARTUP_FRESH_S = 30.0      # On first sight, only a just-written busy session counts
TOOL_WAIT_S = 25.0          # A tool call this quiet is probably waiting on a permission prompt
PERMISSION_HINT_S = 8.0     # ...and this quiet is worth a gentle "maybe waiting on you" hint
MAX_TAIL_BYTES = 1024 * 1024  # A record longer than TAIL_BYTES (a long plan) is read up to this
SOURCE = "transcript"
DESKTOP_ENTRYPOINT = "claude-desktop"  # The ``entrypoint`` Claude Desktop's Code tab writes

_IDLE_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "max_tokens", "refusal"})
# Turn-ending system records (Stop hooks ran / the conversation was compacted)
_IDLE_SYSTEM_SUBTYPES = frozenset({"stop_hook_summary", "compact_boundary"})
# User records written by Claude Code itself for local slash commands: no request follows
_IDLE_USER_MARKERS = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>")
_INTERRUPT_MARKER = "[Request interrupted by user"
_MARKER_PEEK = 64  # Only this many leading characters are ever compared


class TurnPhase(str, Enum):
    GENERATING = "generating"
    TOOL = "tool"
    IDLE = "idle"
    INTERRUPTED = "interrupted"

    @property
    def busy(self) -> bool:
        return self in (TurnPhase.GENERATING, TurnPhase.TOOL)


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


def _leading_text(content: object) -> str:
    """First characters of a user message (a string, or its first text block) for marker checks."""
    if isinstance(content, list):
        content = next(
            (b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"),
            None,
        )
    return content[:_MARKER_PEEK] if isinstance(content, str) else ""


def classify_record(record: object) -> TurnPhase | None:
    """Map one transcript record to a turn phase, or None for metadata / unknown records."""
    if not isinstance(record, dict) or record.get("isSidechain") is True:
        return None
    if record.get("isMeta") is True:
        return None  # Injected context (command expansions, caveats), not a turn boundary
    kind = record.get("type")
    if kind == "api-request":
        return TurnPhase.GENERATING
    if kind == "system":
        return TurnPhase.IDLE if record.get("subtype") in _IDLE_SYSTEM_SUBTYPES else None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    if kind == "user":
        if record.get("isCompactSummary") is True:
            return TurnPhase.IDLE
        head = _leading_text(message.get("content"))
        if head.startswith(_IDLE_USER_MARKERS):
            return TurnPhase.IDLE
        if head.startswith(_INTERRUPT_MARKER):
            return TurnPhase.INTERRUPTED
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


def _parsed(lines: list[bytes]) -> list[dict]:
    records = []
    for raw in lines:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def pending_tool(lines: list[bytes]) -> tuple[str | None, tuple[str, ...]]:
    """(wait key, tool names) when the newest conversation record is a ``tool_use`` stop.

    Claude Code writes one record per content block, so the names are gathered from the trailing
    assistant records sharing that ``message.id`` (the wait key; the record ``uuid`` stands in when
    there is none). Only the ``name`` of each ``tool_use`` block is read, never its ``input``.
    """
    records = _parsed(lines)
    last = next((r for r in reversed(records) if classify_record(r) is not None), None)
    if last is None or classify_record(last) is not TurnPhase.TOOL:
        return None, ()
    message = last["message"]
    message_id = message.get("id")
    key = message_id if isinstance(message_id, str) else last.get("uuid")
    names: list[str] = []
    for record in reversed(records):
        msg = record.get("message")
        if record.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        if record is not last and (message_id is None or msg.get("id") != message_id):
            if classify_record(record) is not None:
                break  # An older turn
            continue
        for block in msg.get("content") or ():
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                names.append(block["name"])
        if message_id is None:
            break
    return (key if isinstance(key, str) else None), tuple(reversed(names))


def interactive_kind(names: tuple[str, ...]) -> AttentionKind | None:
    """The notification an ExitPlanMode / AskUserQuestion call stands for (None for other tools)."""
    return next((INTERACTIVE_TOOL_KINDS[n] for n in names if n in INTERACTIVE_TOOL_KINDS), None)


def session_entrypoint(lines: list[bytes]) -> str | None:
    """The newest top-level ``entrypoint`` (``claude-desktop``, ``cli``, ``sdk-cli`` ...)."""
    for record in reversed(_parsed(lines)):
        entrypoint = record.get("entrypoint")
        if isinstance(entrypoint, str):
            return entrypoint
    return None


def read_tail_adaptive(path: Path, size: int) -> list[bytes]:
    """``read_tail``, widening the window (up to MAX_TAIL_BYTES) while no record in it classifies.

    A single record longer than the window (an ExitPlanMode call carrying a long plan) is cut
    at the front and dropped by ``read_tail``; doubling the window brings it back.
    """
    window = TAIL_BYTES
    while True:
        lines = read_tail(path, window)
        if classify_tail(lines) is not None or window >= size or window >= MAX_TAIL_BYTES:
            return lines
        window = min(window * 2, MAX_TAIL_BYTES)


def latest_compact_marker(lines: list[bytes]) -> str | None:
    """Identity (uuid / timestamp) of the newest ``compact_boundary`` record in a transcript tail."""
    for raw in reversed(lines):
        if b"compact_boundary" not in raw:
            continue  # Cheap pre-filter: most lines are never parsed here
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if (
            isinstance(record, dict)
            and record.get("type") == "system"
            and record.get("subtype") == "compact_boundary"
            and record.get("isSidechain") is not True
        ):
            marker = record.get("uuid") or record.get("timestamp")
            return marker if isinstance(marker, str) else None
    return None


@dataclass
class _Tracked:
    size: int
    mtime: float
    phase: TurnPhase | None
    last_busy_emit: float
    waiting: bool = False             # WAITING was emitted for the current quiet tool call
    compact_marker: str | None = None
    entrypoint: str | None = None
    tool_key: str | None = None       # Wait key of the tool call the transcript currently stops on
    hinted: bool = False              # HINT was emitted for that tool call
    notified: bool = False            # ATTENTION (plan / question) was emitted for it
    wait_id: str | None = None        # The tool call those flags belong to

    @property
    def in_wait(self) -> bool:
        return self.waiting or self.hinted or self.notified

    def end_wait(self) -> None:
        self.waiting = self.hinted = self.notified = False
        self.wait_id = None


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
        self.hint_enabled = True  # The app mirrors the "Notify When Needed" switch here
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _emit(self, event: AgentEvent, path: Path, kind: AttentionKind | None = None) -> None:
        st = self._tracked.get(path)
        desktop = st is not None and st.entrypoint == DESKTOP_ENTRYPOINT
        self.events.put(AgentSignal(event, path.stem, SOURCE, kind, desktop))

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
                lines = read_tail_adaptive(path, stat.st_size)
                st.entrypoint = session_entrypoint(lines) or st.entrypoint
                compact = latest_compact_marker(lines)
                if compact is not None and compact != st.compact_marker:
                    st.compact_marker = compact
                    self._emit(AgentEvent.COMPACT, path)
                phase = classify_tail(lines)
                if phase is None:
                    continue
                st.tool_key, names = pending_tool(lines) if phase is TurnPhase.TOOL else (None, ())
                if st.in_wait and phase is TurnPhase.TOOL and st.tool_key == st.wait_id:
                    pass  # Still the same tool call (only progress / metadata records were added)
                else:
                    was_busy = st.phase is not None and st.phase.busy
                    if phase.busy and (not was_busy or st.in_wait):
                        self._emit(AgentEvent.BUSY, path)
                        st.last_busy_emit = now
                    elif not phase.busy and was_busy:
                        interrupted = phase is TurnPhase.INTERRUPTED
                        self._emit(AgentEvent.INTERRUPTED if interrupted else AgentEvent.DONE, path)
                    st.phase = phase
                    st.end_wait()
                self._notify_interactive(path, st, names)
            if (
                st.phase is TurnPhase.TOOL
                and self.hint_enabled
                and not st.notified
                and not st.hinted
                and now - st.mtime >= PERMISSION_HINT_S
            ):
                st.hinted = True
                st.wait_id = st.tool_key
                self._emit(AgentEvent.HINT, path, AttentionKind.HINT)
            if st.phase is TurnPhase.TOOL and not st.waiting and now - st.mtime >= TOOL_WAIT_S:
                st.waiting = True
                st.wait_id = st.tool_key
                self._emit(AgentEvent.WAITING, path)
            if (
                st.phase is not None
                and st.phase.busy
                and not st.in_wait
                and now - st.mtime <= BUSY_FRESH_S
                and now - st.last_busy_emit >= BUSY_REFRESH_S
            ):
                self._emit(AgentEvent.BUSY, path)
                st.last_busy_emit = now

    def _notify_interactive(self, path: Path, st: _Tracked, names: tuple[str, ...]) -> None:
        """ExitPlanMode / AskUserQuestion: the session now waits on the user, so say so right away.

        Checked on every write while stopped on a tool call (not only on entering it), so a
        record whose ``stop_reason`` arrives in a later write is still caught.
        """
        if st.phase is not TurnPhase.TOOL or st.notified:
            return
        kind = interactive_kind(names)
        if kind is None:
            return
        st.notified = True
        st.wait_id = st.tool_key
        self._emit(AgentEvent.ATTENTION, path, kind)

    def _first_sight(self, path: Path, now: float) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        lines = read_tail_adaptive(path, stat.st_size)
        phase = classify_tail(lines)
        st = _Tracked(
            size=stat.st_size,
            mtime=stat.st_mtime,
            phase=phase,
            last_busy_emit=float("-inf"),
            compact_marker=latest_compact_marker(lines),  # Old compactions are not news
            entrypoint=session_entrypoint(lines),
        )
        self._tracked[path] = st
        names: tuple[str, ...] = ()
        if phase is TurnPhase.TOOL:
            st.tool_key, names = pending_tool(lines)
        if interactive_kind(names) is not None:
            # A plan / question still waiting for an answer is news however long it has waited
            self._notify_interactive(path, st, names)
        elif phase is not None and phase.busy and now - stat.st_mtime <= STARTUP_FRESH_S:
            self._emit(AgentEvent.BUSY, path)
            st.last_busy_emit = now
        elif phase is not None and phase.busy:
            st.phase = None  # Stale busy session: wait for a fresh write before reacting

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
