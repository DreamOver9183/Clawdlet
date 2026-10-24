"""Generate minimalist native-style 64x64 pixel-art sprite sheets for Clawdlet."""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

# Ultra-clean native Claude Desktop mascot palette (no outlines, no blush, flat blocks)
TRANSPARENT = (0, 0, 0, 0)
TERRACOTTA = (215, 119, 87, 255)      # Iconic flat Clawd terracotta (#D77757)
TERRACOTTA_DARK = (186, 95, 66, 255)  # Subtle back-leg depth only during walk/typing
EYE_COLOR = (25, 25, 24, 255)         # Minimalist dark charcoal pixel eyes (#191918)

# Laptop palette for the "Claude is thinking / coding" state
LAPTOP_LID = (242, 239, 233, 255)     # Crisp warm silver/white inner '\\_' stroke
LAPTOP_EDGE = (180, 174, 164, 255)    # Subtle key-tap highlight
LAPTOP_BASE = (35, 35, 34, 255)       # Crisp dark charcoal outer '\\_' stroke
LAPTOP_GLOW = (255, 248, 214, 255)    # Warm screen glow pixel
LAPTOP_LOGO = (215, 119, 87, 255)     # Tiny terracotta pixel emblem
STAR_GOLD = (245, 198, 66, 255)       # Minimalist pixel star for dizzy state

# 16x16 coarse pixel grid -> scaled 4x to 64x64 base sprite (8x8 screen px at 2x scale)
GRID_SIZE = 16
PIXEL_SCALE = 4
FRAME_SIZE = GRID_SIZE * PIXEL_SCALE


