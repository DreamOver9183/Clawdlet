"""Generate polished, expressive 64x64 pixel-art sprite sheets based on docs/clawd.png."""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

# Exact palette sampled from docs/clawd.png:
# - Coral-orange body: RGB(241, 91, 69) (#F15B45)
# - Jet-black square eyes: RGB(10, 10, 10) (#0A0A0A)
TRANSPARENT = (0, 0, 0, 0)
TERRACOTTA = (241, 91, 69, 255)       # Exact docs/clawd.png body color (#F15B45)
TERRACOTTA_DARK = (206, 70, 50, 255)  # Subtle back-limb depth for walk & dual-arm typing
EYE_COLOR = (10, 10, 10, 255)         # Exact docs/clawd.png square eye color (#0A0A0A)

# Laptop & effect palette for the "Claude is thinking / coding" state
LAPTOP_LID = (242, 239, 233, 255)     # Crisp warm silver/white inner '\_' stroke
LAPTOP_EDGE = (180, 174, 164, 255)    # Key-press highlight
LAPTOP_BASE = (28, 28, 27, 255)       # Crisp dark charcoal outer '\_' stroke
STAR_GOLD = (248, 202, 58, 255)       # Pixel star / impact spark gold

# Rich pixel-art explosion palette for Hyper Mode screen-edge laptop crash
EXP_WHITE = (255, 253, 244, 255)      # Blinding white-hot detonation core
EXP_YELLOW = (255, 218, 68, 255)      # Bright golden fireball inner layer
EXP_ORANGE = (255, 128, 34, 255)      # Fiery explosion orange mid layer
EXP_RED = (224, 52, 42, 255)          # Deep crimson blast outer ring
SMOKE_LIGHT = (152, 156, 170, 255)    # Billowing light gray smoke puff
SMOKE_DARK = (78, 82, 96, 255)        # Dark charcoal smoke & scorched soot
ELECTRIC_CYAN = (102, 228, 255, 255)  # Short-circuit electric lightning sparks

# Milestone 5 reaction effect palette
HEART_PINK = (255, 112, 150, 255)     # Petting hearts
SWEAT_BLUE = (120, 196, 255, 255)     # Error sweat drop
ZZZ_WHITE = (232, 238, 255, 255)      # Sleeping Zzz letters
ANGER_RED = (214, 34, 40, 255)        # Flail anger vein mark

# 32x32 grid scaled 2x -> 64x64 base sprite.
# In docs/clawd.png, Clawd is an exact 12-unit wide x 8-unit tall geometric figure.
# Here 1 Clawd unit = 2x2 grid cells (4x4 px), giving exact proportions plus half-unit animation steps!
GRID_SIZE = 32
PIXEL_SCALE = 2
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


