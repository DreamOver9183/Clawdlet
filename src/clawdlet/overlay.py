"""Frameless always-on-top transparent overlay GUI using Tkinter + Win32 LWA_COLORKEY."""

from __future__ import annotations

import ctypes
import time
import tkinter as tk
from PIL import ImageTk
import win32con
import win32gui

from clawdlet.behavior import ClawdletController, PetState, ProjectileRenderState, RenderFrameState
from clawdlet.gestures import (
    ClickGestureRecognizer,
    Gesture,
    PetStrokeDetector,
    system_double_click_s,
)
from clawdlet.sprites import CHROMA_KEY_HEX, CHROMA_KEY_RGB, SpriteFrame, SpriteSheetBank


class _PropSlot:
    """A secondary transparent Toplevel showing one prop sprite (kicked laptop, shed shell)."""

    def __init__(self, win: tk.Toplevel, canvas: tk.Canvas, image_item: int, below_pet: bool = False) -> None:
        self.win = win
        self.canvas = canvas
        self.image_item = image_item
        self.below_pet = below_pet  # Re-raise the pet over it when shown (the shell lies behind Clawd)
        self.photo: ImageTk.PhotoImage | None = None
        self.visible: bool = False
        self.last_pos: tuple[int, int] | None = None
        self.last_key: tuple[str, int, int, bool] | None = None


