"""Simplified 2D kinematics and one-way platform collision engine (Concept Doc §3.2)."""

from __future__ import annotations

from dataclasses import dataclass

from clawdlet.config import PhysicsConfig
from clawdlet.geometry import Rect


@dataclass
class PhysicsBody:
    """Mutable 2D rigid body representing Clawdlet's bounding box and velocity."""

    x: float
    y: float
    width: float = 128.0
    height: float = 128.0
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = False
    on_platform_id: str | None = None

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width * 0.5

    @property
    def center_y(self) -> float:
        return self.y + self.height * 0.5


@dataclass(frozen=True)
class ImpactEvent:
    """Collision summary emitted after a single physics integration step."""

    landed: bool = False
    impact_speed: float = 0.0
    surface: str | None = None
    bounced_wall: bool = False


def step_physics(
    body: PhysicsBody,
    dt: float,
    cfg: PhysicsConfig,
    work_area: Rect,
    claude_rect: Rect | None = None,
) -> ImpactEvent:
    """Advance body by ``dt`` seconds using semi-implicit Euler kinematics.

    Equations (Concept Doc §3.2):
        v_y(t + dt) = v_y(t) + g * dt
        v_x(t + dt) = v_x(t) * mu_drag
        P(t + dt)   = P(t) + v(t + dt) * dt
    """
    was_grounded = body.grounded
    prev_bottom = body.bottom

    # 1. Velocity update: gravity + drag
    body.vy += cfg.gravity * dt
    body.vx *= cfg.air_drag
    if was_grounded:
        body.vx *= cfg.ground_friction
        if abs(body.vx) < 2.0:
            body.vx = 0.0

    # 2. Position update
    body.x += body.vx * dt
    body.y += body.vy * dt

    body.grounded = False
    body.on_platform_id = None
    bounced_wall = False
    landed = False
    impact_speed = 0.0
    surface: str | None = None

    # 3. Screen left/right wall collisions
    min_x = work_area.left
    max_x = max(work_area.left, work_area.right - body.width)
    if body.x < min_x:
        body.x = min_x
        if body.vx < 0.0:
            body.vx = -body.vx * cfg.restitution
            bounced_wall = True
    elif body.x > max_x:
        body.x = max_x
        if body.vx > 0.0:
            body.vx = -body.vx * cfg.restitution
            bounced_wall = True

    within_vertical_bounds = False
    has_enough_overlap = False
    if claude_rect is not None:
        within_vertical_bounds = (
            work_area.top + body.height * 0.25 <= claude_rect.top <= work_area.bottom
        )
        overlap_x = claude_rect.horizontal_overlap(body.x, body.right)
        has_enough_overlap = overlap_x >= body.width * cfg.platform_overlap_ratio

    # Ceiling guard. Above a Claude window whose top sits closer to the screen top than the pet is
    # tall, let the pet poke above the work area so it can still rest on (and fall onto) the window.
    ceiling_y = work_area.top
    if claude_rect is not None and within_vertical_bounds and has_enough_overlap:
        ceiling_y = min(ceiling_y, claude_rect.top - body.height)
    if body.y < ceiling_y:
        body.y = ceiling_y
        if body.vy < 0.0:
            body.vy = -body.vy * cfg.restitution

    # 4. One-way platform collision with Claude Desktop window top edge
    if claude_rect is not None:
        plat_y = claude_rect.top
        crossed_from_above = (
            body.vy >= 0.0
            and prev_bottom <= plat_y + 6.0
            and body.bottom >= plat_y
        )
        if within_vertical_bounds and has_enough_overlap and crossed_from_above:
            impact_speed = body.vy
            body.y = plat_y - body.height
            body.vy = 0.0
            body.grounded = True
            body.on_platform_id = "claude_window"
            landed = not was_grounded or impact_speed > 60.0
            surface = "claude_window"
            return ImpactEvent(
                landed=landed,
                impact_speed=impact_speed,
                surface=surface,
                bounced_wall=bounced_wall,
            )

    # 5. Taskbar / work-area floor collision
    floor_y = work_area.bottom
    if body.bottom >= floor_y:
        impact_speed = max(0.0, body.vy)
        body.y = floor_y - body.height
        surface = "floor"
        if not was_grounded and impact_speed > 40.0:
            landed = True

        if impact_speed > cfg.bounce_speed_threshold:
            rebound_vy = -impact_speed * cfg.restitution
            if abs(rebound_vy) >= 95.0:
                body.vy = rebound_vy
                body.grounded = False
                body.on_platform_id = None
            else:
                body.vy = 0.0
                body.grounded = True
                body.on_platform_id = "floor"
        else:
            body.vy = 0.0
            body.grounded = True
            body.on_platform_id = "floor"

    return ImpactEvent(
        landed=landed,
        impact_speed=impact_speed,
        surface=surface,
        bounced_wall=bounced_wall,
    )
