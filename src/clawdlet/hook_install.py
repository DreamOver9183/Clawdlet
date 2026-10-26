"""Opt-in installer for Clawdlet's Claude Code hooks in the user's ``settings.json``.

Only entries Clawdlet itself owns are touched — identified by an ``http`` handler whose URL is the
local receiver (``http://127.0.0.1:<port>/hook``). Everything else in the file is preserved, a
timestamped backup is written first, and the new file replaces the old one atomically.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any

from clawdlet.agent_events import DEFAULT_HOOK_EVENTS, build_hook_settings
from clawdlet.transcript_watch import claude_config_dir

_OWN_URL_RE = re.compile(r"^http://127\.0\.0\.1:\d+/hook$")


def default_settings_path() -> Path:
    return claude_config_dir() / "settings.json"


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    message: str
    backup: Path | None = None


def _is_own_handler(handler: Any) -> bool:
    return (
        isinstance(handler, dict)
        and handler.get("type") == "http"
        and isinstance(handler.get("url"), str)
        and bool(_OWN_URL_RE.match(handler["url"]))
    )


def _strip_own(hooks: dict[str, Any]) -> int:
    """Remove Clawdlet handlers in place, dropping emptied groups/events. Return how many."""
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept = [h for h in handlers if not _is_own_handler(h)]
            removed += len(handlers) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
            elif len(kept) == len(handlers):
                kept_groups.append(group)  # Already-empty user group: leave it alone
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    return removed


def _load(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        return None, f"Could not read {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} does not contain a JSON object"
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return None, f'"hooks" in {path} is not an object'
    return data, None


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.clawdlet-bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    return backup


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


HOOKS_NONE = "none"
HOOKS_OUTDATED = "outdated"
HOOKS_CURRENT = "current"


def _own_handlers(groups: Any) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        return []
    return [
        h
        for g in groups
        if isinstance(g, dict) and isinstance(g.get("hooks"), list)
        for h in g["hooks"]
        if _is_own_handler(h)
    ]


def hook_install_state(path: Path, port: int, token: str | None = None) -> str:
    """``"current"``: every default event has a Clawdlet handler for ``port`` (and ``token`` if given).

    ``"outdated"``: Clawdlet handlers exist but not that full set (an older Clawdlet without the
    newer events, another port, or a rotated token); installing again upgrades them in place.
    ``"none"``: no Clawdlet handler at all (or the file is missing / unreadable).
    """
    data, error = _load(path)
    if error or not data:
        return HOOKS_NONE
    hooks = data.get("hooks", {})
    if not any(_own_handlers(groups) for groups in hooks.values()):
        return HOOKS_NONE
    url = f"http://127.0.0.1:{port}/hook"
    auth = None if token is None else f"Bearer {token}"
    for event in DEFAULT_HOOK_EVENTS:
        if not any(
            h.get("url") == url
            and (auth is None or (isinstance(h.get("headers"), dict) and h["headers"].get("Authorization") == auth))
            for h in _own_handlers(hooks.get(event))
        ):
            return HOOKS_OUTDATED
    return HOOKS_CURRENT


def hooks_installed(path: Path, port: int) -> bool:
    """True when every default event has a Clawdlet handler pointing at ``port``."""
    return hook_install_state(path, port) == HOOKS_CURRENT


def install_hooks(path: Path, port: int, token: str) -> InstallResult:
    """Add (or upgrade) Clawdlet's hooks. Idempotent: our old entries are removed before adding."""
    data, error = _load(path)
    if data is None:
        return InstallResult(False, error or "unreadable settings")
    hooks = dict(data.get("hooks", {}))
    _strip_own(hooks)
    for event, groups in build_hook_settings(port, token)["hooks"].items():
        existing = hooks.get(event)
        hooks[event] = (existing if isinstance(existing, list) else []) + groups
    backup = _backup(path)
    _write_atomic(path, {**data, "hooks": hooks})
    return InstallResult(True, f"Installed Clawdlet hooks into {path}", backup)


def uninstall_hooks(path: Path) -> InstallResult:
    data, error = _load(path)
    if data is None:
        return InstallResult(False, error or "unreadable settings")
    hooks = dict(data.get("hooks", {}))
    if _strip_own(hooks) == 0:
        return InstallResult(True, f"No Clawdlet hooks found in {path}")
    backup = _backup(path)
    new_data = dict(data)
    if hooks:
        new_data["hooks"] = hooks
    else:
        new_data.pop("hooks", None)
    _write_atomic(path, new_data)
    return InstallResult(True, f"Removed Clawdlet hooks from {path}", backup)
