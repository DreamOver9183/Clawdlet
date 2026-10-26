"""Claude Code hook bridge: a token-protected localhost HTTP receiver mapped to pet reactions (M5)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import re
from pathlib import Path
import queue
import secrets
import socket
import threading
from typing import Any

DEFAULT_HOOK_PORT = 47821
HOOK_PATH = "/hook"
MAX_BODY_BYTES = 64 * 1024          # Bodies up to this size are parsed as JSON
HARD_MAX_BODY_BYTES = 16 * 1024 * 1024  # Larger tool payloads are drained; beyond this -> 413
_HEAD_BYTES = 4096
_HEAD_FIELD_RE = {
    key: re.compile(rb'"' + key.encode() + rb'"\s*:\s*"([^"\\]{1,128})"')
    for key in ("hook_event_name", "session_id", "notification_type")
}
DEFAULT_SESSION = "default"
HOOK_SOURCE = "hook"


class AgentEvent(str, Enum):
    """Semantic agent activity derived from Claude Code hook events."""

    BUSY = "busy"            # Prompt submitted / tool running: pull out the laptop
    ERROR = "error"          # A tool call failed mid-turn: nervous sweat, keep working
    ATTENTION = "attention"  # Claude Code waits for the user: '!' alert
    DONE = "done"            # Turn finished: put the laptop away and celebrate
    FAILED = "failed"        # Turn ended on an API error: sweat, no celebration
    END = "end"              # Session closed
    WAITING = "waiting"      # A tool call went quiet (likely a permission prompt): not busy
    INTERRUPTED = "interrupted"  # The user interrupted the turn: put the laptop away, no party
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    COMPACT = "compact"      # The conversation was compacted


HOOK_EVENT_MAP: dict[str, AgentEvent] = {
    "UserPromptSubmit": AgentEvent.BUSY,
    "PreToolUse": AgentEvent.BUSY,
    "PostToolUse": AgentEvent.BUSY,
    "SubagentStart": AgentEvent.BUSY,
    "PostToolUseFailure": AgentEvent.ERROR,
    "Notification": AgentEvent.ATTENTION,
    "PermissionRequest": AgentEvent.ATTENTION,
    "Stop": AgentEvent.DONE,
    "StopFailure": AgentEvent.FAILED,
    "SessionEnd": AgentEvent.END,
}

# Hooks written by --print-hook-config. PermissionRequest is deliberately left out: Notification
# already covers permission prompts, and staying off the permission path keeps Clawdlet harmless.
DEFAULT_HOOK_EVENTS: tuple[str, ...] = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "Notification",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
# Notification types that do not need the user's attention ("Claude is waiting for your input"
# fires after a finished turn has sat idle; auth_success just confirms a login)
_QUIET_NOTIFICATION_TYPES = frozenset({"idle_prompt", "auth_success"})


def scan_payload_head(head: bytes) -> dict[str, str]:
    """Pull ``hook_event_name`` / ``session_id`` / ``notification_type`` out of an oversized payload's head.

    Claude Code sends the common fields before ``tool_input`` / ``tool_response``, so a big
    PostToolUse body still yields its event name without parsing megabytes of tool output.
    """
    fields: dict[str, str] = {}
    for key, pattern in _HEAD_FIELD_RE.items():
        match = pattern.search(head)
        if match:
            fields[key] = match.group(1).decode("utf-8", "replace")
    return fields


def map_hook_payload(payload: Any) -> tuple[AgentEvent, str] | None:
    """Map a hook JSON payload to (event, session_id).

    Only the event name, the session id and the ``notification_type`` enum are read; messages,
    prompts and tool inputs are never touched.
    """
    if not isinstance(payload, dict):
        return None
    name = payload.get("hook_event_name")
    event = HOOK_EVENT_MAP.get(name) if isinstance(name, str) else None
    if event is None:
        return None
    if name == "Notification" and payload.get("notification_type") in _QUIET_NOTIFICATION_TYPES:
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        session = DEFAULT_SESSION
    return event, session[:128]


SESSION_BUSY = "busy"
SESSION_WAITING = "waiting"


@dataclass
class SessionState:
    """What one Claude Code session is doing, as far as its events tell."""

    phase: str           # SESSION_BUSY or SESSION_WAITING (waiting on the user: not typing)
    last_t: float        # Time of the session's latest event
    subagents: int = 0
    source: str = HOOK_SOURCE


class AgentActivity:
    """Tracks which Claude Code sessions are busy or waiting, expiring sessions that went silent."""

    def __init__(self, busy_expiry_s: float = 180.0) -> None:
        self.busy_expiry_s = busy_expiry_s
        self.sessions: dict[str, SessionState] = {}
        # Sessions that have delivered real hook events: transcript guesses are ignored for them
        self.hook_sessions: set[str] = set()

    def _expire(self, now: float) -> None:
        expired = [s for s, st in self.sessions.items() if (now - st.last_t) > self.busy_expiry_s]
        for s in expired:
            del self.sessions[s]

    def is_session_busy(self, session_id: str, now: float) -> bool:
        st = self.sessions.get(session_id)
        return st is not None and st.phase == SESSION_BUSY and (now - st.last_t) <= self.busy_expiry_s

    def busy(self, now: float) -> bool:
        self._expire(now)
        return any(st.phase == SESSION_BUSY for st in self.sessions.values())

    def note(self, event: AgentEvent, session_id: str, now: float, source: str = HOOK_SOURCE) -> bool:
        """Record an event; return whether the session was busy just before it."""
        was_busy = self.is_session_busy(session_id, now)
        st = self.sessions.get(session_id)
        if event in (AgentEvent.BUSY, AgentEvent.ERROR, AgentEvent.SUBAGENT_START, AgentEvent.SUBAGENT_STOP):
            if st is None:
                st = self.sessions[session_id] = SessionState(SESSION_BUSY, now, source=source)
            st.phase, st.last_t, st.source = SESSION_BUSY, now, source
            if event == AgentEvent.SUBAGENT_START:
                st.subagents += 1
            elif event == AgentEvent.SUBAGENT_STOP:
                st.subagents = max(0, st.subagents - 1)
        elif event in (AgentEvent.ATTENTION, AgentEvent.WAITING):
            if st is None:
                st = self.sessions[session_id] = SessionState(SESSION_WAITING, now, source=source)
            st.phase, st.last_t, st.source = SESSION_WAITING, now, source
        elif event in (AgentEvent.DONE, AgentEvent.FAILED, AgentEvent.END, AgentEvent.INTERRUPTED):
            self.sessions.pop(session_id, None)
        return was_busy

    def describe(self, now: float) -> str:
        """One-line summary for --debug, e.g. ``busy=[3f2a91c0 hook 42s] waiting=[]``."""
        self._expire(now)
        groups: dict[str, list[str]] = {SESSION_BUSY: [], SESSION_WAITING: []}
        for session_id, st in self.sessions.items():
            item = f"{session_id[:8]} {st.source} {max(0.0, now - st.last_t):.0f}s"
            if st.subagents:
                item += f" sub={st.subagents}"
            groups[st.phase].append(item)
        return " ".join(f"{phase}=[{', '.join(items)}]" for phase, items in groups.items())


def default_token_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Clawdlet" / "hook_token"


def read_token(path: Path | None = None) -> str | None:
    path = path or default_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def load_or_create_token(path: Path | None = None) -> str:
    """Return the persisted hook token, generating one on first run."""
    path = path or default_token_path()
    token = read_token(path)
    if token:
        return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def build_hook_settings(port: int, token: str, events: tuple[str, ...] = DEFAULT_HOOK_EVENTS) -> dict:
    """Return the ``hooks`` block to merge into ``~/.claude/settings.json``."""
    handler = {
        "type": "http",
        "url": f"http://127.0.0.1:{port}{HOOK_PATH}",
        "timeout": 2,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    hooks: dict[str, list[dict]] = {}
    for name in events:
        entry: dict[str, Any] = {"hooks": [dict(handler)]}
        if name in _TOOL_EVENTS:
            entry = {"matcher": "*", **entry}
        hooks[name] = [entry]
    return {"hooks": hooks}


def format_hook_settings(port: int, token: str) -> str:
    return json.dumps(build_hook_settings(port, token), indent=2)


class _HookHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port and steal requests
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], token: str, events: queue.Queue) -> None:
        super().__init__(address, _HookRequestHandler)
        self.token = token
        self.events = events

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class _HookRequestHandler(BaseHTTPRequestHandler):
    server: _HookHTTPServer
    server_version = "Clawdlet"
    sys_version = ""

    def _reply(self, status: int) -> None:
        # Empty 2xx body = "no decision": Clawdlet only observes and never steers Claude Code
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if status != 200:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path != HOOK_PATH:
            self._reply(404)
            return
        expected = f"Bearer {self.server.token}".encode()
        given = (self.headers.get("Authorization") or "").encode()
        if not hmac.compare_digest(given, expected):
            self._reply(401)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._reply(400)
            return
        if length < 0:
            self._reply(400)
            return
        if length > HARD_MAX_BODY_BYTES:
            self._reply(413)
            return
        if length > MAX_BODY_BYTES:
            payload: Any = scan_payload_head(self._drain_body(length))
        else:
            try:
                payload = json.loads(self.rfile.read(length) or b"null")
            except (ValueError, UnicodeDecodeError):
                self._reply(400)
                return
            if not isinstance(payload, dict):
                self._reply(400)
                return
        mapped = map_hook_payload(payload)
        if mapped is not None:
            self.server.events.put((*mapped, HOOK_SOURCE))
        self._reply(200)

    def _drain_body(self, length: int) -> bytes:
        """Read and discard an oversized body, keeping only its first bytes."""
        head = self.rfile.read(min(length, _HEAD_BYTES))
        remaining = length - len(head)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        return head

    def _method_not_allowed(self) -> None:
        self._reply(405)

    do_GET = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _method_not_allowed

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Never log requests: payloads may include prompts and tool inputs
        return


class HookServer:
    """Background localhost receiver for Claude Code ``type: "http"`` hooks."""

    def __init__(
        self,
        token: str,
        events: queue.Queue,
        port: int = DEFAULT_HOOK_PORT,
        host: str = "127.0.0.1",
    ) -> None:
        self.token = token
        self.events = events
        self.host = host
        self.requested_port = port
        self._server: _HookHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        return None if self._server is None else int(self._server.server_address[1])

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        """Bind and serve in a daemon thread. Return False (and stay disabled) if the port is taken."""
        try:
            self._server = _HookHTTPServer((self.host, self.requested_port), self.token, self.events)
        except OSError:
            self._server = None
            return False
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ClawdletHookServer",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
