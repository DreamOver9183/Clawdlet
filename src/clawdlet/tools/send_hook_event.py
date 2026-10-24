"""Developer tool: post a fake Claude Code hook event to a running Clawdlet.

Usage:
    python -m clawdlet.tools.send_hook_event Stop
    python -m clawdlet.tools.send_hook_event Notification --session demo --port 47821
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from clawdlet.agent_events import DEFAULT_HOOK_PORT, HOOK_EVENT_MAP, HOOK_PATH, read_token


def send_hook_event(event_name: str, port: int, token: str, session_id: str) -> int:
    """POST one hook payload; return the HTTP status code."""
    body = json.dumps({"hook_event_name": event_name, "session_id": session_id}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{HOOK_PATH}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a fake Claude Code hook event to Clawdlet.")
    parser.add_argument("event", choices=sorted(HOOK_EVENT_MAP), help="hook_event_name to send")
    parser.add_argument("--port", type=int, default=DEFAULT_HOOK_PORT)
    parser.add_argument("--session", default="clawdlet-dev")
    args = parser.parse_args(argv)

    token = read_token()
    if token is None:
        print("No hook token yet: start Clawdlet once so it creates one.", file=sys.stderr)
        return 1
    try:
        status = send_hook_event(args.event, args.port, token, args.session)
    except OSError as exc:
        print(f"Could not reach Clawdlet on port {args.port}: {exc}", file=sys.stderr)
        return 1
    print(f"{args.event} -> HTTP {status}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
