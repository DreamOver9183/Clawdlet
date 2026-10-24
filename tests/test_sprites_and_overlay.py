"""Integration tests for M1+M2 sprite generation, Tkinter + Win32 overlay, and HWND tracking."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from PIL import Image

from clawdlet.behavior import ClawdletController, WindowSnapshot
from clawdlet.config import TrackerConfig
from clawdlet.geometry import PixelMapper, Rect
from clawdlet.overlay import ClawdletOverlay
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import CHROMA_KEY_RGB, SpriteSheetBank
from clawdlet.tools.gen_sprites import generate_all_sprites
from clawdlet.window_tracker import Win32ClaudeTracker


def test_sprite_generator_creates_all_m1_and_m2_animations(tmp_path: Path) -> None:
    manifest_path = generate_all_sprites(tmp_path)
    assert manifest_path.exists()

    expected_frames = {
        "idle_breathe": 8,
        "idle_blob": 6,
        "walk_horizontal": 8,
        "typing_laptop": 19,
        "put_away_laptop": 6,
        "kick_laptop": 7,
        "laptop_spin": 4,
        "laptop_wall_hit": 14,
        "climb_vertical": 6,
        "perch_look_down": 6,
        "dragged_struggle": 6,
        "free_fall": 4,
        "land_squish": 4,
        "dizzy": 6,
    }
    for anim_name, frame_count in expected_frames.items():
        strip_path = tmp_path / f"{anim_name}.png"
        assert strip_path.exists()
        with Image.open(strip_path) as img:
            assert img.height == 64
            assert img.width == 64 * frame_count

    # Verify grounded animations touch the bottom-most row (y=63) so perching has 0px gap
    for grounded_anim in (
        "idle_breathe",
        "idle_blob",
        "walk_horizontal",
        "typing_laptop",
        "put_away_laptop",
        "kick_laptop",
        "perch_look_down",
        "land_squish",
        "dizzy",
    ):
        strip_path = tmp_path / f"{grounded_anim}.png"
        with Image.open(strip_path) as img:
            bottom_row_alphas = [img.getpixel((x, 63))[3] for x in range(64)]
            assert max(bottom_row_alphas) == 255


def test_sprite_bank_squash_and_flip_rendering(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    bank = SpriteSheetBank(assets_dir=tmp_path, scale=2)
    assert bank.scaled_width == 128
    assert bank.scaled_height == 128

    walk_frame = bank.get_frame("walk_horizontal", 0.25)
    normal_img = walk_frame.render_with_squash(scale_x=1.0, scale_y=1.0, flip_x=False)
    flipped_img = walk_frame.render_with_squash(scale_x=1.0, scale_y=1.0, flip_x=True)
    assert normal_img.size == (128, 128)
    assert flipped_img.size == (128, 128)
    assert normal_img.tobytes() != flipped_img.tobytes()
    assert normal_img.getpixel((0, 0)) == CHROMA_KEY_RGB

    # Verify typing_laptop plays intro frames 0..10 once, then loops frames 11..18
    typing_anim = bank._animations["typing_laptop"]
    assert typing_anim.loop_start == 11
    assert typing_anim.frame_at(0.0) is typing_anim.frames[0]
    assert typing_anim.frame_at(11.0 / typing_anim.fps) is typing_anim.frames[11]
    assert typing_anim.frame_at(19.0 / typing_anim.fps) is typing_anim.frames[11]


def test_overlay_and_in_process_win32_tracker(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    bank = SpriteSheetBank(assets_dir=tmp_path, scale=2)

    root = tk.Tk()
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    body = PhysicsBody(x=400.0, y=300.0, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=work_area)
    overlay = ClawdletOverlay(root=root, controller=ctrl, sprite_bank=bank, debug=True)

    snap = WindowSnapshot(hwnd=0x14084, rect=Rect(300.0, 428.0, 1100.0, 780.0))
    state = ctrl.tick(1.0 / 60.0, snap)
    overlay.sync_frame(state, 1.0 / 60.0)
    root.update_idletasks()
    root.update()

    hwnd = int(root.winfo_id())
    tracker = Win32ClaudeTracker(mapper=PixelMapper(1.0), cfg=TrackerConfig())
    info = tracker.inspect_hwnd(hwnd)
    assert info is not None
    assert info.hwnd == hwnd
    assert info.rect.width == 128.0
    assert info.rect.height == 128.0

    tracker._locked_hwnd = hwnd
    tracker._last_scan_time = 100.0
    polled = tracker.poll(now_s=100.1)
    assert polled is not None
    assert polled.hwnd == hwnd
    assert polled.visible is True

    root.destroy()


def test_app_short_run_lifecycle() -> None:
    from clawdlet.app import main

    rc = main(["--duration", "0.12", "--scale", "2", "--spawn", "center"])
    assert rc == 0
