"""Opt-in installation of Clawdlet's Claude Code hooks into settings.json."""

from __future__ import annotations

import json
from pathlib import Path

from clawdlet.agent_events import DEFAULT_HOOK_EVENTS
from clawdlet.app import HookSetup, hooks_status_text, main, toggle_hook_install
from clawdlet.hook_install import hooks_installed, install_hooks, uninstall_hooks
from clawdlet.tray import TrayMenuState

TOKEN = "test-token-not-secret"
USER_HOOK = {"type": "command", "command": "echo mine"}
USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "Stop": [{"hooks": [USER_HOOK]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [USER_HOOK]}],
        "PreCompact": [{"hooks": [USER_HOOK]}],
    },
}


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _own_urls(settings: dict) -> list[str]:
    return [
        h["url"]
        for groups in settings.get("hooks", {}).values()
        for g in groups
        for h in g.get("hooks", [])
        if h.get("type") == "http"
    ]


def test_install_preserves_user_settings_and_hooks(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, USER_SETTINGS)
    result = install_hooks(path, 47821, TOKEN)
    assert result.ok and result.backup is not None and result.backup.exists()
    assert _read(result.backup) == USER_SETTINGS

    data = _read(path)
    assert data["model"] == "opus" and data["permissions"] == USER_SETTINGS["permissions"]
    assert data["hooks"]["PreCompact"] == USER_SETTINGS["hooks"]["PreCompact"]
    assert data["hooks"]["Stop"][0] == {"hooks": [USER_HOOK]}
    assert data["hooks"]["Stop"][1]["hooks"][0]["url"] == "http://127.0.0.1:47821/hook"
    assert data["hooks"]["PreToolUse"][0] == {"matcher": "Bash", "hooks": [USER_HOOK]}
    assert set(DEFAULT_HOOK_EVENTS) <= set(data["hooks"])
    assert hooks_installed(path, 47821)


def test_install_is_idempotent_and_port_change_replaces(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    install_hooks(path, 47821, TOKEN)
    install_hooks(path, 47821, TOKEN)
    assert len(_own_urls(_read(path))) == len(DEFAULT_HOOK_EVENTS)

    install_hooks(path, 48000, TOKEN)
    urls = _own_urls(_read(path))
    assert len(urls) == len(DEFAULT_HOOK_EVENTS)
    assert set(urls) == {"http://127.0.0.1:48000/hook"}
    assert hooks_installed(path, 48000) and not hooks_installed(path, 47821)


def test_missing_file_is_created_without_backup(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    result = install_hooks(path, 47821, TOKEN)
    assert result.ok and result.backup is None
    assert hooks_installed(path, 47821)


def test_invalid_json_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    result = install_hooks(path, 47821, TOKEN)
    assert not result.ok
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert list(tmp_path.iterdir()) == [path]  # no backup, no temp file left behind

    _write(path, {"hooks": ["wrong shape"]})
    assert not install_hooks(path, 47821, TOKEN).ok


def test_uninstall_removes_only_clawdlet_entries(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, USER_SETTINGS)
    install_hooks(path, 47821, TOKEN)
    result = uninstall_hooks(path)
    assert result.ok and result.backup is not None
    assert _read(path) == USER_SETTINGS
    assert not hooks_installed(path, 47821)

    assert uninstall_hooks(path).backup is None  # nothing left to remove: file untouched


def test_uninstall_drops_hooks_key_when_only_ours(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, {"theme": "dark"})
    install_hooks(path, 47821, TOKEN)
    uninstall_hooks(path)
    assert _read(path) == {"theme": "dark"}


def test_tray_toggle_requires_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, USER_SETTINGS)
    answers: list[bool] = [False]
    setup = HookSetup(port=47821, token=TOKEN, settings_path=path, listening=True,
                      confirm=lambda _q: answers[0])
    tray = TrayMenuState()

    toggle_hook_install(setup, tray)  # user said No
    assert _read(path) == USER_SETTINGS and tray.hooks_installed is False

    answers[0] = True
    toggle_hook_install(setup, tray)
    assert tray.hooks_installed is True and hooks_installed(path, 47821)
    assert "已安裝" in tray.hooks_status

    toggle_hook_install(setup, tray)
    assert tray.hooks_installed is False and _read(path) == USER_SETTINGS
    assert "未安裝" in tray.hooks_status
    assert hooks_status_text(None, False).endswith("(Off)")


def test_cli_install_and_uninstall(tmp_path: Path, capsys) -> None:
    path = tmp_path / "settings.json"
    _write(path, USER_SETTINGS)
    assert main(["--install-hooks", "--claude-settings", str(path), "--hook-port", "47999"]) == 0
    assert hooks_installed(path, 47999)
    assert main(["--uninstall-hooks", "--claude-settings", str(path)]) == 0
    assert _read(path) == USER_SETTINGS
    path.write_text("{ broken", encoding="utf-8")
    assert main(["--install-hooks", "--claude-settings", str(path)]) == 1
    assert "Installed" in capsys.readouterr().out
