"""Integration tests for M1+M2 sprite generation, Tkinter + Win32 overlay, and HWND tracking."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from PIL import Image, ImageChops

from clawdlet.behavior import ClawdletController, WindowSnapshot
from clawdlet.config import TrackerConfig
from clawdlet.geometry import PixelMapper, Rect
from clawdlet.overlay import ClawdletOverlay
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import CHROMA_KEY_RGB, SpriteFrame, SpriteSheetBank
from clawdlet.tools.gen_sprites import generate_all_sprites
from clawdlet.window_tracker import Win32ClaudeTracker


# Intentional jumps: typing hoists the laptop overhead (2..4), kick hops on the follow-through (4),
# alert_wave hops (2..3); molt_shell dithers away (4..5) so its last frames may lose the ground row
DESIGNED_AIRBORNE_FRAMES = {
    "typing_laptop": (2, 3, 4),
    "kick_laptop": (4,),
    "alert_wave": (2, 3),
    "molt_shell": (4, 5),
}


# Milestone 5 interaction animations; every one of them is drawn standing on the ground row
M5_EXPECTED_FRAMES = {
    "gaze": 10,
    "pounce": 3,
    "poked": 4,
    "petted": 8,
    "flail": 6,
    "yawn": 6,
    "sleep": 8,
    "wake_startle": 4,
    "peek_edge": 4,
    "peek_out": 4,
    "celebrate": 8,
    "error_sweat": 6,
    "alert": 4,
    "push_window": 6,
}


# Milestone 6: work breaks, juggle, molt, alert escalation, idle tiers, perch variants, mail, goodbye
M6_EXPECTED_FRAMES = {
    "typing_loop": 8,
    "work_glance": 4,
    "work_stretch": 6,
    "work_think": 6,
    "work_sip": 8,
    "juggle": 8,
    "molt": 8,
    "molt_shell": 6,
    "alert_wave": 6,
    "alert_tap": 4,
    "confused": 6,
    "sad": 6,
    "read_scroll": 8,
    "polish_claw": 6,
    "dig_sand": 6,
    "perch_dangle": 6,
    "perch_peer": 6,
    "wall_scratch": 6,
    "wave_goodbye": 6,
    "walk_mail": 8,
    "hold_mail": 4,
    "give_mail": 4,
    "listen_tilt": 4,
}

# Break sprites that must share the typing loop's desk layout (no jump when switching)
M6_DESK_SPRITES = ("work_glance", "work_stretch", "work_think", "work_sip", "juggle")


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
        **M5_EXPECTED_FRAMES,
        **M6_EXPECTED_FRAMES,
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
        *M5_EXPECTED_FRAMES,
        *M6_EXPECTED_FRAMES,
    ):
        strip_path = tmp_path / f"{grounded_anim}.png"
        with Image.open(strip_path) as img:
            for frame_idx in range(img.width // 64):
                if frame_idx in DESIGNED_AIRBORNE_FRAMES.get(grounded_anim, ()):
                    continue
                bottom_row_alphas = [img.getpixel((frame_idx * 64 + x, 63))[3] for x in range(64)]
                assert max(bottom_row_alphas) == 255, f"{grounded_anim} frame {frame_idx} floats"


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

    rc = main(["--duration", "0.12", "--scale", "2", "--spawn", "center", "--no-hooks", "--no-transcript-watch"])
    assert rc == 0


BODY_RGB = (241, 91, 69)
EYE_RGB = (10, 10, 10)


def _frames(sprites_dir: Path, name: str) -> list[Image.Image]:
    with Image.open(sprites_dir / f"{name}.png") as strip:
        sheet = strip.convert("RGBA")
    return [sheet.crop((i * 64, 0, (i + 1) * 64, 64)) for i in range(sheet.width // 64)]


def _color_bbox(frame: Image.Image, rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    pts = [(x, y) for y in range(64) for x in range(64) if frame.getpixel((x, y)) == (*rgb, 255)]
    assert pts, f"no pixels of {rgb}"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _opaque_runs(frame: Image.Image, y: int) -> int:
    runs = 0
    inside = False
    for x in range(64):
        opaque = frame.getpixel((x, y))[3] >= 128
        if opaque and not inside:
            runs += 1
        inside = opaque
    return runs


def test_polished_sprites_match_concept_doc_poses(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)

    # perch_look_down: lying flat and peeking down -> eyes sit in the lower half of the body
    for frame in _frames(tmp_path, "perch_look_down"):
        _, body_top, _, body_bottom = _color_bbox(frame, BODY_RGB)
        _, eye_top, _, _ = _color_bbox(frame, EYE_RGB)
        assert eye_top > (body_top + body_bottom) / 2.0

    # dragged_struggle: stretched taller and narrower than the standing pose
    idle0 = _frames(tmp_path, "idle_breathe")[0]
    drag0 = _frames(tmp_path, "dragged_struggle")[0]
    il, it, ir, ib = _color_bbox(idle0, BODY_RGB)
    dl, dt, dr, db = _color_bbox(drag0, BODY_RGB)
    assert db - dt > ib - it
    assert dr - dl < ir - il

    # idle_breathe: tentacles wiggle on frames 3 and 5, at least two legs always on the ground
    idle = _frames(tmp_path, "idle_breathe")
    legs0 = idle[0].crop((0, 56, 64, 64)).tobytes()
    assert idle[3].crop((0, 56, 64, 64)).tobytes() != legs0
    assert idle[5].crop((0, 56, 64, 64)).tobytes() != legs0
    for frame in idle:
        assert _opaque_runs(frame, 63) >= 2

    # land_squish: every hand-drawn frame fits the canvas, so rendering at 1:1 clips nothing
    for frame in _frames(tmp_path, "land_squish"):
        left, _, right, _ = frame.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
        assert left >= 1 and right - 1 <= 62
        rendered = SpriteFrame(image=frame).render_with_squash(1.0, 1.0)
        opaque = sum(frame.getchannel("A").histogram()[128:])
        chroma_bg = Image.new("RGB", rendered.size, CHROMA_KEY_RGB)
        drawn_mask = ImageChops.difference(rendered, chroma_bg).convert("L").point(lambda v: 255 if v else 0)
        assert drawn_mask.histogram()[255] == opaque

    # typing_laptop intro glides right from the idle pose: body_left 8 -> 10 -> 12 (x2 px)
    typing = _frames(tmp_path, "typing_laptop")
    for idx, expected_left in ((0, 16), (1, 20), (2, 24)):
        _, eye_row, _, _ = _color_bbox(typing[idx], EYE_RGB)
        row = [x for x in range(64) if typing[idx].getpixel((x, eye_row)) == (*BODY_RGB, 255)]
        assert min(row) == expected_left


def _opaque_count(frame: Image.Image, y: int) -> int:
    return sum(1 for x in range(64) if frame.getpixel((x, y))[3] >= 128)


def test_m6_sprites_layout_and_perch_contacts(tmp_path: Path) -> None:
    import json

    generate_all_sprites(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["animations"]
    for name, count in M6_EXPECTED_FRAMES.items():
        assert manifest[name]["frames"] == count
    assert manifest["read_scroll"]["loop"] is True and manifest["read_scroll"]["loop_start"] == 3
    assert manifest["molt"]["loop"] is False and manifest["molt_shell"]["loop"] is False
    assert manifest["typing_loop"]["fps"] == manifest["typing_laptop"]["fps"]

    # typing_loop is exactly the looping tail (frames 11..18) of typing_laptop
    typing = _frames(tmp_path, "typing_laptop")
    loop = _frames(tmp_path, "typing_loop")
    assert [f.tobytes() for f in loop] == [f.tobytes() for f in typing[11:19]]

    # Work breaks keep the open laptop and Clawd's legs exactly where the typing loop has them
    laptop_box = (0, 48, 10, 64)
    legs_box = (36, 56, 64, 64)
    ref_laptop = loop[0].crop(laptop_box).tobytes()
    ref_legs = loop[0].crop(legs_box).tobytes()
    for name in M6_DESK_SPRITES:
        for idx, frame in enumerate(_frames(tmp_path, name)):
            assert frame.crop(laptop_box).tobytes() == ref_laptop, f"{name} frame {idx} laptop moved"
            assert frame.crop(legs_box).tobytes() == ref_legs, f"{name} frame {idx} Clawd moved"

    # alert_wave really leaves the ground on its hop frames
    wave = _frames(tmp_path, "alert_wave")
    for idx in DESIGNED_AIRBORNE_FRAMES["alert_wave"]:
        assert _opaque_count(wave[idx], 63) == 0

    # molt_shell is a ground prop that fades out by dithering
    shell = _frames(tmp_path, "molt_shell")
    opaque_px = [frame.getchannel("A").histogram()[255] for frame in shell]
    assert opaque_px[0] == opaque_px[3]
    assert opaque_px[3] > opaque_px[4] > opaque_px[5] > 0
    assert all(color[:3] != EYE_RGB for _, color in shell[0].getcolors(4096))

    # perch_dangle: the seat (body bottom) sits on pixel row 55, legs dangle below it to row 63
    for idx, frame in enumerate(_frames(tmp_path, "perch_dangle")):
        seat = _opaque_count(frame, 55)
        assert seat >= 32, f"perch_dangle frame {idx} has no seat at row 55"
        assert _opaque_count(frame, 56) < seat // 2
        assert _opaque_runs(frame, 56) >= 2
        assert _opaque_count(frame, 63) > 0
        assert _color_bbox(frame, BODY_RGB)[3] == 63

    # perch_peer: belly contact line on pixel row 53, eyes peer down below the bar
    for idx, frame in enumerate(_frames(tmp_path, "perch_peer")):
        belly = _opaque_count(frame, 53)
        assert belly >= 40 and _opaque_runs(frame, 53) == 1, f"perch_peer frame {idx} belly line"
        assert _opaque_count(frame, 54) < belly
        _, eye_top, _, _ = _color_bbox(frame, EYE_RGB)
        assert eye_top > 53
        assert _opaque_count(frame, 63) > 0