def _draw_canonical_clawd(
    grid: list[list[tuple[int, int, int, int]]],
    body_left: int = 8,
    body_top: int = 16,
    body_width: int = 16,
    body_height: int = 12,
    arm_w: int = 4,
    arm_h: int = 4,
    left_arm_dy: int = 4,
    right_arm_dy: int = 4,
    leg_xs: tuple[int, int, int, int] | None = None,
    leg_w: int = 2,
    leg_tops: tuple[int, int, int, int] | None = None,
    leg_bottoms: tuple[int, int, int, int] = (31, 31, 31, 31),
    leg_colors: tuple[tuple[int, int, int, int], ...] | None = None,
    eye_mode: str = "normal",
    eye_dx: int = 0,
    eye_dy: int = 0,
) -> None:
    """Draw Clawd with the exact 12x8 unit geometry from docs/clawd.png (1 unit = 2 cells)."""
    b_bottom = body_top + body_height

    # 1. Four legs with authentic 1u - 2u - 1u gap spacing
    if leg_xs is None:
        leg_xs = (
            body_left,
            body_left + 4,
            body_left + body_width - 6,
            body_left + body_width - 2,
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
            _rect(grid, lx, ly0, leg_w, ly1 - ly0 + 1, leg_colors[idx])

    # 2. Main coral-orange rectangular torso
    _rect(grid, body_left, body_top, body_width, body_height, TERRACOTTA)

    # 3. Chunky 2u x 2u (4x4 cell) side arms
    if arm_w > 0 and arm_h > 0:
        _rect(grid, body_left - arm_w, body_top + left_arm_dy, arm_w, arm_h, TERRACOTTA)
        _rect(grid, body_left + body_width, body_top + right_arm_dy, arm_w, arm_h, TERRACOTTA)

    # 4. Authentic 2x2 square black eyes
    ex_left = body_left + 2 + eye_dx
    ex_right = body_left + body_width - 4 + eye_dx
    ey = body_top + 2 + eye_dy

    if eye_mode == "blink":
        _rect(grid, ex_left, ey + 1, 2, 1, EYE_COLOR)
        _rect(grid, ex_right, ey + 1, 2, 1, EYE_COLOR)
    elif eye_mode == "squint":
        _rect(grid, ex_left, ey, 2, 1, EYE_COLOR)
        _rect(grid, ex_right, ey, 2, 1, EYE_COLOR)
    elif eye_mode == "dizzy_a":
        _px(grid, ex_left, ey, EYE_COLOR)
        _px(grid, ex_left + 1, ey + 1, EYE_COLOR)
        _px(grid, ex_right + 1, ey, EYE_COLOR)
        _px(grid, ex_right, ey + 1, EYE_COLOR)
    elif eye_mode == "dizzy_b":
        _px(grid, ex_left + 1, ey, EYE_COLOR)
        _px(grid, ex_left, ey + 1, EYE_COLOR)
        _px(grid, ex_right, ey, EYE_COLOR)
        _px(grid, ex_right + 1, ey + 1, EYE_COLOR)
    else:
        _rect(grid, ex_left, ey, 2, 2, EYE_COLOR)
        _rect(grid, ex_right, ey, 2, 2, EYE_COLOR)


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
    """8-frame breathing cycle with arm follow-through, tentacle wiggle (Concept Doc §3.1) and a blink."""
    frames: list[Image.Image] = []
    canon_legs = (8, 12, 18, 22)
    grounded = (31, 31, 31, 31)
    # (body_top, body_height, left_arm_dy, right_arm_dy, eye_mode, leg_xs, leg_bottoms)
    specs = [
        (16, 12, 4, 4, "normal", canon_legs, grounded),  # 0: Canonical docs/clawd.png
        (16, 12, 4, 4, "normal", canon_legs, grounded),  # 1: Hold upright
        (17, 11, 3, 3, "normal", canon_legs, grounded),  # 2: Body inhales/dips 1 cell, arms lag slightly up
        (17, 11, 4, 4, "normal", (8, 13, 18, 22), grounded),  # 3: Arms settle; inner-left tentacle sways right
        (16, 12, 5, 5, "normal", canon_legs, grounded),  # 4: Body rises back, arms lag 1 cell low
        (16, 12, 4, 4, "normal", canon_legs, (31, 31, 30, 31)),  # 5: Inner-right tentacle curls up 1 cell
        (16, 12, 4, 4, "blink", canon_legs, grounded),   # 6: Crisp blink
        (16, 12, 4, 4, "normal", canon_legs, grounded),  # 7: Canonical rest
    ]
    for b_top, b_h, l_arm, r_arm, eyes, l_xs, l_bots in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=b_h,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            eye_mode=eyes,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_idle_blob() -> list[Image.Image]:
    """6-frame cozy loaf/blob mode: settled low, arms resting low, sleepy half-lid -> blink -> open."""
    frames: list[Image.Image] = []
    # (body_top, body_height, arm_dy, eye_mode, eye_dy)
    specs = [
        (20, 10, 4, "normal", 1),
        (20, 10, 5, "squint", 2),
        (21, 9, 4, "blink", 1),
        (21, 9, 4, "blink", 1),
        (20, 10, 5, "squint", 2),
        (20, 10, 4, "normal", 1),
    ]
    for b_top, b_h, arm_dy, eyes, eye_dy in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=7,
            body_top=b_top,
            body_width=18,
            body_height=b_h,
            arm_w=4,
            arm_h=4,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode=eyes,
            eye_dy=eye_dy,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_walk_horizontal() -> list[Image.Image]:
    """8-frame smooth trot walk cycle: legs swing horizontally & lift 1 cell (never stubby!) + arm swing."""
    frames: list[Image.Image] = []
    c_main = TERRACOTTA
    c_back = TERRACOTTA_DARK
    # Pair A = legs 0 & 2 (x=8, 18), Pair B = legs 1 & 3 (x=12, 22)
    # Lifted legs reach y=30 (3 cells tall) while grounded legs reach y=31 (4 cells tall)
    specs = [
        # 0: Pair A steps forward (x=9, 19), Pair B pushes back (x=11, 21, lifted 1 cell)
        (16, (9, 11, 19, 21), (31, 30, 31, 30), (c_main, c_back, c_main, c_back), 3, 5),
        # 1: Pair A full stride (x=9, 19), Pair B swinging forward (x=12, 22, lifted 1 cell)
        (16, (9, 12, 19, 22), (31, 30, 31, 30), (c_main, c_back, c_main, c_back), 3, 5),
        # 2: Passing contact dip (body dips 1 cell to y=17, all 4 legs grounded)
        (17, (8, 12, 18, 22), (31, 31, 31, 31), (c_main, c_back, c_main, c_back), 4, 4),
        # 3: Push-off rise
        (16, (8, 13, 18, 23), (30, 31, 30, 31), (c_back, c_main, c_back, c_main), 5, 3),
        # 4: Pair B steps forward (x=13, 23), Pair A swings forward (x=8, 18, lifted 1 cell)
        (16, (7, 13, 17, 23), (30, 31, 30, 31), (c_back, c_main, c_back, c_main), 5, 3),
        # 5: Pair B full stride, Pair A swinging through
        (16, (8, 12, 18, 22), (30, 31, 30, 31), (c_back, c_main, c_back, c_main), 5, 3),
        # 6: Passing contact dip (body dips 1 cell to y=17, all 4 legs grounded)
        (17, (8, 12, 18, 22), (31, 31, 31, 31), (c_back, c_main, c_back, c_main), 4, 4),
        # 7: Push-off recovery
        (16, (9, 12, 19, 22), (31, 30, 31, 30), (c_main, c_back, c_main, c_back), 3, 5),
    ]
    for b_top, l_xs, l_bots, l_cols, l_arm, r_arm in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            leg_colors=l_cols,
            eye_dx=1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _draw_closed_laptop_32(
    grid: list[list[tuple[int, int, int, int]]],
    x0: int,
    y0: int,
    width: int = 8,
) -> None:
    """Draw a sleek, ultra-thin 8x2 folded clamshell laptop (1-cell silver lid + 1-cell dark base)."""
    # Top row: sleek silver aluminum lid with dark hinge bevel on left
    _rect(grid, x0, y0, width, 1, LAPTOP_LID)
    _rect(grid, x0, y0, 1, 1, LAPTOP_BASE)
    # Bottom row: slim dark unibody base
    _rect(grid, x0, y0 + 1, width, 1, LAPTOP_BASE)


def _draw_side_laptop_32(
    grid: list[list[tuple[int, int, int, int]]],
    y_offset: int = 0,
    slam_squash: int = 0,
    pressed_key_group: int | None = None,
) -> None:
    r"""Draw a sleek, ultra-thin side-profile '\_' laptop (x=0..10, y=24..31).

    - Screen lid '\' (2 cells thin: 1-cell aluminum back + 1-cell glowing screen, length = 6.7 cells, y=24..29)
    - Keyboard base '_' (2 cells thin: 1-cell silver deck at y=30 + 1-cell dark base at y=31, length = 7.0 exposed / 8.0 total)
    """
    dy = y_offset
    sy = slam_squash  # +1 on slam impact compresses screen 1 cell lower

    # 1. Razor-thin diagonal '\' screen lid (6 cells tall: y=24+sy..29, only 2 cells thick!)
    steps = [
        (0, 24 + sy),
        (0, 25 + sy),
        (1, 26 + sy),
        (1, 27),
        (2, 28),
        (2, 29),
    ]
    for sx, sy_pos in steps:
        if 23 <= sy_pos <= 29:
            _rect(grid, sx, sy_pos + dy, 1, 1, LAPTOP_BASE)
            _rect(grid, sx + 1, sy_pos + dy, 1, 1, LAPTOP_LID)

    # 2. Ultra-thin 2-row horizontal '_' keyboard deck & chassis (x=3..10, y=30..31)
    # Row 30: silver top deck + hinge at x=3 + 3 low-profile keycap accents at x=5, 7, 9
    _rect(grid, 3, 30 + dy, 8, 1, LAPTOP_LID)
    _rect(grid, 3, 30 + dy, 1, 1, LAPTOP_BASE)
    for k_idx, kx in enumerate((5, 7, 9)):
        if pressed_key_group == k_idx:
            # Pressed key dips into the silver deck
            _rect(grid, kx, 30 + dy, 1, 1, LAPTOP_BASE)
        else:
            # Crisp 1-cell low-profile keycap accent on the thin deck
            _rect(grid, kx, 30 + dy, 1, 1, LAPTOP_EDGE)

    # Row 31: slim 1-cell dark unibody underside flush on y=31
    _rect(grid, 3, 31 + dy, 8, 1, LAPTOP_BASE)


def _draw_right_clawd_32(
    grid: list[list[tuple[int, int, int, int]]],
    body_left: int = 15,
    body_top: int = 16,
    body_width: int = 15,
    body_height: int = 12,
    eye_mode: str = "normal",
    right_arm_w: int = 2,
    right_arm_dy: int = 4,
    leg_bottom: int = 31,
) -> None:
    """Draw docs/clawd.png standing on the right side (x=15..29) facing left with authentic eye spacing."""
    b_bottom = body_top + body_height
    leg_xs = (
        body_left,
        body_left + 4,
        body_left + body_width - 6,
        body_left + body_width - 2,
    )
    leg_cols = (TERRACOTTA, TERRACOTTA_DARK, TERRACOTTA, TERRACOTTA_DARK)
    for lx, lcol in zip(leg_xs, leg_cols):
        if 0 <= lx < GRID_SIZE and b_bottom <= leg_bottom:
            _rect(grid, lx, b_bottom, 2, leg_bottom - b_bottom + 1, lcol)

    # Torso (wide 15x12 rectangle preserving docs/clawd.png boxy ratio!)
    _rect(grid, body_left, body_top, body_width, body_height, TERRACOTTA)

    # Right chunky arm stub (x=30..31)
    if right_arm_w > 0 and body_left + body_width < GRID_SIZE:
        _rect(
            grid,
            body_left + body_width,
            body_top + right_arm_dy,
            right_arm_w,
            4,
            TERRACOTTA,
        )

    # Left-facing 2x2 square black eyes with wide authentic spacing (gap = 6 cells!)
    ex_left = body_left + 2
    ex_right = body_left + 10
    ey = body_top + 2
    if eye_mode == "blink":
        _rect(grid, ex_left, ey + 1, 2, 1, EYE_COLOR)
        _rect(grid, ex_right, ey + 1, 2, 1, EYE_COLOR)
    elif eye_mode == "squint":
        _rect(grid, ex_left, ey, 2, 1, EYE_COLOR)
        _rect(grid, ex_right, ey, 2, 1, EYE_COLOR)
    else:
        _rect(grid, ex_left, ey, 2, 2, EYE_COLOR)
        _rect(grid, ex_right, ey, 2, 2, EYE_COLOR)


def _draw_long_typing_arm(
    grid: list[list[tuple[int, int, int, int]]],
    shoulder_x: int,
    shoulder_y: int,
    hand_x: int,
    hand_y: int,
    color: tuple[int, int, int, int],
) -> None:
    """Draw a continuous, unbroken 2-cell-thick stretched arm ending in a chunky 3x3 paw."""
    x0 = hand_x + 2
    x1 = shoulder_x
    steps = max(1, x1 - x0)
    prev_y = shoulder_y
    for i, x in enumerate(range(x1, x0 - 1, -1)):
        t = i / steps
        cur_y = round(shoulder_y + (hand_y - shoulder_y) * t)
        y_min = min(prev_y, cur_y)
        y_max = max(prev_y, cur_y) + 1
        _rect(grid, x, y_min, 1, y_max - y_min + 1, color)
        prev_y = cur_y
    # Chunky 3x3 paw/hand at the tip
    _rect(grid, hand_x, hand_y, 3, 3, color)


def _build_typing_laptop() -> list[Image.Image]:
    r"""Polished comedic side-profile laptop animation (5.5 FPS, 19 frames):
    Frames 0..10 (11-frame paced intro):
      0..1: Dig folded laptop out of torso
      2..4: Jump/tiptoe & hoist laptop HIGH OVERHEAD with both arms (preserving boxy 15x12 torso!)
      5..6: VIOLENT FULL-FORCE SLAM onto left floor with shockwave & gold sparks!
      7..8: Laptop bounces up into the air from the slam impact while Clawd steps back to right
      9..10: Laptop lands in '\_' at x=1..10, Clawd stretches BOTH long arms with 3x3 paws across the gap!
    Frames 11..18 (loop_start=11, 8-frame readable 2-handed long-arm typing loop):
      Clawd stands on the right (x=15..29) swinging TWO super-long arms with chunky 3x3 paws up and down!
    """
    frames: list[Image.Image] = []

    # Frames 0..2 glide the body right (x=8 -> 10 -> 12) so the intro continues from the idle pose (x=8)
    # --- Frame 0: Reach left chunky hand into belly (half of 8x2 thin folded laptop emerging) ---
    g0 = _new_grid()
    _draw_right_clawd_32(g0, body_left=8, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    _draw_closed_laptop_32(g0, x0=3, y0=21, width=6)
    _rect(g0, 5, 20, 4, 4, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g0))

    # --- Frame 1: Pull full 8x2 thin folded laptop out from torso ---
    g1 = _new_grid()
    _draw_right_clawd_32(g1, body_left=10, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    _draw_closed_laptop_32(g1, x0=1, y0=20, width=8)
    _rect(g1, 7, 19, 4, 4, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g1))

    # --- Frames 2, 3, 4: HUGE OVERHEAD JUMP & WIND-UP HOLD (holding sleek 8x2 folded laptop!) ---
    for quiver_idx in range(3):
        g_up = _new_grid()
        b_top = 14 if quiver_idx != 1 else 13  # Airborne jump at peak of wind-up!
        _draw_right_clawd_32(
            g_up,
            body_left=12,
            body_top=b_top,
            body_width=15,
            body_height=12,
            eye_mode="normal",
            right_arm_w=0,
            leg_bottom=b_top + 15,
        )
        lx = 15 if quiver_idx % 2 == 0 else 14
        # Two raised chunky arms holding the sleek 8x2 folded laptop high above head!
        _rect(g_up, 15, 6, 3, b_top - 6, TERRACOTTA_DARK)
        _rect(g_up, 20, 6, 3, b_top - 6, TERRACOTTA)
        _draw_closed_laptop_32(g_up, x0=lx, y0=4, width=8)
        if quiver_idx == 2:
            _rect(g_up, 26, 2, 2, 2, STAR_GOLD)
        frames.append(_grid_to_image(g_up))

    # --- Frame 5: MASSIVE OVERHEAD SLAM onto the left floor (impact 1)! ---
    g5 = _new_grid()
    _draw_right_clawd_32(
        g5, body_left=15, body_top=20, body_width=15, body_height=9, eye_mode="blink", right_arm_dy=3
    )
    _draw_long_typing_arm(g5, shoulder_x=15, shoulder_y=23, hand_x=6, hand_y=26, color=TERRACOTTA)
    _draw_side_laptop_32(g5, y_offset=0, slam_squash=1, pressed_key_group=1)
    # Vertical slam shockwave speed-lines + bright gold sparks
    _rect(g5, 2, 8, 2, 10, LAPTOP_LID)
    _rect(g5, 8, 10, 2, 10, LAPTOP_LID)
    _rect(g5, 0, 14, 2, 2, STAR_GOLD)
    _rect(g5, 5, 6, 2, 2, STAR_GOLD)
    _rect(g5, 11, 16, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g5))

    # --- Frame 6: SLAM IMPACT HOLD & SHOCKWAVE EXPANSION (impact 2)! ---
    g6 = _new_grid()
    _draw_right_clawd_32(
        g6, body_left=15, body_top=19, body_width=15, body_height=10, eye_mode="normal", right_arm_dy=3
    )
    _draw_long_typing_arm(g6, shoulder_x=15, shoulder_y=23, hand_x=6, hand_y=26, color=TERRACOTTA)
    _draw_side_laptop_32(g6, y_offset=0, slam_squash=1, pressed_key_group=1)
    _rect(g6, 0, 10, 2, 2, STAR_GOLD)
    _rect(g6, 4, 4, 2, 2, STAR_GOLD)
    _rect(g6, 11, 12, 2, 2, STAR_GOLD)
    _rect(g6, 12, 27, 2, 2, LAPTOP_LID)
    frames.append(_grid_to_image(g6))

    # --- Frame 7: Laptop BOUNCES HIGH UP into the air from the violent slam! ---
    g7 = _new_grid()
    _draw_right_clawd_32(g7, body_left=15, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    _draw_side_laptop_32(g7, y_offset=-6, slam_squash=0)
    _rect(g7, 2, 30, 2, 2, LAPTOP_LID)
    _rect(g7, 9, 30, 2, 2, LAPTOP_LID)
    _rect(g7, 0, 11, 2, 2, STAR_GOLD)
    _rect(g7, 12, 19, 3, 3, TERRACOTTA_DARK)
    _rect(g7, 12, 23, 3, 3, TERRACOTTA)
    frames.append(_grid_to_image(g7))

    # --- Frame 8: Laptop dropping back down from bounce ---
    g8 = _new_grid()
    _draw_right_clawd_32(g8, body_left=15, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    _draw_side_laptop_32(g8, y_offset=-2, slam_squash=0)
    _rect(g8, 11, 19, 4, 3, TERRACOTTA_DARK)
    _rect(g8, 11, 23, 4, 3, TERRACOTTA)
    frames.append(_grid_to_image(g8))

    # --- Frames 9, 10: Laptop settles, Clawd stretches BOTH long arms with 3x3 paws across the gap! ---
    for stretch_idx in range(2):
        g_st = _new_grid()
        _draw_right_clawd_32(g_st, body_left=15, body_top=16, body_width=15, body_height=12, eye_mode="normal")
        _draw_side_laptop_32(g_st, y_offset=0, slam_squash=0)
        hx = 9 if stretch_idx == 0 else 6
        _draw_long_typing_arm(g_st, shoulder_x=15, shoulder_y=19, hand_x=hx, hand_y=18, color=TERRACOTTA_DARK)
        _draw_long_typing_arm(g_st, shoulder_x=15, shoulder_y=23, hand_x=hx, hand_y=22, color=TERRACOTTA)
        frames.append(_grid_to_image(g_st))

    # --- Frames 11..18 (loop_start=11): Paced, readable 2-handed long-arm typing loop! ---
    # Each tuple: (back_hand_x, back_hand_y, front_hand_x, front_hand_y, pressed_key, bob_dip, blink, spark_pos)
    loop_poses = [
        # 11: Back paw raised high (y=15), front paw poised mid (y=21)
        (6, 15, 7, 21, None, 0, False, None),
        # 12: Front paw SMASHES left key (x=5, y=27), back paw stays high (y=16)!
        (6, 16, 5, 27, 0, 1, False, (5, 23)),
        # 13: Front paw rebounds high (y=17), back paw poised to dive (y=19)
        (7, 19, 6, 17, None, 0, False, None),
        # 14: Back paw SMASHES right key (x=8, y=27), front paw raised high (y=16)!
        (8, 27, 6, 16, 2, 1, False, (9, 23)),
        # 15: Both long arms wind up HIGH in the air (y=14 and y=19)
        (6, 14, 6, 19, None, 0, False, None),
        # 16: Peak of double wind-up + blink (y=13 and y=18)
        (6, 13, 6, 18, None, 0, True, None),
        # 17: DOUBLE PAW SMASH onto keys 0 & 2 simultaneously (y=27)!
        (5, 27, 8, 27, 0, 1, False, (4, 18)),
        # 18: Rapid follow-up double tap on center key (y=26 & y=27)!
        (7, 27, 6, 26, 1, 1, False, (8, 22)),
    ]
    for back_x, back_y, front_x, front_y, pkey, dip, blink, spark in loop_poses:
        g = _new_grid()
        _draw_right_clawd_32(
            g,
            body_left=15,
            body_top=16 + dip,
            body_width=15,
            body_height=12 - dip,
            eye_mode="blink" if blink else "normal",
            right_arm_dy=3 if dip else 4,
        )
        _draw_side_laptop_32(g, y_offset=0, slam_squash=0, pressed_key_group=pkey)
        _draw_long_typing_arm(
            g, shoulder_x=15, shoulder_y=19 + dip, hand_x=back_x, hand_y=back_y, color=TERRACOTTA_DARK
        )
        _draw_long_typing_arm(
            g, shoulder_x=15, shoulder_y=23 + dip, hand_x=front_x, hand_y=front_y, color=TERRACOTTA
        )
        if spark is not None:
            _rect(g, spark[0], spark[1], 2, 2, STAR_GOLD)
        frames.append(_grid_to_image(g))

    return frames


def _build_put_away_laptop() -> list[Image.Image]:
    r"""Standard/Quiet Mode laptop put-away animation (6 frames at 5.5 FPS, loop=False):
    0: Retract long arms & push the thin '\' laptop screen lid halfway shut
    1: Snap the sleek 8x2 clamshell laptop shut on the floor & bend down to grab it
    2: Lift the folded 8x2 laptop up to chest height
    3: Tuck the folded laptop halfway back into Clawd's belly
    4: Laptop absorbed into belly, patting stomach contentedly
    5: Settle back into canonical docs/clawd.png stance
    """
    frames: list[Image.Image] = []

    # --- Frame 0: Retract arms & push thin '\' screen lid halfway forward to close it ---
    g0 = _new_grid()
    _draw_right_clawd_32(g0, body_left=14, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    # Thin base on floor (x=3..10, y=30..31) + thin 2-cell lid leaning forward (x=3..6, y=26..29)
    _rect(g0, 3, 30, 8, 1, LAPTOP_LID)
    _rect(g0, 3, 31, 8, 1, LAPTOP_BASE)
    for step_i, sy_pos in enumerate((26, 27, 28, 29)):
        sx = 5 - (step_i // 2)
        _rect(g0, sx, sy_pos, 1, 1, LAPTOP_BASE)
        _rect(g0, sx + 1, sy_pos, 1, 1, LAPTOP_LID)
    # Clawd's hand pushing the lid shut
    _draw_long_typing_arm(g0, shoulder_x=14, shoulder_y=21, hand_x=5, hand_y=24, color=TERRACOTTA)
    frames.append(_grid_to_image(g0))

    # --- Frame 1: Sleek 8x2 clamshell snaps shut on floor (y=29..30), Clawd reaches down ---
    g1 = _new_grid()
    _draw_right_clawd_32(g1, body_left=13, body_top=17, body_width=15, body_height=11, eye_mode="normal")
    _draw_closed_laptop_32(g1, x0=3, y0=29, width=8)
    # Tiny crisp "click" highlight pixel as thin lid closes
    _rect(g1, 2, 27, 2, 1, LAPTOP_LID)
    _draw_long_typing_arm(g1, shoulder_x=13, shoulder_y=22, hand_x=8, hand_y=26, color=TERRACOTTA)
    frames.append(_grid_to_image(g1))

    # --- Frame 2: Lift the sleek 8x2 folded laptop up to chest height ---
    g2 = _new_grid()
    _draw_right_clawd_32(g2, body_left=12, body_top=16, body_width=15, body_height=12, eye_mode="normal")
    _draw_closed_laptop_32(g2, x0=3, y0=20, width=8)
    _rect(g2, 9, 19, 4, 4, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g2))

    # --- Frame 3: Push sleek 6x2 folded laptop halfway back into belly ---
    g3 = _new_grid()
    _draw_right_clawd_32(g3, body_left=10, body_top=16, body_width=16, body_height=12, eye_mode="blink")
    _draw_closed_laptop_32(g3, x0=6, y0=21, width=6)
    _rect(g3, 8, 20, 4, 4, TERRACOTTA_DARK)
    frames.append(_grid_to_image(g3))

    # --- Frame 4: Laptop tucked inside belly, left chunky arm patting stomach ---
    g4 = _new_grid()
    _draw_canonical_clawd(
        g4,
        body_left=8,
        body_top=16,
        body_width=16,
        body_height=12,
        left_arm_dy=5,
        right_arm_dy=4,
        eye_mode="normal",
    )
    frames.append(_grid_to_image(g4))

    # --- Frame 5: Canonical docs/clawd.png stance ---
    g5 = _new_grid()
    _draw_canonical_clawd(g5, body_left=8, body_top=16, body_width=16, body_height=12, eye_mode="normal")
    frames.append(_grid_to_image(g5))

    return frames


def _build_kick_laptop() -> list[Image.Image]:
    r"""Hyper Mode (過動模式) laptop put-away animation (7 frames at 5.5 FPS, loop=False):
    Clawd winds up its front-left leg and violently KICKS the thin laptop across the screen to the left monitor edge!
    """
    frames: list[Image.Image] = []

    # --- Frame 0: Wind-up! Clawd leans back on right 3 legs, cocking front-left leg up high ---
    g0 = _new_grid()
    _draw_side_laptop_32(g0, y_offset=0, slam_squash=0)
    # Draw torso & 3 standing legs (18, 23, 27), while front-left leg is cocked up at (12..14, 24..26)!
    _rect(g0, 18, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g0, 23, 28, 2, 4, TERRACOTTA)
    _rect(g0, 27, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g0, 14, 16, 15, 12, TERRACOTTA)
    # Cocked front-left leg raised ready to kick!
    _rect(g0, 12, 24, 3, 3, TERRACOTTA)
    # Both chunky arms raised in wind-up
    _rect(g0, 11, 18, 3, 4, TERRACOTTA_DARK)
    _rect(g0, 29, 18, 3, 4, TERRACOTTA)
    _rect(g0, 16, 18, 2, 2, EYE_COLOR)
    _rect(g0, 24, 18, 2, 2, EYE_COLOR)
    frames.append(_grid_to_image(g0))

    # --- Frame 1: BOOM! Front-left leg KICKS straight out, launching the thin laptop across the screen! ---
    g1 = _new_grid()
    # Sleek thin laptop blasting off the left edge of Clawd's frame (x=0..5, y=18..23)
    for dy_s in range(4):
        _rect(g1, 0, 17 + dy_s, 1, 1, LAPTOP_BASE)
        _rect(g1, 1, 17 + dy_s, 1, 1, LAPTOP_LID)
    _rect(g1, 1, 21, 5, 1, LAPTOP_LID)
    _rect(g1, 1, 22, 5, 1, LAPTOP_BASE)
    # 3 standing legs planted on floor (16, 21, 25)
    _rect(g1, 16, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g1, 21, 28, 2, 4, TERRACOTTA)
    _rect(g1, 25, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g1, 13, 16, 15, 12, TERRACOTTA)
    _rect(g1, 28, 19, 3, 4, TERRACOTTA)
    _rect(g1, 15, 18, 2, 2, EYE_COLOR)
    _rect(g1, 23, 18, 2, 2, EYE_COLOR)
    # Extended front-left kicking leg shooting left (x=6..13, y=22..26)!
    _rect(g1, 8, 24, 5, 3, TERRACOTTA)
    _rect(g1, 6, 22, 3, 5, TERRACOTTA)
    # Explosive golden & white kick impact sparks right at the foot!
    _rect(g1, 5, 18, 2, 2, STAR_GOLD)
    _rect(g1, 8, 20, 2, 2, LAPTOP_LID)
    _rect(g1, 4, 25, 2, 2, STAR_GOLD)
    _rect(g1, 7, 28, 2, 2, LAPTOP_LID)
    frames.append(_grid_to_image(g1))

    # --- Frame 2: High-kick follow-through! Speed lines streak out left as projectile rockets to screen edge! ---
    g2 = _new_grid()
    _rect(g2, 15, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g2, 20, 28, 2, 4, TERRACOTTA)
    _rect(g2, 24, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g2, 12, 16, 15, 12, TERRACOTTA)
    _rect(g2, 27, 18, 3, 4, TERRACOTTA)
    _rect(g2, 14, 18, 2, 2, EYE_COLOR)
    _rect(g2, 22, 18, 2, 2, EYE_COLOR)
    # Front-left leg swung HIGH into the air (martial arts follow-through at x=7..12, y=20..25)!
    _rect(g2, 9, 23, 3, 3, TERRACOTTA)
    _rect(g2, 7, 20, 3, 4, TERRACOTTA)
    # Horizontal sonic-boom speed lines & sparks streaking off the left side (x=0..8)
    _rect(g2, 0, 17, 6, 1, LAPTOP_LID)
    _rect(g2, 0, 21, 5, 1, LAPTOP_LID)
    _rect(g2, 2, 14, 2, 2, STAR_GOLD)
    _rect(g2, 4, 24, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g2))

    # --- Frame 3: Lowering kicking leg while watching the laptop fly across the monitor! ---
    g3 = _new_grid()
    _rect(g3, 14, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g3, 19, 28, 2, 4, TERRACOTTA)
    _rect(g3, 23, 28, 2, 4, TERRACOTTA_DARK)
    _rect(g3, 11, 16, 15, 12, TERRACOTTA)
    _rect(g3, 26, 18, 4, 4, TERRACOTTA)
    _rect(g3, 13, 18, 2, 2, EYE_COLOR)
    _rect(g3, 21, 18, 2, 2, EYE_COLOR)
    _rect(g3, 8, 25, 3, 4, TERRACOTTA)
    frames.append(_grid_to_image(g3))

    # --- Frame 4: Celebratory hop! Clawd jumps with both arms raised high & happy eyes! ---
    g4 = _new_grid()
    _draw_canonical_clawd(
        g4,
        body_left=9,
        body_top=15,
        body_width=16,
        body_height=12,
        left_arm_dy=2,
        right_arm_dy=2,
        leg_bottoms=(29, 30, 30, 29),
        eye_mode="blink",
    )
    frames.append(_grid_to_image(g4))

    # --- Frame 5: Land from celebratory hop ---
    g5 = _new_grid()
    _draw_canonical_clawd(
        g5,
        body_left=8,
        body_top=17,
        body_width=16,
        body_height=11,
        left_arm_dy=4,
        right_arm_dy=4,
        eye_mode="normal",
    )
    frames.append(_grid_to_image(g5))

    # --- Frame 6: Canonical docs/clawd.png stance ---
    g6 = _new_grid()
    _draw_canonical_clawd(g6, body_left=8, body_top=16, body_width=16, body_height=12, eye_mode="normal")
    frames.append(_grid_to_image(g6))

    return frames


def _build_laptop_spin() -> list[Image.Image]:
    """4-frame high-speed spinning thin laptop projectile flying toward the left screen edge (16 FPS, loop=True)."""
    frames: list[Image.Image] = []

    # Frame 0: Open thin '\_' laptop flying left with speed lines trailing on the right
    g0 = _new_grid()
    for i, sy_pos in enumerate(range(13, 19)):
        sx = 8 + (i // 2)
        _rect(g0, sx, sy_pos, 1, 1, LAPTOP_BASE)
        _rect(g0, sx + 1, sy_pos, 1, 1, LAPTOP_LID)
    _rect(g0, 10, 19, 8, 1, LAPTOP_LID)
    _rect(g0, 10, 20, 8, 1, LAPTOP_BASE)
    # Speed lines & spark trailing to the right
    _rect(g0, 20, 15, 8, 1, LAPTOP_LID)
    _rect(g0, 22, 19, 7, 1, LAPTOP_LID)
    _rect(g0, 19, 12, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g0))

    # Frame 1: Rotated 90° ('|_' rotated to '_|') spinning in mid-air
    g1 = _new_grid()
    _rect(g1, 9, 13, 8, 1, LAPTOP_BASE)
    _rect(g1, 9, 14, 8, 1, LAPTOP_LID)
    _rect(g1, 15, 14, 1, 7, LAPTOP_LID)
    _rect(g1, 16, 14, 1, 7, LAPTOP_BASE)
    _rect(g1, 19, 14, 9, 1, LAPTOP_LID)
    _rect(g1, 21, 18, 6, 1, LAPTOP_LID)
    _rect(g1, 18, 20, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g1))

    # Frame 2: Rotated 180° (upside-down thin laptop) spinning rapidly
    g2 = _new_grid()
    _rect(g2, 8, 13, 8, 1, LAPTOP_BASE)
    _rect(g2, 8, 14, 8, 1, LAPTOP_LID)
    for i, sy_pos in enumerate(range(15, 21)):
        sx = 14 + (i // 2)
        _rect(g2, sx, sy_pos, 1, 1, LAPTOP_LID)
        _rect(g2, sx + 1, sy_pos, 1, 1, LAPTOP_BASE)
    _rect(g2, 19, 16, 8, 1, LAPTOP_LID)
    _rect(g2, 21, 13, 6, 1, LAPTOP_LID)
    _rect(g2, 18, 19, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g2))

    # Frame 3: Rotated 270° (vertical spine on left, base extending right)
    g3 = _new_grid()
    _rect(g3, 9, 13, 1, 7, LAPTOP_BASE)
    _rect(g3, 10, 13, 1, 7, LAPTOP_LID)
    _rect(g3, 10, 19, 8, 1, LAPTOP_LID)
    _rect(g3, 10, 20, 8, 1, LAPTOP_BASE)
    _rect(g3, 20, 15, 7, 1, LAPTOP_LID)
    _rect(g3, 19, 18, 9, 1, LAPTOP_LID)
    _rect(g3, 17, 12, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g3))

    return frames