class ClawdletOverlay:
    """Transparent desktop pet overlay window with Win32 color-key & click-through lock."""

    def __init__(
        self,
        root: tk.Tk,
        controller: ClawdletController,
        sprite_bank: SpriteSheetBank,
        debug: bool = False,
        click_through: bool = False,
    ) -> None:
        self.root = root
        self.controller = controller
        self.sprite_bank = sprite_bank
        self.debug = debug
        self.click_through = click_through

        self.width = self.sprite_bank.scaled_width
        self.height = self.sprite_bank.scaled_height

        self._anim_name: str = PetState.IDLE.animation_name
        self._anim_elapsed_s: float = 0.0
        self._current_frame: SpriteFrame = self.sprite_bank.get_frame(self._anim_name, 0.0)
        self._photo_image: ImageTk.PhotoImage | None = None
        self._last_pos: tuple[int, int] | None = None
        self._last_render_key: tuple[str, int, int, int, bool, int] | None = None

        # Secondary prop overlays: the kicked laptop (created up front) and the shed molt shell
        # (created on first use)
        self._proj_slot: _PropSlot | None = None
        self._shell_slot: _PropSlot | None = None

        # Mouse gestures: clicks/drags are settled by the recognizer, polled once per frame by the app
        self._clicks = ClickGestureRecognizer(double_click_s=system_double_click_s())
        self._strokes = PetStrokeDetector(min_travel_px=self.width * 0.6)
        self._pending_gestures: list[Gesture] = []

        self._configure_window()

    @property
    def scale(self) -> int:
        return self.sprite_bank.scale

    @property
    def toplevel_hwnd(self) -> int:
        """Return the outer TkTopLevel HWND (never the inner TkChild HWND)."""
        self.root.update_idletasks()
        child_id = int(self.root.winfo_id())
        parent_id = int(win32gui.GetParent(child_id))
        ctypes.windll.kernel32.SetLastError(0)
        if parent_id != 0:
            return parent_id
        frame_str = self.root.wm_frame()
        if frame_str:
            try:
                return int(frame_str, 16)
            except ValueError:
                pass
        return child_id

    def _configure_window(self) -> None:
        self.root.title("Clawdlet")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", CHROMA_KEY_HEX)
        self.root.configure(bg=CHROMA_KEY_HEX)

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=CHROMA_KEY_HEX,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self._debug_bg_item = self.canvas.create_rectangle(
            0,
            0,
            self.width,
            18,
            fill="#181818",
            outline="",
            state="normal" if self.debug else "hidden",
        )
        self._debug_text_item = self.canvas.create_text(
            4,
            2,
            anchor="nw",
            fill="#FFE8B0",
            font=("Consolas", 8, "bold"),
            text="",
            state="normal" if self.debug else "hidden",
        )

        # Bind mouse interactions
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<ButtonPress-3>", self._on_right_press)
        self.canvas.bind("<Motion>", self._on_hover_motion)
        self.canvas.bind("<Leave>", self._on_leave)

        # Configure secondary transparent projectile window (hidden until kicked laptop spawns)
        self._proj_slot = self._create_prop_window("ClawdletProjectile")

        self.root.update_idletasks()
        if self.click_through:
            self.set_click_through(True)

    def _create_prop_window(self, title: str, below_pet: bool = False) -> _PropSlot:
        """A hidden, frameless, always-on-top color-keyed Toplevel the size of the pet window."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.wm_attributes("-transparentcolor", CHROMA_KEY_HEX)
        win.configure(bg=CHROMA_KEY_HEX)
        canvas = tk.Canvas(
            win,
            width=self.width,
            height=self.height,
            bg=CHROMA_KEY_HEX,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        image_item = canvas.create_image(0, 0, anchor="nw")
        win.withdraw()
        return _PropSlot(win, canvas, image_item, below_pet)

    def set_click_through(self, enabled: bool) -> None:
        """Toggle WS_EX_TRANSPARENT on outer TkTopLevel so mouse clicks pass through."""
        self.click_through = bool(enabled)
        self._clicks.reset()
        self._strokes.reset()
        self._pending_gestures.clear()
        try:
            hwnd = self.toplevel_hwnd
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if self.click_through:
                new_style = ex_style | win32con.WS_EX_TRANSPARENT
            else:
                new_style = ex_style & ~win32con.WS_EX_TRANSPARENT
            if new_style != ex_style:
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new_style)
        except Exception:
            pass
        finally:
            ctypes.windll.kernel32.SetLastError(0)

    def set_debug(self, enabled: bool) -> None:
        """Toggle on-screen debug status banner."""
        self.debug = bool(enabled)
        vis_state = "normal" if self.debug else "hidden"
        self.canvas.itemconfigure(self._debug_bg_item, state=vis_state)
        self.canvas.itemconfigure(self._debug_text_item, state=vis_state)

    def set_scale(self, scale: int) -> None:
        """Change integer pixel-art scaling factor (1x/2x/3x) at runtime."""
        if scale not in (1, 2, 3) or scale == self.sprite_bank.scale:
            return
        self.sprite_bank = SpriteSheetBank(
            assets_dir=self.sprite_bank.assets_dir,
            scale=scale,
        )
        self.width = self.sprite_bank.scaled_width
        self.height = self.sprite_bank.scaled_height
        self.controller.resize_body(float(self.width), float(self.height))
        self._strokes.min_travel_px = self.width * 0.6

        self.canvas.configure(width=self.width, height=self.height)
        self.canvas.coords(self._debug_bg_item, 0, 0, self.width, 18)
        for slot in (self._proj_slot, self._shell_slot):
            if slot is not None:
                slot.canvas.configure(width=self.width, height=self.height)
                slot.last_key = None
                slot.last_pos = None
        self._last_render_key = None
        self._last_pos = None
        nx = round(self.controller.body.x)
        ny = round(self.controller.body.y)
        self.root.geometry(f"{self.width}x{self.height}+{nx}+{ny}")

    def sync_frame(self, state: RenderFrameState, dt: float) -> None:
        """Update window position, active animation frame, and squash/flip image."""
        if state.animation_name != self._anim_name:
            self._anim_name = state.animation_name
            self._anim_elapsed_s = 0.0
        else:
            self._anim_elapsed_s += dt

        if state.frame_index is not None:
            frame = self.sprite_bank.get_frame_index(self._anim_name, state.frame_index)
        else:
            frame = self.sprite_bank.get_frame(self._anim_name, self._anim_elapsed_s)
        self._current_frame = frame

        qx = round(state.scale_x * 50)
        qy = round(state.scale_y * 50)
        render_key = (
            self._anim_name,
            id(frame.image),
            qx,
            qy,
            state.flip_x,
            self.sprite_bank.scale,
        )
        if render_key != self._last_render_key or self._photo_image is None:
            self._last_render_key = render_key
            composited = frame.render_with_squash(
                scale_x=state.scale_x,
                scale_y=state.scale_y,
                flip_x=state.flip_x,
                chroma_rgb=CHROMA_KEY_RGB,
            )
            self._photo_image = ImageTk.PhotoImage(composited, master=self.root)
            self.canvas.itemconfigure(self._image_item, image=self._photo_image)

        if self.debug:
            hwnd_str = hex(state.locked_hwnd) if state.locked_hwnd else "-"
            lock_tag = "[LOCK]" if self.click_through else ""
            self.canvas.itemconfigure(
                self._debug_text_item,
                text=f"{lock_tag}{state.state.name[:9]}|{hwnd_str}",
            )

        target_pos = (state.x, state.y)
        if target_pos != self._last_pos:
            self._last_pos = target_pos
            self.root.geometry(f"{self.width}x{self.height}+{state.x}+{state.y}")

        self.root.wm_attributes("-topmost", True)

        # Sync cross-screen kicked laptop projectile window
        if self._proj_slot is not None:
            self._sync_prop_window(self._proj_slot, state.projectile)
        # Shed molt shell: its window is only created the first time Clawd molts
        if state.shell is not None and self._shell_slot is None:
            self._shell_slot = self._create_prop_window("ClawdletShell", below_pet=True)
        if self._shell_slot is not None:
            self._sync_prop_window(self._shell_slot, state.shell)

    def _sync_prop_window(self, slot: _PropSlot, prop: ProjectileRenderState | None) -> None:
        """Show / hide / move one prop window and redraw it only when its frame changes."""
        if prop is None:
            if slot.visible:
                slot.win.withdraw()
                slot.visible = False
                slot.last_pos = None
            return
        if not slot.visible:
            slot.win.deiconify()
            slot.win.wm_attributes("-topmost", True)
            slot.visible = True
            if slot.below_pet:
                self.root.lift()

        pframe = self.sprite_bank.get_frame(prop.animation_name, prop.elapsed_s)
        pkey = (prop.animation_name, id(pframe.image), self.sprite_bank.scale, prop.flip_x)
        if pkey != slot.last_key or slot.photo is None:
            slot.last_key = pkey
            pcomp = pframe.render_with_squash(
                scale_x=1.0,
                scale_y=1.0,
                flip_x=prop.flip_x,
                chroma_rgb=CHROMA_KEY_RGB,
            )
            slot.photo = ImageTk.PhotoImage(pcomp, master=self.root)
            slot.canvas.itemconfigure(slot.image_item, image=slot.photo)

        ppos = (prop.x, prop.y)
        if ppos != slot.last_pos:
            slot.last_pos = ppos
            slot.win.geometry(f"{self.width}x{self.height}+{prop.x}+{prop.y}")

    def poll_gestures(self, now: float) -> list[Gesture]:
        """Return gestures completed since the last frame (settled clicks, combos, strokes)."""
        gestures = self._pending_gestures
        self._pending_gestures = []
        settled = self._clicks.poll(now)
        if settled is not None:
            gestures.append(settled)
        return gestures

    def _on_left_press(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.click_through:
            return
        self._strokes.reset()
        self._clicks.press(time.monotonic(), float(event.x_root), float(event.y_root))

    def _on_left_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.click_through:
            return
        x, y = float(event.x_root), float(event.y_root)
        if self._clicks.motion(time.monotonic(), x, y) == Gesture.DRAG_START:
            # Anchor the grab where the button went down so the pet doesn't jump under the cursor
            self.canvas.configure(cursor="fleur")
            self.controller.begin_drag(*self._clicks.press_point)
        if not self._clicks.dragging:
            return
        self.controller.update_drag(x, y)
        nx = round(self.controller.body.x)
        ny = round(self.controller.body.y)
        if (nx, ny) != self._last_pos:
            self._last_pos = (nx, ny)
            self.root.geometry(f"{self.width}x{self.height}+{nx}+{ny}")

    def _on_left_release(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.click_through:
            return
        was_dragging = self._clicks.dragging
        self._clicks.release(time.monotonic())
        if was_dragging:
            self.canvas.configure(cursor="hand2")
            self.controller.end_drag()

    def _on_hover_motion(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.click_through:
            return
        if self._strokes.motion(time.monotonic(), float(event.x_root)):
            self._pending_gestures.append(Gesture.PET)

    def _on_leave(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._strokes.reset()

    def _on_right_press(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.click_through:
            return
        self.controller.hop(-650.0)
