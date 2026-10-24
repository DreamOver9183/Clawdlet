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