def _build_laptop_wall_hit() -> list[Image.Image]:
    """Rich 14-frame pixel-art explosion when the kicked laptop slams into the left screen edge (x=0) at 8.0 FPS (1.75s)."""
    frames: list[Image.Image] = []

    # --- Frame 0: High-speed impact against left screen edge (x=0) + electric short-circuit! ---
    g0 = _new_grid()
    _rect(g0, 0, 11, 1, 11, LAPTOP_BASE)
    _rect(g0, 1, 12, 1, 9, LAPTOP_LID)
    _rect(g0, 2, 15, 2, 3, LAPTOP_BASE)
    # Blinding impact flash at center + electric cyan lightning & golden sparks
    _rect(g0, 0, 14, 4, 4, EXP_WHITE)
    _rect(g0, 2, 9, 2, 3, ELECTRIC_CYAN)
    _rect(g0, 4, 7, 2, 2, ELECTRIC_CYAN)
    _rect(g0, 2, 21, 2, 3, ELECTRIC_CYAN)
    _rect(g0, 4, 24, 2, 2, ELECTRIC_CYAN)
    _rect(g0, 5, 12, 3, 2, EXP_YELLOW)
    _rect(g0, 5, 19, 3, 2, EXP_YELLOW)
    frames.append(_grid_to_image(g0))

    # --- Frame 1: Blinding core detonation! Laptop cracks apart as fireball erupts from x=0! ---
    g1 = _new_grid()
    # Concentric semicircular blast erupting from left border (x=0..12, y=6..25)
    _rect(g1, 0, 6, 9, 20, EXP_RED)
    _rect(g1, 9, 9, 3, 14, EXP_RED)
    _rect(g1, 0, 8, 8, 16, EXP_ORANGE)
    _rect(g1, 8, 11, 3, 10, EXP_ORANGE)
    _rect(g1, 0, 10, 6, 12, EXP_YELLOW)
    _rect(g1, 6, 12, 3, 8, EXP_YELLOW)
    _rect(g1, 0, 12, 5, 8, EXP_WHITE)
    # Cracked laptop lid & base blown outward + electric arcs
    _rect(g1, 10, 6, 5, 2, LAPTOP_LID)
    _rect(g1, 10, 7, 5, 1, LAPTOP_BASE)
    _rect(g1, 11, 23, 5, 1, LAPTOP_LID)
    _rect(g1, 11, 24, 5, 1, LAPTOP_BASE)
    _rect(g1, 13, 12, 2, 2, ELECTRIC_CYAN)
    _rect(g1, 14, 18, 2, 2, EXP_YELLOW)
    frames.append(_grid_to_image(g1))

    # --- Frame 2: Massive multi-layered fireball expansion + flying keycaps! ---
    g2 = _new_grid()
    # Vertical shockwave streak hugging the monitor edge (x=0..1)
    _rect(g2, 0, 1, 2, 30, EXP_YELLOW)
    # Multi-lobed pixel fireball (x=0..18, y=3..28)
    _rect(g2, 0, 4, 14, 24, EXP_RED)
    _rect(g2, 14, 7, 4, 18, EXP_RED)
    _rect(g2, 0, 6, 12, 20, EXP_ORANGE)
    _rect(g2, 12, 9, 4, 14, EXP_ORANGE)
    _rect(g2, 0, 8, 9, 16, EXP_YELLOW)
    _rect(g2, 9, 11, 4, 10, EXP_YELLOW)
    _rect(g2, 0, 11, 6, 10, EXP_WHITE)
    # Broken laptop halves & keycaps blasted right
    _rect(g2, 16, 4, 5, 1, LAPTOP_LID)
    _rect(g2, 16, 5, 5, 1, LAPTOP_BASE)
    _rect(g2, 17, 25, 5, 1, LAPTOP_LID)
    _rect(g2, 17, 26, 5, 1, LAPTOP_BASE)
    _rect(g2, 20, 11, 2, 2, LAPTOP_BASE)
    _rect(g2, 19, 19, 2, 2, LAPTOP_LID)
    _rect(g2, 21, 15, 2, 2, ELECTRIC_CYAN)
    frames.append(_grid_to_image(g2))

    # --- Frame 3: Peak mushroom fireball + rolling flame clusters + shockwave ring! ---
    g3 = _new_grid()
    # Dark smoke rim forming around outer lobes
    _rect(g3, 2, 2, 12, 4, SMOKE_DARK)
    _rect(g3, 2, 26, 12, 4, SMOKE_DARK)
    _rect(g3, 16, 5, 6, 22, SMOKE_DARK)
    # Three rolling flame lobes (top, middle-right, bottom)
    _rect(g3, 0, 3, 16, 26, EXP_RED)
    _rect(g3, 16, 8, 5, 16, EXP_RED)
    _rect(g3, 0, 5, 14, 22, EXP_ORANGE)
    _rect(g3, 14, 10, 5, 12, EXP_ORANGE)
    # Hot yellow & white sub-cores inside the 3 lobes
    _rect(g3, 2, 6, 7, 6, EXP_YELLOW)
    _rect(g3, 8, 12, 8, 8, EXP_YELLOW)
    _rect(g3, 2, 19, 7, 6, EXP_YELLOW)
    _rect(g3, 4, 8, 3, 3, EXP_WHITE)
    _rect(g3, 10, 14, 4, 4, EXP_WHITE)
    _rect(g3, 4, 21, 3, 3, EXP_WHITE)
    # Tumbling laptop debris & keycaps flying further out (x=21..27)
    _rect(g3, 21, 3, 4, 2, LAPTOP_BASE)
    _rect(g3, 22, 27, 4, 2, LAPTOP_BASE)
    _rect(g3, 24, 10, 2, 2, LAPTOP_LID)
    _rect(g3, 25, 17, 2, 2, ELECTRIC_CYAN)
    _rect(g3, 23, 21, 2, 2, STAR_GOLD)
    frames.append(_grid_to_image(g3))

    # --- Frame 4: Secondary cluster detonations + thick smoke billowing! ---
    g4 = _new_grid()
    # Billowing dark & light smoke body
    _rect(g4, 0, 2, 18, 28, SMOKE_DARK)
    _rect(g4, 18, 6, 6, 20, SMOKE_DARK)
    _rect(g4, 2, 4, 14, 24, SMOKE_LIGHT)
    # Three secondary chain-reaction fireballs popping inside the smoke!
    # Top-left boom
    _rect(g4, 1, 3, 9, 9, EXP_RED)
    _rect(g4, 2, 4, 7, 7, EXP_ORANGE)
    _rect(g4, 3, 5, 5, 5, EXP_YELLOW)
    _rect(g4, 4, 6, 3, 3, EXP_WHITE)
    # Center-right boom
    _rect(g4, 10, 10, 11, 11, EXP_RED)
    _rect(g4, 11, 11, 9, 9, EXP_ORANGE)
    _rect(g4, 13, 13, 5, 5, EXP_YELLOW)
    _rect(g4, 14, 14, 3, 3, EXP_WHITE)
    # Bottom-left boom
    _rect(g4, 2, 19, 9, 9, EXP_RED)
    _rect(g4, 3, 20, 7, 7, EXP_ORANGE)
    _rect(g4, 4, 21, 5, 5, EXP_YELLOW)
    # Electric crackles & falling debris
    _rect(g4, 23, 8, 2, 2, ELECTRIC_CYAN)
    _rect(g4, 26, 14, 2, 2, EXP_YELLOW)
    _rect(g4, 25, 24, 3, 2, LAPTOP_BASE)
    frames.append(_grid_to_image(g4))

    # --- Frame 5: Rolling smoke clouds with glowing orange/red magma cores & spark pop! ---
    g5 = _new_grid()
    # 4 distinct round smoke puffs (top, mid-left, mid-right, bottom)
    for sx, sy, sw, sh in ((1, 2, 12, 10), (0, 11, 13, 10), (11, 8, 13, 12), (2, 19, 13, 10)):
        _rect(g5, sx, sy, sw, sh, SMOKE_DARK)
        _rect(g5, sx + 1, sy + 1, sw - 2, sh - 3, SMOKE_LIGHT)
    # Glowing fiery cores still burning inside the smoke clouds
    _rect(g5, 3, 5, 5, 4, EXP_ORANGE)
    _rect(g5, 4, 6, 3, 2, EXP_YELLOW)
    _rect(g5, 14, 11, 7, 6, EXP_RED)
    _rect(g5, 15, 12, 5, 4, EXP_ORANGE)
    _rect(g5, 16, 13, 3, 2, EXP_YELLOW)
    _rect(g5, 5, 22, 5, 4, EXP_RED)
    _rect(g5, 6, 23, 3, 2, EXP_ORANGE)
    # Sparks & electric arcs outside
    _rect(g5, 25, 11, 2, 2, EXP_YELLOW)
    _rect(g5, 22, 4, 2, 2, ELECTRIC_CYAN)
    _rect(g5, 21, 24, 2, 2, EXP_ORANGE)
    frames.append(_grid_to_image(g5))

    # --- Frame 6: Rising mushroom smoke plume + late secondary spark pop! ---
    g6 = _new_grid()
    for sx, sy, sw, sh in ((2, 1, 12, 9), (1, 10, 11, 9), (12, 6, 13, 11), (4, 18, 12, 9)):
        _rect(g6, sx, sy, sw, sh, SMOKE_DARK)
        _rect(g6, sx + 1, sy + 1, sw - 2, sh - 3, SMOKE_LIGHT)
    # Late mini-pop flash at (15..20, 9..14)
    _rect(g6, 15, 9, 6, 6, EXP_ORANGE)
    _rect(g6, 16, 10, 4, 4, EXP_YELLOW)
    _rect(g6, 17, 11, 2, 2, EXP_WHITE)
    # Glowing red/orange embers
    _rect(g6, 5, 4, 3, 2, EXP_RED)
    _rect(g6, 7, 21, 3, 2, EXP_ORANGE)
    _rect(g6, 26, 8, 2, 2, STAR_GOLD)
    _rect(g6, 23, 19, 2, 2, ELECTRIC_CYAN)
    frames.append(_grid_to_image(g6))

    # --- Frame 7: Smoke clouds separating & drifting upward while golden embers rain down ---
    g7 = _new_grid()
    for sx, sy, sw, sh in ((3, 1, 11, 8), (1, 9, 9, 8), (13, 4, 12, 9), (6, 17, 11, 8)):
        _rect(g7, sx, sy, sw, sh, SMOKE_DARK)
        _rect(g7, sx + 1, sy + 1, sw - 2, sh - 3, SMOKE_LIGHT)
    # Bright embers & pre-star core gathering at (11..15, 11..15)
    _rect(g7, 11, 11, 4, 4, EXP_ORANGE)
    _rect(g7, 12, 12, 2, 2, EXP_WHITE)
    _rect(g7, 5, 19, 2, 2, EXP_RED)
    _rect(g7, 18, 16, 2, 2, EXP_YELLOW)
    _rect(g7, 10, 26, 2, 2, EXP_ORANGE)
    frames.append(_grid_to_image(g7))

    # --- Frame 8: Big comedic anime star-burst finale (★) exploding out of the smoke! ---
    g8 = _new_grid()
    # Dissipating smoke puffs in background
    for sx, sy, sw, sh in ((4, 0, 9, 6), (1, 8, 7, 6), (15, 3, 10, 7), (8, 18, 9, 6)):
        _rect(g8, sx, sy, sw, sh, SMOKE_DARK)
        _rect(g8, sx + 1, sy + 1, sw - 2, sh - 2, SMOKE_LIGHT)
    # Giant golden & white 4-point anime star at (x=6..20, y=6..20, center=12..14, 12..14)!
    _rect(g8, 12, 6, 2, 14, EXP_YELLOW)
    _rect(g8, 6, 12, 14, 2, EXP_YELLOW)
    _rect(g8, 10, 10, 6, 6, EXP_ORANGE)
    _rect(g8, 11, 11, 4, 4, EXP_YELLOW)
    _rect(g8, 12, 12, 2, 2, EXP_WHITE)
    # Diagonal star rays & secondary cyan/gold sparkles
    _rect(g8, 8, 8, 2, 2, EXP_YELLOW)
    _rect(g8, 16, 8, 2, 2, EXP_YELLOW)
    _rect(g8, 8, 16, 2, 2, EXP_YELLOW)
    _rect(g8, 16, 16, 2, 2, EXP_YELLOW)
    _rect(g8, 22, 11, 2, 2, ELECTRIC_CYAN)
    _rect(g8, 4, 21, 2, 2, EXP_ORANGE)
    frames.append(_grid_to_image(g8))

    # --- Frame 9: Triple star twinkle (1 large star + 2 medium stars) & rising smoke rings ---
    g9 = _new_grid()
    for sx, sy, sw, sh in ((5, 0, 7, 5), (17, 2, 8, 5), (10, 19, 7, 5)):
        _rect(g9, sx, sy, sw, sh, SMOKE_DARK)
        _rect(g9, sx + 1, sy + 1, sw - 2, sh - 2, SMOKE_LIGHT)
    # Main star gleaming at (13, 13)
    _rect(g9, 12, 8, 2, 10, EXP_YELLOW)
    _rect(g9, 8, 12, 10, 2, EXP_YELLOW)
    _rect(g9, 11, 11, 4, 4, EXP_WHITE)
    # Second medium star at top-left (4..8, 4..8)
    _rect(g9, 5, 3, 2, 6, EXP_YELLOW)
    _rect(g9, 3, 5, 6, 2, EXP_YELLOW)
    _rect(g9, 5, 5, 2, 2, EXP_WHITE)
    # Third medium star at bottom-right (20..24, 15..19)
    _rect(g9, 21, 14, 2, 6, ELECTRIC_CYAN)
    _rect(g9, 19, 16, 6, 2, ELECTRIC_CYAN)
    _rect(g9, 21, 16, 2, 2, EXP_WHITE)
    frames.append(_grid_to_image(g9))

    # --- Frame 10: Shimmering star cluster & floating soot wisps ---
    g10 = _new_grid()
    _rect(g10, 6, 0, 5, 3, SMOKE_LIGHT)
    _rect(g10, 18, 1, 6, 3, SMOKE_LIGHT)
    _rect(g10, 12, 19, 5, 3, SMOKE_DARK)
    # Three 4-point cross stars twinkling
    for cx, cy, col in ((6, 6, EXP_YELLOW), (13, 12, EXP_YELLOW), (21, 16, EXP_ORANGE)):
        _rect(g10, cx, cy - 2, 2, 6, col)
        _rect(g10, cx - 2, cy, 6, 2, col)
        _rect(g10, cx, cy, 2, 2, EXP_WHITE)
    _rect(g10, 9, 22, 2, 2, EXP_YELLOW)
    frames.append(_grid_to_image(g10))

    # --- Frame 11: Drifting smoke wisps & falling golden/cyan sparkles ---
    g11 = _new_grid()
    _rect(g11, 7, 0, 4, 2, SMOKE_LIGHT)
    _rect(g11, 19, 0, 4, 2, SMOKE_LIGHT)
    _rect(g11, 5, 6, 2, 2, EXP_WHITE)
    _rect(g11, 13, 11, 2, 4, EXP_YELLOW)
    _rect(g11, 12, 12, 4, 2, EXP_YELLOW)
    _rect(g11, 21, 17, 2, 2, ELECTRIC_CYAN)
    _rect(g11, 9, 23, 2, 2, EXP_ORANGE)
    _rect(g11, 16, 20, 2, 2, EXP_YELLOW)
    frames.append(_grid_to_image(g11))

    # --- Frame 12: Lingering afterglow sparkles ---
    g12 = _new_grid()
    _rect(g12, 8, 0, 3, 1, SMOKE_LIGHT)
    _rect(g12, 13, 12, 2, 2, EXP_YELLOW)
    _rect(g12, 6, 8, 2, 2, EXP_YELLOW)
    _rect(g12, 20, 18, 2, 2, EXP_WHITE)
    _rect(g12, 15, 22, 1, 1, EXP_ORANGE)
    frames.append(_grid_to_image(g12))

    # --- Frame 13: Final fading star glint ---
    g13 = _new_grid()
    _rect(g13, 13, 12, 2, 2, EXP_WHITE)
    _rect(g13, 7, 9, 1, 1, EXP_YELLOW)
    _rect(g13, 19, 19, 1, 1, EXP_YELLOW)
    frames.append(_grid_to_image(g13))

    return frames


