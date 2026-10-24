"""Milestone 5 step 0: pointer sampling, interaction config, and controller-driven frame indices."""

from __future__ import annotations

from pathlib import Path

import pytest

from clawdlet.behavior import ClawdletController
from clawdlet.config import ACTIVITY_PROFILES, ActivityMode, InteractionConfig
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot, idle_seconds_from_ticks, read_pointer_snapshot
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import SpriteSheetBank
from clawdlet.tools.gen_sprites import generate_all_sprites

DT = 1.0 / 60.0


def test_idle_seconds_handles_tick_wraparound() -> None:
    assert idle_seconds_from_ticks(5_000, 2_000) == pytest.approx(3.0)
    # Last input just before the 32-bit wrap, now just after it
    assert idle_seconds_from_ticks(1_000, 0xFFFFFFFF - 999) == pytest.approx(2.0)


def test_read_pointer_snapshot_returns_live_cursor() -> None:
    snap = read_pointer_snapshot()
    assert snap is None or snap.idle_s >= 0.0


def test_activity_profiles_expose_chase_and_pounce() -> None:
    assert ACTIVITY_PROFILES[ActivityMode.QUIET].chase_chance == pytest.approx(0.15)
    assert ACTIVITY_PROFILES[ActivityMode.NORMAL].chase_chance == pytest.approx(0.35)
    assert ACTIVITY_PROFILES[ActivityMode.HYPER].chase_chance == pytest.approx(0.60)
    assert [m for m, p in ACTIVITY_PROFILES.items() if p.pounce_enabled] == [ActivityMode.HYPER]
    cfg = InteractionConfig()
    assert cfg.sleep_after_idle_s == pytest.approx(90.0)
    assert cfg.agent_busy_expiry_s == pytest.approx(180.0)


def test_controller_tracks_cursor_stillness_against_anchor() -> None:
    body = PhysicsBody(x=500.0, y=200.0, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=Rect(0.0, 0.0, 1536.0, 816.0), rng_seed=1)
    for _ in range(30):
        ctrl.tick(DT, None, PointerSnapshot(900.0, 300.0))
    assert ctrl._cursor_still_s == pytest.approx(29 * DT)
    # Slow drift of 1px per frame accumulates past the 3px anchor tolerance and resets stillness
    for i in range(1, 6):
        ctrl.tick(DT, None, PointerSnapshot(900.0 + i, 300.0))
    assert ctrl._cursor_still_s < 3 * DT
    frame = ctrl.tick(DT, None, None)
    assert frame.frame_index is None
    assert ctrl._cursor_still_s == 0.0


def test_get_frame_index_clamps_to_strip(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    bank = SpriteSheetBank(assets_dir=tmp_path, scale=1)
    frames = bank._animations["idle_breathe"].frames
    assert bank.get_frame_index("idle_breathe", 3) is frames[3]
    assert bank.get_frame_index("idle_breathe", 999) is frames[-1]
    assert bank.get_frame_index("idle_breathe", -4) is frames[0]


def test_gaze_frames_shift_eyes_in_a_3x3_grid(tmp_path: Path) -> None:
    generate_all_sprites(tmp_path)
    bank = SpriteSheetBank(assets_dir=tmp_path, scale=1)
    eye = (10, 10, 10, 255)

    def eye_left_x(idx: int) -> int:
        img = bank.get_frame_index("gaze", idx).image
        return min(x for y in range(64) for x in range(64) if img.getpixel((x, y)) == eye)

    def eye_top_y(idx: int) -> int:
        img = bank.get_frame_index("gaze", idx).image
        return min(y for y in range(64) for x in range(64) if img.getpixel((x, y)) == eye)

    assert eye_left_x(3) < eye_left_x(4) < eye_left_x(5)   # look left / center / right
    assert eye_top_y(1) < eye_top_y(4) < eye_top_y(7)      # look up / center / down
