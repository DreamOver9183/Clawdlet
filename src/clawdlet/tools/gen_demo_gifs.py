"""Generate animated showcase GIFs in assets/demo/ for the GitHub README (Milestone 4)."""

from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw

from clawdlet.behavior import ClawdletController
from clawdlet.sprites import SpriteSheetBank


def _paste_rgba(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    dst.paste(src, (round(x), round(y)), src)


def generate_demo_scene_gif(output_path: Path, bank: SpriteSheetBank) -> Path:
    """Create an animated 720x320 desktop showcase GIF showing perching, typing, and kicking laptop to screen edge."""
    w, h = 720, 320
    dt = 0.08  # 12.5 FPS (80ms per frame)
    frames: list[Image.Image] = []

    # Scene timeline (total 88 frames = 7.04s loop):
    # 0..11   (0.00..0.96s): Perched on Claude Desktop titlebar (perch_look_down)
    # 12..45  (0.96..3.68s): Claude is thinking -> typing_laptop (pull out, overhead slam, long-arm typing!)
    # 46..58  (3.68..4.72s): Standard mode put_away_laptop (close lid & tuck into belly)
    # 59..87  (4.72..7.04s): Hyper mode kick_laptop -> laptop_spin across screen -> 14-frame laptop_wall_hit at x=0!
    # The laptop only launches once kick_laptop stops drawing it at the foot (same rule as the live app).
    hit_time_scale = 1.15
    release_idx = math.ceil(ClawdletController.KICK_RELEASE_S / dt)
    fly_frames = 4
    hit_start_idx = release_idx + fly_frames + 1
    hit_frames = math.ceil((13.0 / 8.0) / (dt * hit_time_scale)) + 1  # until frame 13 of 14 @ 8 FPS shows
    kick_scene_frames = hit_start_idx + hit_frames
    clawd_x = 410
    win_left, win_top, win_right, win_bottom = 190, 148, 680, 304
    clawd_y = win_top - bank.scaled_height

    for idx in range(59 + kick_scene_frames):
        canvas = Image.new("RGBA", (w, h), (18, 20, 28, 255))
        draw = ImageDraw.Draw(canvas)

        # Subtle desktop grid dots
        for gx in range(24, w, 48):
            for gy in range(24, h - 24, 48):
                draw.point((gx, gy), fill=(36, 40, 54, 255))

        # Left monitor bezel highlight bar
        draw.rectangle((0, 0, 3, h), fill=(48, 54, 74, 255))

        # Mock Claude Desktop Window
        draw.rounded_rectangle(
            (win_left - 2, win_top - 2, win_right + 2, win_bottom + 2),
            radius=10,
            fill=(10, 12, 18, 255),
        )
        draw.rounded_rectangle(
            (win_left, win_top, win_right, win_bottom),
            radius=8,
            fill=(30, 29, 27, 255),
            outline=(68, 65, 60, 255),
            width=2,
        )
        # Window Titlebar
        draw.rounded_rectangle(
            (win_left + 2, win_top + 2, win_right - 2, win_top + 32),
            radius=6,
            fill=(40, 38, 35, 255),
        )
        # Window traffic/control dots
        draw.ellipse((win_right - 56, win_top + 12, win_right - 46, win_top + 22), fill=(95, 92, 86, 255))
        draw.ellipse((win_right - 38, win_top + 12, win_right - 28, win_top + 22), fill=(95, 92, 86, 255))
        draw.ellipse((win_right - 20, win_top + 12, win_right - 10, win_top + 22), fill=(215, 85, 70, 255))
        draw.text((win_left + 16, win_top + 10), "Claude Desktop", fill=(222, 216, 206, 255))

        # Status badge & chat area inside mock Claude window
        if 12 <= idx <= 45:
            badge_text = "STATUS: Claude is thinking... -> Clawdlet slams laptop & types!"
            badge_col = (241, 91, 69, 255)
        elif 46 <= idx <= 58:
            badge_text = "MODE: Normal -> Neatly closes laptop & tucks into belly"
            badge_col = (110, 205, 145, 255)
        elif idx >= 59:
            badge_text = "MODE: Hyper -> KICKS laptop to left screen edge + BOOM!"
            badge_col = (255, 196, 64, 255)
        else:
            badge_text = "STATE: Perched on Claude Desktop titlebar (Window-Aware)"
            badge_col = (155, 165, 190, 255)

        draw.rounded_rectangle(
            (win_left + 16, win_top + 46, win_right - 16, win_top + 78),
            radius=6,
            fill=(46, 44, 40, 255),
            outline=badge_col,
            width=1,
        )
        draw.text((win_left + 28, win_top + 56), badge_text, fill=badge_col)

        # Mock chat lines
        draw.rounded_rectangle(
            (win_left + 16, win_top + 92, win_right - 90, win_top + 136),
            radius=6,
            fill=(36, 35, 32, 255),
        )
        draw.text(
            (win_left + 28, win_top + 106),
            "let clawd = new WindowCompanion({ gravity: true });",
            fill=(195, 190, 180, 255),
        )

        # Taskbar at bottom
        draw.rectangle((0, h - 16, w, h), fill=(12, 14, 20, 255))

        # Select Clawdlet animation frame
        if idx < 12:
            anim_t = idx * dt
            c_frame = bank.get_frame("perch_look_down", anim_t).image
            _paste_rgba(canvas, c_frame, clawd_x, clawd_y)
        elif idx <= 45:
            anim_t = (idx - 12) * dt * 1.15
            c_frame = bank.get_frame("typing_laptop", anim_t).image
            _paste_rgba(canvas, c_frame, clawd_x, clawd_y)
        elif idx <= 58:
            anim_t = (idx - 46) * dt
            c_frame = bank.get_frame("put_away_laptop", anim_t).image
            _paste_rgba(canvas, c_frame, clawd_x, clawd_y)
        else:
            kick_idx = idx - 59
            anim_t = kick_idx * dt
            c_frame = bank.get_frame("kick_laptop", anim_t).image
            _paste_rgba(canvas, c_frame, clawd_x, clawd_y)

            # Cross-screen projectile launched once the laptop leaves the foot
            if release_idx <= kick_idx < hit_start_idx:
                fly_progress = (kick_idx - release_idx) / float(fly_frames)
                proj_x = (clawd_x - 30) * (1.0 - fly_progress)
                proj_y = clawd_y + 12 - 24 * (fly_progress * (1.0 - fly_progress) * 4.0)
                p_frame = bank.get_frame("laptop_spin", (kick_idx - release_idx) * dt).image
                _paste_rgba(canvas, p_frame, proj_x, proj_y)
            elif kick_idx >= hit_start_idx:
                hit_t = (kick_idx - hit_start_idx) * dt * hit_time_scale
                p_frame = bank.get_frame("laptop_wall_hit", hit_t).image
                _paste_rgba(canvas, p_frame, 0, clawd_y + 8)

        frames.append(canvas.convert("RGB"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=int(dt * 1000),
        loop=0,
        optimize=True,
    )
    return output_path


# Small per-action GIFs for the README introduction (one card per basic action)
ACTION_CARD_SIZE = 176
ACTION_FLOOR_Y = ACTION_CARD_SIZE - 14
ACTION_DT = 0.1  # 10 FPS

# A step is (animation, seconds, (dx, dy) offset at the start, (dx, dy) offset at the end)
ActionStep = tuple[str, float, tuple[float, float], tuple[float, float]]

ACTION_GIFS: dict[str, list[ActionStep]] = {
    "walk.gif": [("walk_horizontal", 2.0, (0, 0), (0, 0))],
    "climb.gif": [
        ("climb_vertical", 2.4, (-28, 0), (-28, -90)),
        ("walk_horizontal", 0.6, (-28, -90), (24, -90)),
        ("perch_look_down", 1.2, (24, -90), (24, -90)),
    ],
    "typing.gif": [("typing_laptop", 4.6, (0, 0), (0, 0))],
    "thrown.gif": [
        ("dragged_struggle", 1.0, (0, -64), (0, -64)),
        ("free_fall", 0.4, (0, -64), (0, 0)),
        ("land_squish", 0.4, (0, 0), (0, 0)),
        ("dizzy", 1.6, (0, 0), (0, 0)),
    ],
    "petted.gif": [("petted", 2.6, (0, 0), (0, 0))],
    "juggle.gif": [("juggle", 2.0, (0, 0), (0, 0))],
    "notify.gif": [
        ("notify_plan", 1.6, (0, 0), (0, 0)),
        ("notify_question", 1.6, (0, 0), (0, 0)),
        ("notify_permission", 1.6, (0, 0), (0, 0)),
    ],
    "sleep.gif": [("sleep", 2.7, (0, 0), (0, 0))],
}


def _draw_action_backdrop(draw: ImageDraw.ImageDraw, name: str) -> None:
    size = ACTION_CARD_SIZE
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=14, fill=(18, 20, 28, 255))
    for gx in range(16, size, 32):
        for gy in range(16, ACTION_FLOOR_Y - 8, 32):
            draw.point((gx, gy), fill=(36, 40, 54, 255))
    if name == "climb.gif":
        # Corner of a mock Claude Desktop window to climb up and perch on
        left, top = 96, ACTION_FLOOR_Y - 90
        draw.rectangle((left, top, size - 1, ACTION_FLOOR_Y), fill=(30, 29, 27, 255))
        draw.rectangle((left, top, size - 1, top + 12), fill=(40, 38, 35, 255))
        draw.rectangle((left, top, left + 1, ACTION_FLOOR_Y), fill=(68, 65, 60, 255))
        draw.rectangle((left, top, size - 1, top + 1), fill=(68, 65, 60, 255))
    draw.rectangle((10, ACTION_FLOOR_Y, size - 11, ACTION_FLOOR_Y + 1), fill=(48, 54, 74, 255))


def generate_action_gif(output_path: Path, bank: SpriteSheetBank, steps: list[ActionStep]) -> Path:
    """Create a small looping card GIF that plays ``steps`` back to back, standing on a floor line."""
    frames: list[Image.Image] = []
    base_x = (ACTION_CARD_SIZE - bank.scaled_width) / 2
    base_y = ACTION_FLOOR_Y - bank.scaled_height
    for anim_name, seconds, start, end in steps:
        count = max(1, round(seconds / ACTION_DT))
        for i in range(count):
            p = i / max(1, count - 1)
            dx = start[0] + (end[0] - start[0]) * p
            dy = start[1] + (end[1] - start[1]) * p
            canvas = Image.new("RGBA", (ACTION_CARD_SIZE, ACTION_CARD_SIZE), (0, 0, 0, 0))
            _draw_action_backdrop(ImageDraw.Draw(canvas), output_path.name)
            sprite = bank.get_frame(anim_name, i * ACTION_DT).image
            _paste_rgba(canvas, sprite, base_x + dx, base_y + dy)
            frames.append(canvas)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Corners outside the rounded card stay transparent so the card sits on any README theme
    paletted = [f.convert("RGB").quantize(colors=255, dither=Image.Dither.NONE) for f in frames]
    for src, pal in zip(frames, paletted):
        pal.paste(255, mask=src.getchannel("A").point(lambda a: 255 if a < 128 else 0))
    paletted[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=int(ACTION_DT * 1000),
        loop=0,
        transparency=255,
        disposal=2,
    )
    return output_path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    bank = SpriteSheetBank(scale=2)
    out = generate_demo_scene_gif(repo_root / "assets" / "demo" / "clawdlet_demo.gif", bank)
    print(f"Generated showcase GIF at: {out}")
    for name, steps in ACTION_GIFS.items():
        out = generate_action_gif(repo_root / "assets" / "demo" / "actions" / name, bank, steps)
        print(f"Generated action GIF at: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
