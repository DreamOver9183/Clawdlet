"""Unit tests for 2D geometry primitives and high-DPI PixelMapper."""

from __future__ import annotations

import pytest

from clawdlet.geometry import PixelMapper, Rect, Vec2


def test_vec2_operations() -> None:
    a = Vec2(3.0, 4.0)
    b = Vec2(1.0, -2.0)
    assert a.magnitude == pytest.approx(5.0)
    assert (a + b) == Vec2(4.0, 2.0)
    assert (a - b) == Vec2(2.0, 6.0)
    assert a.scale(2.5) == Vec2(7.5, 10.0)


def test_rect_dimensions_and_overlap() -> None:
    r = Rect(100.0, 200.0, 500.0, 600.0)
    assert r.width == 400.0
    assert r.height == 400.0
    assert r.center_x == 300.0
    assert r.center_y == 400.0

    # Partial overlap on left edge
    assert r.horizontal_overlap(50.0, 150.0) == pytest.approx(50.0)
    # Full containment
    assert r.horizontal_overlap(200.0, 328.0) == pytest.approx(128.0)
    # Disjoint
    assert r.horizontal_overlap(520.0, 600.0) == 0.0

    # Clamp x inside [left, right - entity_width]
    assert r.clamp_x(80.0, 128.0) == 100.0
    assert r.clamp_x(450.0, 128.0) == 372.0
    assert r.clamp_x(220.0, 128.0) == 220.0


def test_pixel_mapper_dpi_scaling() -> None:
    # 125% Windows scaling (1920x1080 physical -> 1536x864 logical)
    mapper = PixelMapper(dpr=1.25)
    assert mapper.dpr == pytest.approx(1.25)

    phys = Rect(0.0, 0.0, 1920.0, 1020.0)
    logical = mapper.to_logical_rect(phys)
    assert logical == Rect(0.0, 0.0, 1536.0, 816.0)

    assert mapper.to_logical_scalar(250.0) == pytest.approx(200.0)
    assert mapper.to_physical_scalar(200.0) == pytest.approx(250.0)

    # Multi-monitor extension readiness
    mapper.set_screen_dpr(1, 1.5)
    phys_secondary = Rect(1920.0, 0.0, 3840.0, 1080.0)
    logical_secondary = mapper.to_logical_rect(phys_secondary, screen_id=1)
    assert logical_secondary.width == pytest.approx(1280.0)
    assert logical_secondary.height == pytest.approx(720.0)


def test_pixel_mapper_rejects_non_positive_dpr() -> None:
    with pytest.raises(ValueError):
        PixelMapper(0.0)
    with pytest.raises(ValueError):
        PixelMapper(-1.25)