def _new_grid() -> list[list[tuple[int, int, int, int]]]:
    return [[TRANSPARENT for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]


def _rect(
    grid: list[list[tuple[int, int, int, int]]],
    x0: int,
    y0: int,
    w: int,
    h: int,
    color: tuple[int, int, int, int],
) -> None:
    for y in range(max(0, y0), min(GRID_SIZE, y0 + h)):
        for x in range(max(0, x0), min(GRID_SIZE, x0 + w)):
            grid[y][x] = color


def _px(
    grid: list[list[tuple[int, int, int, int]]],
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    if 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE:
        grid[y][x] = color


def _draw_native_clawd(
    grid: list[list[tuple[int, int, int, int]]],
    body_left: int = 3,
    body_top: int = 6,
    body_width: int = 10,
    body_height: int = 7,
    ear_dy: int = 2,
    ear_h: int = 2,
    left_ear_dy: int | None = None,
    right_ear_dy: int | None = None,
    leg_xs: tuple[int, int, int, int] | None = None,
    leg_tops: tuple[int, int, int, int] | None = None,
    leg_bottoms: tuple[int, int, int, int] = (15, 15, 15, 15),
    leg_colors: tuple[tuple[int, int, int, int], ...] | None = None,
    eye_mode: str = "normal",
    eye_dx: int = 0,
    eye_dy: int = 0,
) -> None:
    """Draw the minimalist native Claude Desktop Clawd on a 16x16 coarse grid.

    Default grounded proportions:
      - Torso: x=3..12 (10 wide), y=6..12 (7 tall)
      - Side arms/ears: x=2 and x=13, y=8..9 (1 wide, 2 tall)
      - Eyes: x=5 and x=10, y=8..9 (1 wide, 2 tall)
      - 4 vertical legs: x=(3, 5, 10, 12), y=13..15 (touching bottom row y=15)
    """
    b_bottom = body_top + body_height

    # 1. Legs (drawn first so torso cleanly joins top of legs)
    if leg_xs is None:
        leg_xs = (
            body_left,
            body_left + 2,
            body_left + body_width - 3,
            body_left + body_width - 1,
        )
    if leg_tops is None:
        leg_tops = (b_bottom, b_bottom, b_bottom, b_bottom)
    if leg_colors is None:
        leg_colors = (TERRACOTTA, TERRACOTTA, TERRACOTTA, TERRACOTTA)

    for idx in range(4):
        lx = leg_xs[idx]
        ly0 = leg_tops[idx]
        ly1 = leg_bottoms[idx]
        if ly1 >= ly0:
            _rect(grid, lx, ly0, 1, ly1 - ly0 + 1, leg_colors[idx])

    # 2. Main flat rectangular terracotta torso
    _rect(grid, body_left, body_top, body_width, body_height, TERRACOTTA)

    # 3. Side 1-block protrusions (arms / ears)
    l_dy = ear_dy if left_ear_dy is None else left_ear_dy
    r_dy = ear_dy if right_ear_dy is None else right_ear_dy
    if ear_h > 0:
        _rect(grid, body_left - 1, body_top + l_dy, 1, ear_h, TERRACOTTA)
        _rect(grid, body_left + body_width, body_top + r_dy, 1, ear_h, TERRACOTTA)

    # 4. Minimalist pixel eyes
    ex_left = body_left + 2 + eye_dx
    ex_right = body_left + body_width - 3 + eye_dx
    ey = body_top + 2 + eye_dy

    if eye_mode == "blink":
        _px(grid, ex_left, ey + 1, EYE_COLOR)
        _px(grid, ex_right, ey + 1, EYE_COLOR)
    elif eye_mode == "squint":
        # Happy/focused "> <" or horizontal bar eyes
        _px(grid, ex_left, ey, EYE_COLOR)
        _px(grid, ex_right, ey, EYE_COLOR)
    elif eye_mode == "dizzy":
        # Minimalist staggered dizzy eyes
        _px(grid, ex_left, ey, EYE_COLOR)
        _px(grid, ex_left + 1, ey + 1, EYE_COLOR)
        _px(grid, ex_right + 1, ey, EYE_COLOR)
        _px(grid, ex_right, ey + 1, EYE_COLOR)
    else:
        # Classic 1x2 vertical dark pixel eyes
        _rect(grid, ex_left, ey, 1, 2, EYE_COLOR)
        _rect(grid, ex_right, ey, 1, 2, EYE_COLOR)


def _grid_to_image(grid: list[list[tuple[int, int, int, int]]]) -> Image.Image:
    img = Image.new("RGBA", (FRAME_SIZE, FRAME_SIZE), TRANSPARENT)
    px = img.load()
    for y in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            c = grid[y][x]
            if c[3] == 0:
                continue
            for dy in range(PIXEL_SCALE):
                for dx in range(PIXEL_SCALE):
                    px[x * PIXEL_SCALE + dx, y * PIXEL_SCALE + dy] = c
    return img


def _make_strip(frames: list[Image.Image]) -> Image.Image:
    strip = Image.new("RGBA", (FRAME_SIZE * len(frames), FRAME_SIZE), TRANSPARENT)
    for i, frame in enumerate(frames):
        strip.paste(frame, (i * FRAME_SIZE, 0))
    return strip


def _build_idle_breathe() -> list[Image.Image]:
    """Minimalist native idle: subtle 1-block breathing bob and occasional blink."""
    frames: list[Image.Image] = []
    # (body_top, body_height, ear_dy, eye_mode)
    specs = [
        (6, 7, 2, "normal"),
        (7, 6, 2, "normal"),
        (6, 7, 2, "blink"),
        (6, 7, 2, "normal"),
    ]
    for b_top, b_h, ear_dy, eyes in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=b_top,
            body_height=b_h,
            ear_dy=ear_dy,
            eye_mode=eyes,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_idle_blob() -> list[Image.Image]:
    """Minimalist flat loaf/blob mode: settled low on the ground, legs tucked."""
    frames: list[Image.Image] = []
    specs = [
        (9, 6, 2, "normal"),
        (10, 5, 2, "blink"),
        (10, 5, 2, "blink"),
        (9, 6, 2, "normal"),
    ]
    for b_top, b_h, ear_dy, eyes in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_left=2,
            body_top=b_top,
            body_width=12,
            body_height=b_h,
            ear_dy=ear_dy,
            ear_h=2,
            leg_xs=(3, 5, 10, 12),
            leg_bottoms=(15, 15, 15, 15),
            eye_mode=eyes,
            eye_dy=1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_walk_horizontal() -> list[Image.Image]:
    """Minimalist 6-frame walk cycle with alternating 1-block leg lift."""
    frames: list[Image.Image] = []
    c_main = TERRACOTTA
    c_back = TERRACOTTA_DARK
    # Each frame specifies (body_top, leg_xs, leg_bottoms, leg_colors)
    # At least two legs reach y=15 on every frame so feet stay grounded.
    specs = [
        (6, (3, 5, 10, 12), (15, 14, 15, 14), (c_main, c_back, c_main, c_back)),
        (6, (2, 6, 9, 13), (15, 14, 15, 14), (c_main, c_back, c_main, c_back)),
        (7, (3, 5, 10, 12), (15, 15, 15, 15), (c_main, c_main, c_main, c_main)),
        (6, (3, 5, 10, 12), (14, 15, 14, 15), (c_back, c_main, c_back, c_main)),
        (6, (4, 5, 11, 12), (14, 15, 14, 15), (c_back, c_main, c_back, c_main)),
        (7, (3, 5, 10, 12), (15, 15, 15, 15), (c_main, c_main, c_main, c_main)),
    ]
    for b_top, l_xs, l_bots, l_cols in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=b_top,
            body_height=13 - b_top,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            leg_colors=l_cols,
            eye_dx=1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _draw_side_laptop_profile(
    grid: list[list[tuple[int, int, int, int]]],
    y_offset: int = 0,
    slam_squash: int = 0,
    pressed_key_x: int | None = None,
) -> None:
    """Draw a sleek minimalist '\\_' laptop in side profile on the far left (x=0..5, y=9..15).

    Placed at x=0..5 so there is a wide comedic gap to Clawd on the far right (x=9..15),
    allowing Clawd to stretch BOTH long arms across x=3..8 to type!
    """
    sy = slam_squash
    dy = y_offset

    # 1. Diagonal '\\' screen lid (x=0..2, y=9..13)
    if sy == 0:
        _px(grid, 0, 9 + dy, LAPTOP_BASE)
        _px(grid, 1, 9 + dy, LAPTOP_LID)

    _px(grid, 0, 10 + dy, LAPTOP_BASE)
    _px(grid, 1, 10 + dy, LAPTOP_LID)

    _px(grid, 1, 11 + dy, LAPTOP_BASE)
    _px(grid, 2, 11 + dy, LAPTOP_LID)

    _px(grid, 1, 12 + dy, LAPTOP_BASE)
    _px(grid, 2, 12 + dy, LAPTOP_LID)

    _px(grid, 2, 13 + dy, LAPTOP_BASE)
    _px(grid, 3, 13 + dy, LAPTOP_LID)

    # 2. Horizontal '_' keyboard base (x=2..5, y=14..15)
    _px(grid, 2, 14 + dy, LAPTOP_BASE)
    for kx in (3, 4, 5):
        _px(grid, kx, 14 + dy, LAPTOP_EDGE if kx == pressed_key_x else LAPTOP_LID)

    _rect(grid, 2, 15 + dy, 4, 1, LAPTOP_BASE)


def _draw_right_side_clawd_facing_left(
    grid: list[list[tuple[int, int, int, int]]],
    body_left: int = 9,
    body_top: int = 6,
    body_width: int = 7,
    body_height: int = 7,
    eye_mode: str = "normal",
    right_ear_dy: int = 2,
) -> None:
    """Draw Clawd standing on the far right side (x=9..15) facing left toward the laptop."""
    b_bottom = body_top + body_height
    leg_xs = (
        body_left,
        body_left + 2,
        body_left + body_width - 3,
        body_left + body_width - 1,
    )
    leg_cols = (TERRACOTTA, TERRACOTTA_DARK, TERRACOTTA, TERRACOTTA_DARK)
    for lx, lcol in zip(leg_xs, leg_cols):
        if 0 <= lx < GRID_SIZE and b_bottom <= 15:
            _rect(grid, lx, b_bottom, 1, 15 - b_bottom + 1, lcol)

    # Torso
    _rect(grid, body_left, body_top, body_width, body_height, TERRACOTTA)

    # Right-side little arm/ear protrusion if room on right
    if body_left + body_width < GRID_SIZE and right_ear_dy >= 0:
        _rect(grid, body_left + body_width, body_top + right_ear_dy, 1, 2, TERRACOTTA)

    # Left-facing eyes
    ex_left = body_left + 1
    ex_right = body_left + 4
    ey = body_top + 2
    if eye_mode == "blink":
        _px(grid, ex_left, ey + 1, EYE_COLOR)
        _px(grid, ex_right, ey + 1, EYE_COLOR)
    elif eye_mode == "squint":
        _px(grid, ex_left, ey, EYE_COLOR)
        _px(grid, ex_right, ey, EYE_COLOR)
    else:
        _rect(grid, ex_left, ey, 1, 2, EYE_COLOR)
        _rect(grid, ex_right, ey, 1, 2, EYE_COLOR)


def _build_typing_laptop() -> list[Image.Image]:
    """Comedic side-profile laptop animation with readable comedic timing & holds (5.5 FPS):
    Frames 0..10 (11-frame paced intro, ~2.0s total):
      0..1 (2f): Dig laptop out of torso (reaching in -> pulling out)
      2..4 (3f): Hoist laptop HIGH OVERHEAD with both arms on tiptoes (quivering anticipation hold!)
      5..6 (2f): VIOLENT FULL-FORCE SLAM onto left floor with shockwave & expanding gold sparks!
      7..8 (2f): Laptop bounces up into the air from the slam impact while Clawd steps back to x=9..15
      9..10 (2f): Laptop lands in '\\_' at x=0..5, Clawd stretches BOTH 6-px arms out across the gap!
    Frames 11..18 (loop_start=11, 8-frame readable 2-handed long-arm typing loop):
      Clawd stands on the far right (x=9..15) stretching TWO super-long arms (x=3..8) all the way
      to the left, clearly alternating high wind-ups and heavy keyboard smashes!
    """
    frames: list[Image.Image] = []

    # --- Frame 0: Reach hand deep into belly ---
    g0 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g0, body_left=6, body_top=6, body_width=8, body_height=7, eye_mode="normal"
    )
    _rect(g0, 4, 9, 3, 3, LAPTOP_BASE)
    _rect(g0, 5, 10, 2, 1, LAPTOP_LID)
    _rect(g0, 5, 10, 2, 1, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g0))

    # --- Frame 1: Pull folded laptop out from torso with squinting effort ---
    g1 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g1, body_left=6, body_top=6, body_width=8, body_height=7, eye_mode="squint"
    )
    _rect(g1, 2, 8, 4, 4, LAPTOP_BASE)
    _rect(g1, 3, 9, 2, 2, LAPTOP_LID)
    _rect(g1, 5, 9, 2, 2, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g1))

    # --- Frames 2, 3, 4: HUGE OVERHEAD WIND-UP HOLD (3 frames so viewer clearly sees overhead hoist!) ---
    for quiver_idx in range(3):
        g_up = _new_grid()
        b_top = 5 if quiver_idx != 1 else 4  # Quiver 1px higher at peak of wind-up!
        _draw_right_side_clawd_facing_left(
            g_up,
            body_left=6,
            body_top=b_top,
            body_width=8,
            body_height=13 - b_top,
            eye_mode="squint",
        )
        laptop_x = 6 if quiver_idx % 2 == 0 else 5
        # Two long arms raised straight overhead holding the laptop high above head!
        _rect(g_up, 7, 2, 1, b_top - 2, TERRACOTTA_DARK)
        _rect(g_up, 11, 2, 1, b_top - 2, TERRACOTTA)
        _rect(g_up, laptop_x, 0, 7, 3, LAPTOP_BASE)
        _rect(g_up, laptop_x + 1, 1, 5, 1, LAPTOP_LID)
        if quiver_idx == 2:
            # Tiny effort spark near top right before slamming down
            _px(g_up, 14, 1, STAR_GOLD)
        frames.append(_grid_to_image(g_up))

    # --- Frame 5: MASSIVE OVERHEAD SLAM onto the left floor (impact frame 1)! ---
    g5 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g5, body_left=8, body_top=9, body_width=7, body_height=5, eye_mode="blink", right_ear_dy=-1
    )
    _rect(g5, 5, 11, 3, 1, TERRACOTTA)
    _rect(g5, 3, 12, 3, 1, TERRACOTTA)
    _draw_side_laptop_profile(g5, y_offset=0, slam_squash=1)
    # Huge vertical slam speed-lines + bright impact sparks!
    _rect(g5, 1, 4, 1, 5, LAPTOP_LID)
    _rect(g5, 4, 5, 1, 5, LAPTOP_LID)
    _px(g5, 0, 7, STAR_GOLD)
    _px(g5, 3, 3, STAR_GOLD)
    _px(g5, 6, 8, STAR_GOLD)
    frames.append(_grid_to_image(g5))

    # --- Frame 6: SLAM IMPACT HOLD & SHOCKWAVE EXPANSION (impact frame 2)! ---
    g6 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g6, body_left=8, body_top=9, body_width=7, body_height=5, eye_mode="squint", right_ear_dy=-1
    )
    _rect(g6, 5, 11, 3, 1, TERRACOTTA)
    _rect(g6, 3, 12, 3, 1, TERRACOTTA)
    _draw_side_laptop_profile(g6, y_offset=0, slam_squash=1)
    # Shockwave sparks flying outward
    _px(g6, 0, 5, STAR_GOLD)
    _px(g6, 2, 2, STAR_GOLD)
    _px(g6, 6, 6, STAR_GOLD)
    _px(g6, 7, 13, LAPTOP_LID)
    _px(g6, 0, 13, LAPTOP_LID)
    frames.append(_grid_to_image(g6))

    # --- Frame 7: Laptop BOUNCES HIGH UP into the air from the violent slam! ---
    g7 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g7, body_left=9, body_top=6, body_width=7, body_height=7, eye_mode="normal", right_ear_dy=-1
    )
    _draw_side_laptop_profile(g7, y_offset=-3, slam_squash=0)
    _px(g7, 1, 15, LAPTOP_LID)
    _px(g7, 5, 15, LAPTOP_LID)
    _px(g7, 0, 5, STAR_GOLD)
    _rect(g7, 7, 9, 2, 1, TERRACOTTA_DARK)
    _rect(g7, 7, 11, 2, 1, TERRACOTTA)
    frames.append(_grid_to_image(g7))

    # --- Frame 8: Laptop falling back down from bounce as Clawd winds arms back ---
    g8 = _new_grid()
    _draw_right_side_clawd_facing_left(
        g8, body_left=9, body_top=6, body_width=7, body_height=7, eye_mode="normal", right_ear_dy=-1
    )
    _draw_side_laptop_profile(g8, y_offset=-1, slam_squash=0)
    _rect(g8, 6, 9, 3, 1, TERRACOTTA_DARK)
    _rect(g8, 6, 11, 3, 1, TERRACOTTA)
    frames.append(_grid_to_image(g8))

    # --- Frames 9, 10: Laptop settles at x=0..5, Clawd shoots BOTH 6-px long arms straight out (2f hold)! ---
    for stretch_idx in range(2):
        g_stretch = _new_grid()
        _draw_right_side_clawd_facing_left(
            g_stretch,
            body_left=9,
            body_top=6,
            body_width=7,
            body_height=7,
            eye_mode="squint",
            right_ear_dy=-1,
        )
        _draw_side_laptop_profile(g_stretch, y_offset=0, slam_squash=0)
        if stretch_idx == 0:
            # Arms shooting out across the gap (4 blocks long)
            _rect(g_stretch, 5, 9, 4, 1, TERRACOTTA_DARK)
            _rect(g_stretch, 5, 11, 4, 1, TERRACOTTA)
        else:
            # Both comically long parallel horizontal arms fully extended (6 blocks long, x=3..8)!
            _rect(g_stretch, 3, 9, 6, 1, TERRACOTTA_DARK)
            _rect(g_stretch, 3, 11, 6, 1, TERRACOTTA)
        frames.append(_grid_to_image(g_stretch))

    # --- Frames 11..18 (loop_start=11): Paced, readable 2-handed long-arm typing loop! ---
    # 8 frames at 5.5 FPS: each swing has a clear raised pose and a clear smash pose
    for i in range(8):
        g = _new_grid()
        bob_y = 7 if i in (1, 3, 6) else 6
        bob_h = 6 if i in (1, 3, 6) else 7
        eyes = "blink" if i == 5 else ("squint" if i in (1, 3, 6, 7) else "normal")
        _draw_right_side_clawd_facing_left(
            g,
            body_left=9,
            body_top=bob_y,
            body_width=7,
            body_height=bob_h,
            eye_mode=eyes,
            right_ear_dy=-1,
        )
        pkey = 3 if i in (1, 6) else (5 if i in (3, 7) else None)
        _draw_side_laptop_profile(g, y_offset=0, slam_squash=0, pressed_key_x=pkey)

        if i == 0:
            # Beat 0: Back arm winds up HIGH (y=7..8), front arm poised mid (y=11)
            _rect(g, 5, 9, 4, 1, TERRACOTTA_DARK)
            _rect(g, 3, 7, 3, 2, TERRACOTTA_DARK)
            _rect(g, 4, 11, 5, 1, TERRACOTTA)
        elif i == 1:
            # Beat 1: Front long arm SMASHES key 3 (y=13), back arm stays arched high (y=8)!
            _rect(g, 5, 9, 4, 1, TERRACOTTA_DARK)
            _rect(g, 3, 8, 3, 1, TERRACOTTA_DARK)
            _rect(g, 5, 11, 4, 1, TERRACOTTA)
            _rect(g, 4, 12, 2, 1, TERRACOTTA)
            _rect(g, 3, 13, 2, 1, TERRACOTTA)
            _px(g, 3, 11, STAR_GOLD)
        elif i == 2:
            # Beat 2: Front arm rebounds HIGH (y=8..9), back arm poised to dive
            _rect(g, 4, 9, 5, 1, TERRACOTTA_DARK)
            _rect(g, 5, 11, 4, 1, TERRACOTTA)
            _rect(g, 3, 9, 3, 2, TERRACOTTA)
        elif i == 3:
            # Beat 3: Back long arm SMASHES key 5 (y=13), front arm raised!
            _rect(g, 6, 9, 3, 1, TERRACOTTA_DARK)
            _rect(g, 4, 10, 3, 1, TERRACOTTA_DARK)
            _rect(g, 3, 11, 2, 3, TERRACOTTA_DARK)
            _rect(g, 5, 12, 4, 1, TERRACOTTA)
            _px(g, 5, 10, STAR_GOLD)
        elif i in (4, 5):
            # Beats 4 & 5: Both long noodle arms flung HIGH up in the air (big double wind-up!)
            top_y = 7 if i == 5 else 8
            _rect(g, 5, 8, 4, 1, TERRACOTTA_DARK)
            _rect(g, 3, top_y - 1, 3, 1, TERRACOTTA_DARK)
            _rect(g, 5, 11, 4, 1, TERRACOTTA)
            _rect(g, 3, top_y + 2, 3, 1, TERRACOTTA)
        else:
            # Beats 6 & 7: DOUBLE LONG-ARM SMASH (both 6px arms hammering the keyboard together!)
            _rect(g, 4, 10, 5, 1, TERRACOTTA_DARK)
            _rect(g, 3, 11, 2, 3, TERRACOTTA_DARK)
            _rect(g, 5, 12, 4, 1, TERRACOTTA)
            _rect(g, 4, 13, 3, 1, TERRACOTTA)
            if i == 6:
                _px(g, 2, 8, STAR_GOLD)
                _px(g, 5, 10, LAPTOP_LID)
            else:
                _px(g, 4, 9, STAR_GOLD)

        frames.append(_grid_to_image(g))

    return frames


