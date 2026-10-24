"""Unit tests for 2D kinematics and one-way platform collision (Concept Doc §3.2)."""

from __future__ import annotations

import pytest

from clawdlet.config import PhysicsConfig
from clawdlet.geometry import Rect
from clawdlet.physics import PhysicsBody, step_physics


def test_kinematic_step_matches_concept_formula() -> None:
    cfg = PhysicsConfig(gravity=2400.0, air_drag=0.99, dt=1.0 / 60.0)
    work_area = Rect(0.0, 0.0, 1536.0, 816.0)
    body = PhysicsBody(x=400.0, y=100.0, width=128.0, height=128.0, vx=300.0, vy=50.0)

    dt = cfg.dt
    expected_vy = 50.0 + 2400.0 * dt
    expected_vx = 300.0 * 0.99
    expected_x = 400.0 + expected_vx * dt
    expected_y = 100.0 + expected_vy * dt

    impact = step_physics(body, dt, cfg, work_area)
    assert not impact.landed
    assert body.vx == pytest.approx(expected_vx)
    assert body.vy == pytest.approx(expected_vy)
    assert body.x == pytest.approx(expected_x)
    assert body.y == pytest.approx(expected_y)


def test_one_way_claude_platform_landing_from_above() -> None:
    cfg = PhysicsConfig()
    work_area = Rect(0.0, 0.0, 1536.0, 816.0)
    claude_rect = Rect(300.0, 400.0, 1100.0, 750.0)

    # Positioned 2px above Claude window top, moving down
    body = PhysicsBody(
        x=500.0,
        y=claude_rect.top - 128.0 - 2.0,
        width=128.0,
        height=128.0,
        vx=0.0,
        vy=300.0,
    )
    impact = step_physics(body, cfg.dt, cfg, work_area, claude_rect)
    assert impact.landed
    assert impact.surface == "claude_window"
    assert body.grounded
    assert body.on_platform_id == "claude_window"
    assert body.bottom == pytest.approx(claude_rect.top)
    assert body.vy == 0.0


def test_one_way_platform_allows_upward_passage() -> None:
    cfg = PhysicsConfig()
    work_area = Rect(0.0, 0.0, 1536.0, 816.0)
    claude_rect = Rect(300.0, 400.0, 1100.0, 750.0)

    # Moving upward through the top border of the Claude window
    body = PhysicsBody(
        x=500.0,
        y=claude_rect.top - 128.0 + 5.0,
        width=128.0,
        height=128.0,
        vx=0.0,
        vy=-900.0,
    )
    impact = step_physics(body, cfg.dt, cfg, work_area, claude_rect)
    assert not impact.landed
    assert body.on_platform_id is None
    assert body.vy < 0.0


def test_wall_bounce_and_floor_collision() -> None:
    cfg = PhysicsConfig(restitution=0.4, bounce_speed_threshold=200.0)
    work_area = Rect(0.0, 0.0, 1536.0, 816.0)

    # Left wall bounce
    body = PhysicsBody(x=2.0, y=300.0, width=128.0, height=128.0, vx=-500.0, vy=0.0)
    impact = step_physics(body, cfg.dt, cfg, work_area)
    assert impact.bounced_wall
    assert body.x == pytest.approx(0.0)
    assert body.vx > 0.0

    # High-speed floor impact -> bounce
    body_floor = PhysicsBody(
        x=500.0,
        y=816.0 - 128.0 - 3.0,
        width=128.0,
        height=128.0,
        vx=0.0,
        vy=600.0,
    )
    floor_impact = step_physics(body_floor, cfg.dt, cfg, work_area)
    assert floor_impact.landed
    assert floor_impact.surface == "floor"
    assert body_floor.bottom == pytest.approx(816.0)
    assert body_floor.vy < 0.0  # Rebounded upward


def test_lands_on_window_whose_top_is_closer_to_screen_top_than_pet_height() -> None:
    """Claude window top at y=110 with a 128px pet: the pet pokes above the work area to perch."""
    cfg = PhysicsConfig()
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    claude_rect = Rect(165.0, 110.0, 1596.0, 924.0)
    body = PhysicsBody(x=816.0, y=claude_rect.top - 128.0, width=128.0, height=128.0)

    impact = step_physics(body, cfg.dt, cfg, work_area, claude_rect)
    assert impact.landed
    assert impact.surface == "claude_window"
    assert body.bottom == pytest.approx(claude_rect.top)
    assert body.y < work_area.top


def test_ceiling_stays_at_work_area_top_away_from_the_window() -> None:
    cfg = PhysicsConfig()
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    claude_rect = Rect(165.0, 110.0, 600.0, 924.0)
    # Horizontally clear of the window: no ceiling allowance
    body = PhysicsBody(x=1200.0, y=-30.0, width=128.0, height=128.0, vy=-200.0)
    step_physics(body, cfg.dt, cfg, work_area, claude_rect)
    assert body.y == pytest.approx(work_area.top)


def test_flung_up_past_window_near_screen_top_falls_back_onto_it() -> None:
    """Thrown upward through a window whose top (y=110) is lower than the pet is tall (128px)."""
    cfg = PhysicsConfig()
    work_area = Rect(0.0, 0.0, 1920.0, 1020.0)
    claude_rect = Rect(165.0, 110.0, 1596.0, 924.0)
    body = PhysicsBody(x=816.0, y=300.0, width=128.0, height=128.0, vy=-1600.0)

    landed_surface = None
    for _ in range(240):
        impact = step_physics(body, cfg.dt, cfg, work_area, claude_rect)
        if impact.landed:
            landed_surface = impact.surface
            break
    assert landed_surface == "claude_window"
    assert body.bottom == pytest.approx(claude_rect.top)
