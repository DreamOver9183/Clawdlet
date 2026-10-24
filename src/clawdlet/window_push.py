"""Opt-in "push the Claude window" mischief (Milestone 5, Hyper mode only, off by default)."""

from __future__ import annotations

from dataclasses import dataclass

from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect

PUSH_SPEED_PX_S = 30.0       # Far below TrackerConfig.shake_speed_threshold, so no fling
PUSH_MAX_DURATION_S = 1.2
PUSH_MAX_DISTANCE_PX = 36.0
PUSH_COOLDOWN_S = 15 * 60.0
PUSH_MIN_USER_IDLE_S = 20.0  # Never shove a window the user is actively working in


@dataclass(frozen=True)
class PushPlan:
    """Where the pet stands to push, and which way the window moves (+1 right, -1 left)."""

    stand_x: float
    direction: int


def plan_window_push(
    *,
    mode: ActivityMode,
    enabled: bool,
    perched: bool,
    win_rect: Rect | None,
    win_maximized: bool,
    body_x: float,
    body_y: float,
    body_w: float,
    body_h: float,
    work_area: Rect,
    idle_s: float | None,
    last_push_t: float | None,
    now: float,
) -> PushPlan | None:
    """Return a push plan when every safety condition holds, else None."""
    if not enabled or mode != ActivityMode.HYPER or perched or win_rect is None or win_maximized:
        return None
    if idle_s is None or idle_s < PUSH_MIN_USER_IDLE_S:
        return None
    if last_push_t is not None and (now - last_push_t) < PUSH_COOLDOWN_S:
        return None
    if win_rect.width >= work_area.width * 0.95 or win_rect.height >= work_area.height * 0.95:
        return None  # Effectively fills the screen

    # The pet must be level with the window's side to lean on it
    overlap = min(win_rect.bottom, body_y + body_h) - max(win_rect.top, body_y)
    if overlap < body_h * 0.5:
        return None

    # Push away from the side the pet is on, and only if the window stays fully on screen
    if body_x + body_w * 0.5 <= win_rect.center_x:
        direction = 1
        stand_x = win_rect.left - body_w
        fits = win_rect.right + PUSH_MAX_DISTANCE_PX <= work_area.right
    else:
        direction = -1
        stand_x = win_rect.right
        fits = win_rect.left - PUSH_MAX_DISTANCE_PX >= work_area.left
    if not fits or stand_x < work_area.left or stand_x + body_w > work_area.right:
        return None
    return PushPlan(stand_x=stand_x, direction=direction)