def _build_climb_vertical() -> list[Image.Image]:
    """Minimalist 4-frame vertical wall climb."""
    frames: list[Image.Image] = []
    specs = [
        (5, (1, 1), (14, 15, 14, 15)),
        (6, (2, 1), (15, 14, 15, 14)),
        (5, (1, 2), (14, 15, 14, 15)),
        (6, (2, 2), (15, 14, 15, 14)),
    ]
    for b_top, (l_ear, r_ear), l_bots in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_left=2,
            body_top=b_top,
            body_width=10,
            body_height=7,
            left_ear_dy=l_ear,
            right_ear_dy=r_ear,
            leg_xs=(2, 4, 9, 11),
            leg_bottoms=l_bots,
            eye_dx=-1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_perch_look_down() -> list[Image.Image]:
    """Minimalist perch on Claude Desktop title bar, peeking down into the window."""
    frames: list[Image.Image] = []
    specs = [
        (7, 6, "normal", 1),
        (8, 5, "normal", 1),
        (7, 6, "normal", 1),
        (7, 6, "blink", 1),
    ]
    for b_top, b_h, eyes, eye_dy in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=b_top,
            body_height=b_h,
            ear_dy=2,
            eye_mode=eyes,
            eye_dy=eye_dy,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_dragged_struggle() -> list[Image.Image]:
    """Minimalist dangling/struggling animation when held by mouse cursor."""
    frames: list[Image.Image] = []
    specs = [
        ((3, 5, 10, 12), (14, 15, 14, 15), 1, 2),
        ((2, 5, 10, 13), (15, 14, 15, 14), 2, 1),
        ((3, 5, 10, 12), (14, 15, 14, 15), 1, 2),
        ((4, 6, 9, 11), (15, 14, 15, 14), 2, 1),
    ]
    for l_xs, l_bots, l_ear, r_ear in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=5,
            body_height=7,
            left_ear_dy=l_ear,
            right_ear_dy=r_ear,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            eye_mode="blink",
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_free_fall() -> list[Image.Image]:
    """Minimalist free-fall with side arms raised up."""
    frames: list[Image.Image] = []
    specs = [
        (5, 0, (13, 14, 14, 13)),
        (6, 1, (14, 13, 13, 14)),
    ]
    for b_top, ear_dy, l_bots in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=b_top,
            body_height=7,
            ear_dy=ear_dy,
            leg_bottoms=l_bots,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_land_squish() -> list[Image.Image]:
    """Minimalist landing squash-and-recover."""
    frames: list[Image.Image] = []
    specs = [
        (2, 9, 12, 5, "blink"),
        (3, 8, 10, 5, "normal"),
        (3, 6, 10, 7, "normal"),
    ]
    for b_left, b_top, b_w, b_h, eyes in specs:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            eye_mode=eyes,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_dizzy() -> list[Image.Image]:
    """Minimalist dizzy state with orbiting pixel stars."""
    frames: list[Image.Image] = []
    star_sets = [
        [(4, 3), (8, 2), (11, 3)],
        [(5, 2), (10, 2), (12, 4)],
        [(4, 4), (8, 3), (11, 2)],
        [(3, 3), (6, 2), (10, 3)],
    ]
    for stars in star_sets:
        g = _new_grid()
        _draw_native_clawd(
            g,
            body_top=7,
            body_height=6,
            eye_mode="dizzy",
        )
        for sx, sy in stars:
            _px(g, sx, sy, STAR_GOLD)
        frames.append(_grid_to_image(g))
    return frames