def _build_climb_vertical() -> list[Image.Image]:
    """6-frame vertical wall climb: left arm reaches up & pulls down while legs step up the wall."""
    frames: list[Image.Image] = []
    c_main = TERRACOTTA
    c_back = TERRACOTTA_DARK
    # (body_top, left_arm_dy, right_arm_dy, leg_bottoms, leg_colors)
    specs = [
        (15, 1, 5, (30, 31, 29, 31), (c_main, c_back, c_main, c_back)),
        (15, 2, 4, (29, 31, 30, 31), (c_main, c_back, c_main, c_back)),
        (16, 4, 3, (31, 31, 31, 31), (c_main, c_main, c_main, c_main)),
        (15, 5, 2, (31, 29, 31, 30), (c_back, c_main, c_back, c_main)),
        (15, 3, 3, (31, 30, 31, 29), (c_back, c_main, c_back, c_main)),
        (16, 2, 4, (31, 31, 31, 31), (c_main, c_main, c_main, c_main)),
    ]
    for b_top, l_arm, r_arm, l_bots, l_cols in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=6,
            body_top=b_top,
            body_width=16,
            body_height=12,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_xs=(6, 10, 16, 20),
            leg_bottoms=l_bots,
            leg_colors=l_cols,
            eye_dx=-1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_perch_look_down() -> list[Image.Image]:
    """6-frame perch lying flat on the Claude Desktop titlebar, peeking down into the window (§5).

    Legs fold to 1 unit and the body widens/lowers into a lying pose; eyes sit in the lower half of
    the face to read as looking down. Unlike idle_blob (sleepy squint, eyes high) the gaze stays alert.
    """
    frames: list[Image.Image] = []
    # (body_top, body_height, left_arm_dy, right_arm_dy, eye_mode, eye_dx, eye_dy)
    specs = [
        (21, 9, 4, 4, "normal", 0, 4),   # 0: Lie flat, peek straight down into the window
        (21, 9, 5, 4, "normal", -1, 4),  # 1: Glance down-left at the chat text
        (22, 8, 4, 4, "normal", 0, 3),   # 2: Lean forward over the edge
        (21, 9, 4, 5, "normal", 1, 4),   # 3: Glance down-right
        (21, 9, 4, 4, "blink", 0, 4),    # 4: Blink
        (21, 9, 4, 4, "normal", 0, 4),   # 5: Peek center
    ]
    for b_top, b_h, l_arm, r_arm, eyes, edx, edy in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=7,
            body_top=b_top,
            body_width=18,
            body_height=b_h,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            eye_mode=eyes,
            eye_dx=edx,
            eye_dy=edy,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_dragged_struggle() -> list[Image.Image]:
    """6-frame dangling struggle when held by the mouse (§5): body stretched 1u taller & 1u narrower,
    arms flailing overhead, long legs kicking in the air."""
    frames: list[Image.Image] = []
    # (body_left, leg_xs, leg_bottoms, left_arm_dy, right_arm_dy, eye_mode)
    specs = [
        (9, (8, 13, 17, 22), (29, 31, 30, 31), 0, 1, "squint"),
        (8, (7, 12, 16, 21), (31, 29, 31, 29), 1, 0, "normal"),
        (9, (9, 13, 17, 21), (30, 31, 29, 30), 0, 1, "squint"),
        (10, (11, 14, 18, 23), (31, 29, 30, 29), 1, 0, "normal"),
        (9, (9, 13, 17, 21), (29, 30, 31, 29), 0, 1, "blink"),
        (9, (8, 13, 16, 22), (31, 29, 29, 31), 1, 0, "squint"),
    ]
    for b_left, l_xs, l_bots, l_arm, r_arm, eyes in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=10,
            body_width=14,
            body_height=14,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            eye_mode=eyes,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_free_fall() -> list[Image.Image]:
    """4-frame free-fall: arms raised high (dy=1..2 so top head corners stay distinct!) & legs fluttering."""
    frames: list[Image.Image] = []
    specs = [
        (14, 1, 2, (29, 31, 30, 29), -1),
        (15, 2, 1, (30, 29, 31, 30), -1),
        (14, 1, 1, (31, 30, 29, 31), 0),
        (15, 2, 2, (29, 31, 31, 29), -1),
    ]
    for b_top, l_arm, r_arm, l_bots, edy in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=12,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_bottoms=l_bots,
            eye_dy=edy,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_land_squish() -> list[Image.Image]:
    """4-frame landing squash -> rebound -> slight vertical stretch -> canonical settle."""
    frames: list[Image.Image] = []
    # (body_left, body_top, body_width, body_height, arm_dy, eye_mode)
    specs = [
        (5, 22, 22, 7, 2, "blink"),    # 0: Wide impact squash
        (7, 18, 18, 10, 3, "squint"),  # 1: Rebound upward
        (9, 15, 14, 13, 5, "normal"),  # 2: Slight vertical overshoot stretch (arms lag low)
        (8, 16, 16, 12, 4, "normal"),  # 3: Canonical docs/clawd.png settle
    ]
    for b_left, b_top, b_w, b_h, arm_dy, eyes in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode=eyes,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_dizzy() -> list[Image.Image]:
    """6-frame dizzy state: body sways left-to-right while 3 golden stars orbit smoothly above head."""
    frames: list[Image.Image] = []
    specs = [
        (8, 17, 4, 4, "dizzy_a", [(8, 10), (15, 8), (22, 10)]),
        (7, 17, 3, 5, "dizzy_a", [(10, 8), (18, 8), (23, 11)]),
        (7, 18, 3, 5, "dizzy_b", [(13, 8), (21, 9), (7, 11)]),
        (8, 17, 4, 4, "dizzy_b", [(16, 8), (22, 10), (8, 10)]),
        (9, 17, 5, 3, "dizzy_a", [(19, 8), (11, 11), (7, 9)]),
        (9, 18, 5, 3, "dizzy_b", [(22, 9), (14, 11), (9, 8)]),
    ]
    for b_left, b_top, l_arm, r_arm, eyes, stars in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            eye_mode=eyes,
        )
        for sx, sy in stars:
            _rect(g, sx, sy, 2, 2, STAR_GOLD)
        frames.append(_grid_to_image(g))
    return frames


