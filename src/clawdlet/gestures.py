"""Pure mouse-gesture recognition for Clawdlet's overlay (Milestone 5 touch & combo reactions)."""

from __future__ import annotations

import ctypes
from collections import deque
from enum import Enum


class Gesture(str, Enum):
    """High-level gestures dispatched from the overlay to the controller."""

    DRAG_START = "drag_start"
    POKE = "poke"      # single click
    DOUBLE = "double"  # double click (toggles laptop typing)
    COMBO = "combo"    # rapid 3+ clicks (tantrum)
    PET = "pet"        # back-and-forth stroking without a button held


def system_double_click_s() -> float:
    """Windows double-click interval, clamped to 0.2–0.5 s so pokes never feel sluggish."""
    try:
        ms = int(ctypes.windll.user32.GetDoubleClickTime())
    except Exception:
        ms = 400
    return max(0.2, min(0.5, ms / 1000.0))


class ClickGestureRecognizer:
    """Separates drags from clicks and settles click bursts into POKE / DOUBLE / COMBO.

    A press that moves more than ``drag_threshold_px`` becomes a drag and never counts as a click.
    Click counts settle ``double_click_s`` after the last release: 1 -> POKE, 2 -> DOUBLE. Reaching
    ``combo_count`` clicks fires COMBO at once and swallows further clicks until the burst ends.
    """

    def __init__(
        self,
        double_click_s: float = 0.4,
        drag_threshold_px: float = 4.0,
        combo_count: int = 3,
    ) -> None:
        self.double_click_s = double_click_s
        self.drag_threshold_px = drag_threshold_px
        self.combo_count = combo_count
        self.reset()

    def reset(self) -> None:
        self._pressed = False
        self._dragging = False
        self._press_x = 0.0
        self._press_y = 0.0
        self._clicks = 0
        self._last_release_t = 0.0
        self._combo_fired = False
        self._combo_pending = False

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def press_point(self) -> tuple[float, float]:
        return (self._press_x, self._press_y)

    def press(self, t: float, x: float, y: float) -> None:
        self._pressed = True
        self._dragging = False
        self._press_x = x
        self._press_y = y

    def motion(self, t: float, x: float, y: float) -> Gesture | None:
        if not self._pressed or self._dragging:
            return None
        if abs(x - self._press_x) + abs(y - self._press_y) <= self.drag_threshold_px:
            return None
        self._dragging = True
        # A drag cancels any click burst in progress
        self._clicks = 0
        self._combo_fired = False
        self._combo_pending = False
        return Gesture.DRAG_START

    def release(self, t: float) -> None:
        if not self._pressed:
            return
        self._pressed = False
        if self._dragging:
            self._dragging = False
            return
        self._clicks += 1
        self._last_release_t = t
        if self._clicks >= self.combo_count and not self._combo_fired:
            self._combo_fired = True
            self._combo_pending = True

    def poll(self, t: float) -> Gesture | None:
        if self._combo_pending:
            self._combo_pending = False
            return Gesture.COMBO
        if self._clicks == 0 or self._pressed or t - self._last_release_t < self.double_click_s:
            return None
        clicks = self._clicks
        swallowed = self._combo_fired
        self._clicks = 0
        self._combo_fired = False
        if swallowed:
            return None
        return Gesture.POKE if clicks == 1 else Gesture.DOUBLE


class PetStrokeDetector:
    """Detects stroking: horizontal direction reversals over the pet with no button held."""

    def __init__(
        self,
        min_travel_px: float,
        window_s: float = 1.2,
        min_reversals: int = 4,
        jitter_px: float = 2.0,
    ) -> None:
        self.min_travel_px = min_travel_px
        self.window_s = window_s
        self.min_reversals = min_reversals
        self.jitter_px = jitter_px
        self.reset()

    def reset(self) -> None:
        self._last_x: float | None = None
        self._direction = 0
        self._reversals: deque[float] = deque()
        self._travel: deque[tuple[float, float]] = deque()

    def motion(self, t: float, x: float) -> bool:
        """Feed a cursor sample over the pet; return True when a stroke gesture completes."""
        if self._last_x is None:
            self._last_x = x
            return False
        dx = x - self._last_x
        if abs(dx) < self.jitter_px:
            return False
        self._last_x = x
        direction = 1 if dx > 0 else -1
        if self._direction and direction != self._direction:
            self._reversals.append(t)
        self._direction = direction
        self._travel.append((t, abs(dx)))

        cutoff = t - self.window_s
        while self._reversals and self._reversals[0] < cutoff:
            self._reversals.popleft()
        while self._travel and self._travel[0][0] < cutoff:
            self._travel.popleft()

        travel = sum(d for _, d in self._travel)
        if len(self._reversals) >= self.min_reversals and travel >= self.min_travel_px:
            self._reversals.clear()
            self._travel.clear()
            return True
        return False