def generate_all_sprites(output_dir: Path | None = None) -> Path:
    """Generate all native-style sprite strips, manifest.json, and clawdlet.ico."""
    if output_dir is None:
        repo_root = Path(__file__).resolve().parents[3]
        output_dir = repo_root / "assets" / "sprites"

    output_dir.mkdir(parents=True, exist_ok=True)

    animations = {
        "idle_breathe": {"frames": _build_idle_breathe(), "fps": 2.5, "loop": True},
        "idle_blob": {"frames": _build_idle_blob(), "fps": 2.5, "loop": True},
        "walk_horizontal": {"frames": _build_walk_horizontal(), "fps": 6, "loop": True},
        "typing_laptop": {
            "frames": _build_typing_laptop(),
            "fps": 5.5,
            "loop": True,
            "loop_start": 11,
        },
        "climb_vertical": {"frames": _build_climb_vertical(), "fps": 5, "loop": True},
        "perch_look_down": {"frames": _build_perch_look_down(), "fps": 2.5, "loop": True},
        "dragged_struggle": {"frames": _build_dragged_struggle(), "fps": 7, "loop": True},
        "free_fall": {"frames": _build_free_fall(), "fps": 6, "loop": True},
        "land_squish": {"frames": _build_land_squish(), "fps": 9, "loop": False},
        "dizzy": {"frames": _build_dizzy(), "fps": 5, "loop": True},
    }

    manifest: dict[str, object] = {
        "frame_width": FRAME_SIZE,
        "frame_height": FRAME_SIZE,
        "animations": {},
    }

    anim_meta: dict[str, dict[str, object]] = {}
    for name, spec in animations.items():
        frames = spec["frames"]
        strip_img = _make_strip(frames)
        file_name = f"{name}.png"
        strip_img.save(output_dir / file_name, format="PNG")
        anim_meta[name] = {
            "file": file_name,
            "frames": len(frames),
            "fps": spec["fps"],
            "loop": spec["loop"],
            "loop_start": int(spec.get("loop_start", 0)),
        }

    manifest["animations"] = anim_meta
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Generate multi-size Windows icon (clawdlet.ico) from frame 0 of idle_breathe
    icon_base = animations["idle_breathe"]["frames"][0]
    icon_path = output_dir / "clawdlet.ico"
    icon_base.save(
        icon_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    return manifest_path


def main() -> int:
    manifest_path = generate_all_sprites()
    print(f"Generated sprite sheets and manifest at: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