# --- Milestone 5 interaction sprites ---------------------------------------------------------------


def _draw_heart(grid: list[list[tuple[int, int, int, int]]], x: int, y: int) -> None:
    """5x4 pixel heart with its top-left corner at (x, y)."""
    for dx in (1, 3):
        _px(grid, x + dx, y, HEART_PINK)
    _rect(grid, x, y + 1, 5, 1, HEART_PINK)
    _rect(grid, x + 1, y + 2, 3, 1, HEART_PINK)
    _px(grid, x + 2, y + 3, HEART_PINK)


def _draw_z(grid: list[list[tuple[int, int, int, int]]], x: int, y: int, size: int = 3) -> None:
    """Small 'Z' letter (size x size) for the sleep animation."""
    _rect(grid, x, y, size, 1, ZZZ_WHITE)
    for i in range(1, size - 1):
        _px(grid, x + size - 1 - i, y + i, ZZZ_WHITE)
    _rect(grid, x, y + size - 1, size, 1, ZZZ_WHITE)


def _draw_bang(grid: list[list[tuple[int, int, int, int]]], x: int, y: int) -> None:
    """2-cell wide golden '!' (5-cell bar, 1-cell gap, 2-cell dot)."""
    _rect(grid, x, y, 2, 5, STAR_GOLD)
    _rect(grid, x, y + 6, 2, 2, STAR_GOLD)


