"""2D geometry primitives and physical-to-logical DPI coordinate mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vec2:
    """Immutable 2D vector."""

    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def scale(self, factor: float) -> Vec2:
        return Vec2(self.x * factor, self.y * factor)

    @property
    def magnitude(self) -> float:
        return (self.x * self.x + self.y * self.y) ** 0.5


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in screen coordinates (left, top, right, bottom)."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) * 0.5

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) * 0.5

    def horizontal_overlap(self, other_left: float, other_right: float) -> float:
        """Return horizontal overlap length with interval [other_left, other_right]."""
        start = max(self.left, other_left)
        end = min(self.right, other_right)
        return max(0.0, end - start)

    def clamp_x(self, x: float, entity_width: float) -> float:
        """Clamp horizontal coordinate x so [x, x + entity_width] stays within [left, right]."""
        max_x = max(self.left, self.right - entity_width)
        return max(self.left, min(x, max_x))


class PixelMapper:
    """Converts Win32 physical pixel coordinates into Qt logical coordinates.

    Designed with an optional ``screen_id`` parameter so multi-monitor per-display
    scale factors can be added in future milestones without changing callers.
    """

    def __init__(self, dpr: float = 1.0) -> None:
        if dpr <= 0:
            raise ValueError(f"Device pixel ratio must be positive, got {dpr}")
        self._primary_dpr = float(dpr)
        self._screen_dprs: dict[int, float] = {0: self._primary_dpr}

    @property
    def dpr(self) -> float:
        return self._primary_dpr

    def set_screen_dpr(self, screen_id: int, dpr: float) -> None:
        if dpr <= 0:
            raise ValueError(f"Device pixel ratio must be positive, got {dpr}")
        self._screen_dprs[screen_id] = float(dpr)
        if screen_id == 0:
            self._primary_dpr = float(dpr)

    def get_dpr(self, screen_id: int = 0) -> float:
        return self._screen_dprs.get(screen_id, self._primary_dpr)

    def to_logical_scalar(self, physical_value: float, screen_id: int = 0) -> float:
        return physical_value / self.get_dpr(screen_id)

    def to_physical_scalar(self, logical_value: float, screen_id: int = 0) -> float:
        return logical_value * self.get_dpr(screen_id)

    def to_logical_rect(self, phys_rect: Rect, screen_id: int = 0) -> Rect:
        scale = self.get_dpr(screen_id)
        return Rect(
            left=phys_rect.left / scale,
            top=phys_rect.top / scale,
            right=phys_rect.right / scale,
            bottom=phys_rect.bottom / scale,
        )
