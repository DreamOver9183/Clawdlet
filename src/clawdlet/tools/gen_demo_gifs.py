"""Generate animated showcase GIFs in assets/demo/ for the GitHub README (Milestone 4)."""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw

from clawdlet.sprites import SpriteSheetBank


def _paste_rgba(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    dst.paste(src, (round(x), round(y)), src)


def generate_demo_scene_gif(output_path: Path, bank: SpriteSheetBank) -> Path:
    """Create an animated 720x320 desktop showcase GIF showing perching, typing, and kicking laptop to screen edge."""
    w, h = 720, 320
    dt = 0.08  # 12.5 FPS (80ms per frame)
    frames: list[Image.Image] = []

    # Scene timeline (total ~82 frames = 6.56s loop):
    # 0..11   (0.00..0.96s): Perched on Claude Desktop titlebar (perch_look_down)
    # 12..45  (0.96..3.68s): Claude is thinking -> typing_laptop (pull out, overhead slam, long-arm typing!)
    # 46..58  (3.68..4.72s): Standard mode put_away_laptop (close lid & tuck into belly)
    # 59..81  (4.72..6.56s): Hyper mode kick_laptop -> laptop_spin across screen -> 14-frame laptop_wall_hit at x=0!
    clawd_x = 410
    win_left, win_top, win_right, win_bottom = 190, 148, 680, 304
    clawd_y = win_top - bank.scaled_height

    for idx in range(82):
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

            # Cross-screen projectile launched at kick_idx >= 2
            if 2 <= kick_idx <= 6:
                fly_progress = (kick_idx - 2) / 4.0
                proj_x = (clawd_x - 30) * (1.0 - fly_progress)
                proj_y = clawd_y + 12 - 24 * (fly_progress * (1.0 - fly_progress) * 4.0)
                p_frame = bank.get_frame("laptop_spin", (kick_idx - 2) * dt).image
                _paste_rgba(canvas, p_frame, proj_x, proj_y)
            elif kick_idx >= 7:
                hit_t = (kick_idx - 7) * dt * 1.15
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


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    bank = SpriteSheetBank(scale=2)
    out = generate_demo_scene_gif(repo_root / "assets" / "demo" / "clawdlet_demo.gif", bank)
    print(f"Generated showcase GIF at: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