def _build_gaze() -> list[Image.Image]:
    """10 controller-indexed poses: frames 0-8 look in a 3x3 grid ((dy+1)*3+(dx+1)), frame 9 blinks."""
    frames: list[Image.Image] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            g = _new_grid()
            # Arms dip slightly on the side the gaze turns away from, a hint of body lean
            _draw_canonical_clawd(
                g,
                left_arm_dy=4 + (1 if dx > 0 else 0),
                right_arm_dy=4 + (1 if dx < 0 else 0),
                eye_dx=dx,
                eye_dy=dy,
            )
            frames.append(_grid_to_image(g))
    g = _new_grid()
    _draw_canonical_clawd(g, eye_mode="blink")
    frames.append(_grid_to_image(g))
    return frames


def _build_pounce() -> list[Image.Image]:
    """3-frame crouch wind-up before leaping at the cursor: sink low, wiggle, spring up."""
    frames: list[Image.Image] = []
    # (body_left, body_top, body_width, body_height, arm_dy, eye_mode)
    specs = [
        (7, 19, 18, 9, 5, "normal"),
        (6, 21, 20, 8, 6, "squint"),
        (9, 14, 14, 14, 1, "normal"),
    ]
    for b_left, b_top, b_w, b_h, arm_dy, eyes in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode=eyes,
            eye_dy=-1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_poked() -> list[Image.Image]:
    """4-frame flinch when single-clicked: squish away from the poke with a spark, then settle."""
    frames: list[Image.Image] = []
    # (body_left, body_top, body_width, body_height, l_arm, r_arm, eye_mode, spark)
    specs = [
        (9, 18, 15, 10, 1, 2, "blink", True),
        (9, 17, 15, 11, 2, 3, "squint", False),
        (8, 16, 16, 12, 3, 4, "normal", False),
        (8, 16, 16, 12, 4, 4, "normal", False),
    ]
    for b_left, b_top, b_w, b_h, l_arm, r_arm, eyes, spark in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            eye_mode=eyes,
        )
        if spark:
            for sx, sy in ((5, 12), (7, 11), (6, 14)):
                _px(g, sx, sy, STAR_GOLD)
        frames.append(_grid_to_image(g))
    return frames


