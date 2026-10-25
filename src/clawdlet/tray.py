"""Native Win32 System Tray Icon and context menu for Clawdlet (Milestone 3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import win32api
import win32con
import win32gui

from clawdlet.config import ActivityMode

WM_TRAYICON = win32con.WM_USER + 20

# Context menu command IDs
CMD_SUMMON_CENTER = 1001
CMD_SUMMON_CLAUDE = 1002
CMD_TOGGLE_TYPING = 1003
CMD_MODE_QUIET = 1011
CMD_MODE_NORMAL = 1012
CMD_MODE_HYPER = 1013
CMD_SCALE_1X = 1021
CMD_SCALE_2X = 1022
CMD_SCALE_3X = 1023
CMD_TOGGLE_CLICK_THROUGH = 1031
CMD_TOGGLE_DEBUG = 1032
CMD_TOGGLE_PEEK = 1041
CMD_TOGGLE_WINDOW_PUSH = 1042
CMD_COPY_HOOK_CONFIG = 1051
CMD_HOOK_STATUS = 1052  # Disabled informational row
CMD_TOGGLE_HOOK_INSTALL = 1053
CMD_EXIT = 1099


@dataclass(frozen=True)
class TrayAction:
    """Action dispatched from the Win32 tray menu thread to the main GUI loop."""

    command_id: int


@dataclass
class TrayMenuState:
    """Current settings reflected as checkmarks in the system tray menu."""

    activity_mode: ActivityMode = ActivityMode.NORMAL
    scale: int = 2
    click_through: bool = False
    debug: bool = False
    manual_typing: bool = False
    peek_mode: bool = False
    window_push: bool = False
    hooks_status: str = "Claude Code Hooks：關閉 (Off)"
    hooks_installed: bool = False


def confirm_dialog(text: str, title: str = "Clawdlet") -> bool:
    """Blocking Yes/No question; True only when the user explicitly answers Yes."""
    try:
        answer = win32api.MessageBox(
            0,
            text,
            title,
            win32con.MB_YESNO | win32con.MB_ICONQUESTION | win32con.MB_TOPMOST | win32con.MB_SETFOREGROUND,
        )
    except Exception:
        return False
    return answer == win32con.IDYES


def copy_text_to_clipboard(text: str) -> bool:
    """Put ``text`` on the Windows clipboard; return False if the clipboard is busy."""
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
    except Exception:
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        return True
    except Exception:
        return False
    finally:
        win32clipboard.CloseClipboard()


class Win32TrayIcon:
    """Manages a Windows notification area icon and popup menu on a dedicated thread."""

    def __init__(
        self,
        icon_path: Path,
        state: TrayMenuState,
        action_queue: queue.Queue[TrayAction],
        tooltip: str = "Clawdlet — Claude Desktop Companion",
    ) -> None:
        self.icon_path = icon_path
        self.state = state
        self.action_queue = action_queue
        self.tooltip = tooltip

        self._hwnd: int | None = None
        self._hicon: int | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._closed = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_message_loop,
            name="ClawdletTrayThread",
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        hwnd = self._hwnd
        if hwnd is not None:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _run_message_loop(self) -> None:
        message_map = {
            win32con.WM_COMMAND: self._on_command,
            WM_TRAYICON: self._on_tray_notify,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_CLOSE: self._on_close,
        }

        wc = win32gui.WNDCLASS()
        hinst = wc.hInstance = win32api.GetModuleHandle(None)
        class_name = f"ClawdletTrayWnd_{id(self)}"
        wc.lpszClassName = class_name
        wc.lpfnWndProc = message_map

        try:
            class_atom = win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(
                class_atom,
                "ClawdletTray",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                hinst,
                None,
            )
            self._add_tray_icon()
        except Exception:
            self._ready_event.set()
            return

        self._ready_event.set()
        try:
            win32gui.PumpMessages()
        except Exception:
            pass

    def _load_icon_handle(self) -> int:
        if self.icon_path.exists():
            try:
                flags = win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE
                return int(
                    win32gui.LoadImage(
                        0,
                        str(self.icon_path),
                        win32con.IMAGE_ICON,
                        0,
                        0,
                        flags,
                    )
                )
            except Exception:
                pass
        return int(win32gui.LoadIcon(0, win32con.IDI_APPLICATION))

    def _add_tray_icon(self) -> None:
        if self._hwnd is None:
            return
        self._hicon = self._load_icon_handle()
        nid = (
            self._hwnd,
            0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            WM_TRAYICON,
            self._hicon,
            self.tooltip,
        )
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        except Exception:
            pass

    def _remove_tray_icon(self) -> None:
        if self._hwnd is None:
            return
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self._hwnd, 0))
        except Exception:
            pass

    def _on_tray_notify(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        del msg, wparam
        if lparam == win32con.WM_RBUTTONUP:
            self._show_context_menu(hwnd)
        elif lparam == win32con.WM_LBUTTONDBLCLK:
            self.action_queue.put(TrayAction(CMD_SUMMON_CENTER))
        return 0

    def _show_context_menu(self, hwnd: int) -> None:
        menu = win32gui.CreatePopupMenu()

        win32gui.AppendMenu(
            menu,
            win32con.MF_STRING,
            CMD_SUMMON_CENTER,
            "召喚回畫面中央 (Summon to Center)",
        )
        win32gui.AppendMenu(
            menu,
            win32con.MF_STRING,
            CMD_SUMMON_CLAUDE,
            "召喚至 Claude 視窗 (Summon to Claude)",
        )
        typing_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.manual_typing else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            typing_flags,
            CMD_TOGGLE_TYPING,
            "掏出筆電打字 (Coding / Typing Mode)",
        )
        peek_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.peek_mode else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            peek_flags,
            CMD_TOGGLE_PEEK,
            "邊緣躲藏 (Peek from Screen Edge)",
        )
        push_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.window_push else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            push_flags,
            CMD_TOGGLE_WINDOW_PUSH,
            "允許推視窗（過動模式） (Allow Window Push, Hyper)",
        )
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(
            menu,
            win32con.MF_STRING | win32con.MF_GRAYED,
            CMD_HOOK_STATUS,
            self.state.hooks_status,
        )
        install_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.hooks_installed else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            install_flags,
            CMD_TOGGLE_HOOK_INSTALL,
            "安裝 Claude Code hooks（~/.claude/settings.json）(Install Hooks)",
        )
        win32gui.AppendMenu(
            menu,
            win32con.MF_STRING,
            CMD_COPY_HOOK_CONFIG,
            "複製 Claude Code hook 設定 (Copy Hook Settings)",
        )
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        # Activity mode radio group
        for mode, cmd_id, label in (
            (ActivityMode.QUIET, CMD_MODE_QUIET, "活躍度：安靜模式 (Quiet)"),
            (ActivityMode.NORMAL, CMD_MODE_NORMAL, "活躍度：標準模式 (Normal)"),
            (ActivityMode.HYPER, CMD_MODE_HYPER, "活躍度：過動模式 (Hyperactive)"),
        ):
            flags = win32con.MF_STRING | (
                win32con.MF_CHECKED if self.state.activity_mode == mode else win32con.MF_UNCHECKED
            )
            win32gui.AppendMenu(menu, flags, cmd_id, label)

        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        # Scale radio group
        for sc, cmd_id, label in (
            (1, CMD_SCALE_1X, "縮放比例：1x (64px)"),
            (2, CMD_SCALE_2X, "縮放比例：2x (128px)"),
            (3, CMD_SCALE_3X, "縮放比例：3x (192px)"),
        ):
            flags = win32con.MF_STRING | (
                win32con.MF_CHECKED if self.state.scale == sc else win32con.MF_UNCHECKED
            )
            win32gui.AppendMenu(menu, flags, cmd_id, label)

        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        ct_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.click_through else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            ct_flags,
            CMD_TOGGLE_CLICK_THROUGH,
            "穿透鎖定模式 (Click-through Lock)",
        )

        dbg_flags = win32con.MF_STRING | (
            win32con.MF_CHECKED if self.state.debug else win32con.MF_UNCHECKED
        )
        win32gui.AppendMenu(
            menu,
            dbg_flags,
            CMD_TOGGLE_DEBUG,
            "顯示除錯資訊 (Debug HUD)",
        )

        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, CMD_EXIT, "結束 Clawdlet (Exit)")

        pos = win32gui.GetCursorPos()
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        win32gui.TrackPopupMenu(
            menu,
            win32con.TPM_LEFTALIGN | win32con.TPM_BOTTOMALIGN,
            pos[0],
            pos[1],
            0,
            hwnd,
            None,
        )
        win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_command(self, _hwnd: int, _msg: int, wparam: int, _lparam: int) -> int:
        cmd_id = win32api.LOWORD(wparam)
        self.action_queue.put(TrayAction(cmd_id))
        return 0

    def _on_close(self, hwnd: int, _msg: int, _wparam: int, _lparam: int) -> int:
        self._remove_tray_icon()
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, _hwnd: int, _msg: int, _wparam: int, _lparam: int) -> int:
        self._remove_tray_icon()
        win32gui.PostQuitMessage(0)
        return 0