def _build_petted() -> list[Image.Image]:
    """8-frame happy squint while being stroked: gentle sway with two hearts drifting up."""
    frames: list[Image.Image] = []
    # (body_left, body_top, arm_dy, heart A (x, y) or None, heart B (x, y) or None)
    specs = [
        (8, 17, 4, (19, 10), None),
        (9, 17, 3, (19, 8), None),
        (9, 16, 3, (20, 6), (6, 11)),
        (8, 16, 4, (20, 4), (6, 9)),
        (7, 17, 4, (21, 2), (5, 7)),
        (7, 17, 5, None, (5, 5)),
        (8, 16, 4, (19, 11), (6, 3)),
        (8, 17, 4, (19, 9), None),
    ]
    for b_left, b_top, arm_dy, heart_a, heart_b in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode="squint",
            eye_dy=1,
        )
        for heart in (heart_a, heart_b):
            if heart is not None:
                _draw_heart(g, heart[0], heart[1])
        frames.append(_grid_to_image(g))
    return frames


def _build_flail() -> list[Image.Image]:
    """6-frame tantrum after rapid clicks: arms windmill, legs stamp, angry vein mark pulses."""
    frames: list[Image.Image] = []
    # (body_top, l_arm, r_arm, leg_bottoms, vein)
    specs = [
        (15, 1, 6, (31, 29, 31, 30), True),
        (16, 6, 1, (30, 31, 29, 31), False),
        (15, 1, 5, (31, 30, 31, 29), True),
        (16, 5, 1, (29, 31, 30, 31), False),
        (15, 1, 1, (31, 31, 29, 31), True),
        (16, 6, 6, (31, 29, 31, 31), False),
    ]
    for b_top, l_arm, r_arm, l_bots, vein in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            leg_bottoms=l_bots,
            eye_mode="squint",
        )
        if vein:
            for vx, vy in ((24, 11), (26, 11), (25, 10), (25, 12)):
                _px(g, vx, vy, ANGER_RED)
        frames.append(_grid_to_image(g))
    return frames


def _build_yawn() -> list[Image.Image]:
    """6-frame big yawn & stretch that settles into the low sleeping loaf."""
    frames: list[Image.Image] = []
    # (body_left, body_top, body_width, body_height, arm_dy, eye_mode, eye_dy, mouth_h)
    specs = [
        (8, 16, 16, 12, 4, "normal", 0, 0),
        (8, 15, 16, 13, 2, "squint", 0, 1),
        (9, 13, 14, 15, 1, "blink", 0, 2),
        (9, 13, 14, 15, 1, "blink", 0, 3),
        (8, 17, 16, 11, 4, "blink", 1, 1),
        (7, 20, 18, 10, 4, "squint", 1, 0),
    ]
    for b_left, b_top, b_w, b_h, arm_dy, eyes, edy, mouth_h in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode=eyes,
            eye_dy=edy,
        )
        if mouth_h:
            _rect(g, b_left + b_w // 2 - 2, b_top + 6, 4, mouth_h, EYE_COLOR)
        frames.append(_grid_to_image(g))
    return frames


def _build_sleep() -> list[Image.Image]:
    """8-frame sleeping loaf: slow breathing with closed eyes while Zzz letters float up and away."""
    frames: list[Image.Image] = []
    # (body_top, body_height, small z (x, y) or None, big z (x, y) or None)
    specs = [
        (20, 10, (23, 14), None),
        (20, 10, (24, 12), None),
        (21, 9, (25, 10), (19, 14)),
        (21, 9, (26, 8), (20, 11)),
        (21, 9, None, (21, 8)),
        (20, 10, None, (22, 5)),
        (20, 10, (23, 15), (23, 2)),
        (20, 10, (23, 14), None),
    ]
    for b_top, b_h, small_z, big_z in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=7,
            body_top=b_top,
            body_width=18,
            body_height=b_h,
            left_arm_dy=4,
            right_arm_dy=4,
            eye_mode="blink",
            eye_dy=1,
        )
        if small_z is not None:
            _draw_z(g, small_z[0], small_z[1], size=4)
        if big_z is not None:
            _draw_z(g, big_z[0], big_z[1], size=5)
        frames.append(_grid_to_image(g))
    return frames


def _build_wake_startle() -> list[Image.Image]:
    """4-frame startled wake-up: shoot upright wide-eyed with a '!' then shake it off."""
    frames: list[Image.Image] = []
    # (body_left, body_top, body_width, body_height, arm_dy, eye_mode, eye_dy, bang)
    specs = [
        (9, 13, 14, 15, 1, "normal", -1, True),
        (9, 14, 14, 14, 2, "normal", -1, True),
        (8, 16, 16, 12, 3, "normal", 0, False),
        (8, 16, 16, 12, 4, "blink", 0, False),
    ]
    for b_left, b_top, b_w, b_h, arm_dy, eyes, edy, bang in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=b_top,
            body_width=b_w,
            body_height=b_h,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_mode=eyes,
            eye_dy=edy,
        )
        if bang:
            _draw_bang(g, 26, b_top - 9)
        frames.append(_grid_to_image(g))
    return frames


def _build_peek_edge() -> list[Image.Image]:
    """4-frame hide at the left frame edge: only the right half (one eye + arm) peeks out."""
    frames: list[Image.Image] = []
    # (body_left, right_arm_dy, eye_mode, eye_dx)
    specs = [
        (-8, 4, "normal", 0),
        (-8, 3, "normal", 1),
        (-7, 4, "normal", 1),
        (-8, 4, "blink", 0),
    ]
    for b_left, r_arm, eyes, edx in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            right_arm_dy=r_arm,
            leg_xs=(b_left, b_left + 4, b_left + 10, b_left + 14),
            eye_mode=eyes,
            eye_dx=edx,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_peek_out() -> list[Image.Image]:
    """4-frame lean out of the hiding edge to wave at a nearby cursor."""
    frames: list[Image.Image] = []
    for r_arm in (4, 1, 0, 1):
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=-2,
            right_arm_dy=r_arm,
            leg_xs=(-2, 2, 8, 12),
            eye_dx=1,
        )
        frames.append(_grid_to_image(g))
    return frames


def _build_celebrate() -> list[Image.Image]:
    """8-frame victory dance when Claude Code finishes: bounce, arms pumping, confetti burst."""
    frames: list[Image.Image] = []
    confetti_colors = (STAR_GOLD, HEART_PINK, ELECTRIC_CYAN, ZZZ_WHITE)
    # (body_top, l_arm, r_arm, confetti points)
    specs = [
        (16, 4, 4, [(14, 12), (17, 11)]),
        (14, 1, 1, [(10, 8), (21, 7), (15, 6), (18, 9)]),
        (15, 1, 1, [(6, 6), (25, 5), (13, 3), (20, 4), (9, 10), (23, 10)]),
        (14, 1, 2, [(4, 9), (27, 8), (11, 5), (21, 2), (16, 1)]),
        (16, 2, 1, [(3, 13), (28, 12), (9, 8), (23, 6), (15, 4)]),
        (14, 1, 1, [(3, 16), (28, 15), (8, 11), (24, 10)]),
        (15, 1, 1, [(2, 19), (29, 18), (7, 14)]),
        (16, 3, 3, [(25, 13)]),
    ]
    for b_top, l_arm, r_arm, confetti in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            eye_mode="squint",
        )
        for i, (cx, cy) in enumerate(confetti):
            _px(g, cx, cy, confetti_colors[i % len(confetti_colors)])
        frames.append(_grid_to_image(g))
    return frames


def _build_error_sweat() -> list[Image.Image]:
    """6-frame nervous slump after a tool failure: droopy arms, downcast eyes, sweat drop sliding."""
    frames: list[Image.Image] = []
    # (body_top, arm_dy, eye_dx, sweat y or None, spark)
    specs = [
        (17, 5, 0, 11, True),
        (17, 6, -1, 12, False),
        (18, 6, -1, 14, False),
        (18, 6, 0, 16, True),
        (17, 6, 1, 18, False),
        (17, 5, 0, None, False),
    ]
    for b_top, arm_dy, edx, sweat_y, spark in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=arm_dy,
            right_arm_dy=arm_dy,
            eye_dx=edx,
            eye_dy=1,
        )
        if sweat_y is not None:
            _px(g, 26, sweat_y, SWEAT_BLUE)
            _rect(g, 25, sweat_y + 1, 3, 2, SWEAT_BLUE)
        if spark:
            for sx, sy in ((3, 10), (4, 11), (3, 12)):
                _px(g, sx, sy, ELECTRIC_CYAN)
        frames.append(_grid_to_image(g))
    return frames


def _build_alert() -> list[Image.Image]:
    """4-frame attention loop when Claude Code waits for you: wide eyes and a bobbing golden '!'."""
    frames: list[Image.Image] = []
    # (body_top, l_arm, r_arm, bang_y)
    specs = [
        (16, 4, 2, 4),
        (15, 4, 1, 3),
        (16, 4, 2, 4),
        (16, 4, 3, 5),
    ]
    for b_top, l_arm, r_arm, bang_y in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_top=b_top,
            body_height=28 - b_top,
            left_arm_dy=l_arm,
            right_arm_dy=r_arm,
            eye_dy=-1,
        )
        _draw_bang(g, 15, bang_y)
        frames.append(_grid_to_image(g))
    return frames


def _build_push_window() -> list[Image.Image]:
    """6-frame side-profile shove facing right: pushing arm locked forward, legs braced and stepping."""
    frames: list[Image.Image] = []
    c_main = TERRACOTTA
    c_back = TERRACOTTA_DARK
    # (body_left, leg_xs, leg_bottoms, leg_colors)
    specs = [
        (10, (8, 12, 18, 22), (31, 30, 31, 30), (c_main, c_back, c_main, c_back)),
        (10, (7, 12, 17, 22), (31, 31, 31, 31), (c_main, c_back, c_main, c_back)),
        (11, (8, 13, 18, 23), (30, 31, 30, 31), (c_back, c_main, c_back, c_main)),
        (11, (8, 12, 18, 22), (31, 31, 31, 31), (c_back, c_main, c_back, c_main)),
        (10, (7, 12, 17, 22), (31, 30, 31, 30), (c_main, c_back, c_main, c_back)),
        (11, (8, 13, 18, 23), (31, 31, 31, 31), (c_main, c_main, c_main, c_main)),
    ]
    for b_left, l_xs, l_bots, l_cols in specs:
        g = _new_grid()
        _draw_canonical_clawd(
            g,
            body_left=b_left,
            body_top=17,
            body_width=15,
            body_height=11,
            left_arm_dy=5,
            right_arm_dy=3,
            leg_xs=l_xs,
            leg_bottoms=l_bots,
            leg_colors=l_cols,
            eye_mode="squint",
            eye_dx=1,
        )
        # Stretch the pushing arm all the way to the right frame edge (the window side)
        arm_x = b_left + 15 + 4
        _rect(g, arm_x, 20, GRID_SIZE - arm_x, 3, TERRACOTTA)
        frames.append(_grid_to_image(g))
    return frames


def generate_all_sprites(output_dir: Path | None = None) -> Path:
    """Generate all authentic docs/clawd.png sprite strips, manifest.json, and clawdlet.ico."""
    if output_dir is None:
        repo_root = Path(__file__).resolve().parents[3]
        output_dir = repo_root / "assets" / "sprites"

    output_dir.mkdir(parents=True, exist_ok=True)

    animations = {
        "idle_breathe": {"frames": _build_idle_breathe(), "fps": 4.0, "loop": True},
        "idle_blob": {"frames": _build_idle_blob(), "fps": 3.0, "loop": True},
        "walk_horizontal": {"frames": _build_walk_horizontal(), "fps": 8.0, "loop": True},
        "typing_laptop": {
            "frames": _build_typing_laptop(),
            "fps": 5.5,
            "loop": True,
            "loop_start": 11,
        },
        "put_away_laptop": {
            "frames": _build_put_away_laptop(),
            "fps": 5.5,
            "loop": False,
        },
        "kick_laptop": {
            "frames": _build_kick_laptop(),
            "fps": 5.5,
            "loop": False,
        },
        "laptop_spin": {
            "frames": _build_laptop_spin(),
            "fps": 16.0,
            "loop": True,
        },
        "laptop_wall_hit": {
            "frames": _build_laptop_wall_hit(),
            "fps": 8.0,
            "loop": False,
        },
        "climb_vertical": {"frames": _build_climb_vertical(), "fps": 6.0, "loop": True},
        "perch_look_down": {"frames": _build_perch_look_down(), "fps": 3.0, "loop": True},
        "dragged_struggle": {"frames": _build_dragged_struggle(), "fps": 8.0, "loop": True},
        "free_fall": {"frames": _build_free_fall(), "fps": 8.0, "loop": True},
        "land_squish": {"frames": _build_land_squish(), "fps": 10.0, "loop": False},
        "dizzy": {"frames": _build_dizzy(), "fps": 6.0, "loop": True},
        # Milestone 5 interactions (gaze is always indexed by the controller, never time-based)
        "gaze": {"frames": _build_gaze(), "fps": 1.0, "loop": True},
        "pounce": {"frames": _build_pounce(), "fps": 8.6, "loop": False},
        "poked": {"frames": _build_poked(), "fps": 6.6, "loop": False},
        "petted": {"frames": _build_petted(), "fps": 6.0, "loop": True},
        "flail": {"frames": _build_flail(), "fps": 8.0, "loop": True},
        "yawn": {"frames": _build_yawn(), "fps": 5.0, "loop": False},
        "sleep": {"frames": _build_sleep(), "fps": 3.0, "loop": True},
        "wake_startle": {"frames": _build_wake_startle(), "fps": 6.6, "loop": False},
        "peek_edge": {"frames": _build_peek_edge(), "fps": 2.0, "loop": True},
        "peek_out": {"frames": _build_peek_out(), "fps": 4.0, "loop": True},
        "celebrate": {"frames": _build_celebrate(), "fps": 5.0, "loop": True},
        "error_sweat": {"frames": _build_error_sweat(), "fps": 4.3, "loop": False},
        "alert": {"frames": _build_alert(), "fps": 4.0, "loop": True},
        "push_window": {"frames": _build_push_window(), "fps": 6.0, "loop": True},
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
