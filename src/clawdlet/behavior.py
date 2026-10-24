"""Finite State Machine and window-aware behavior controller (Concept Doc §3.1)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import random

from clawdlet.config import (
    ACTIVITY_PROFILES,
    ActivityMode,
    ActivityProfile,
    PhysicsConfig,
    TrackerConfig,
)
from clawdlet.geometry import Rect, Vec2
from clawdlet.physics import ImpactEvent, PhysicsBody, step_physics


class PetState(str, Enum):
    """Milestone 1–3 behavior states for Clawdlet."""

    IDLE = "idle_breathe"
    BLOB = "idle_blob"
    WANDER = "walk_horizontal"
    TYPING = "typing_laptop"
    PUT_AWAY_LAPTOP = "put_away_laptop"
    KICK_LAPTOP = "kick_laptop"
    CLIMB = "climb_vertical"
    PERCH_CLAUDE = "perch_look_down"
    DRAGGED = "dragged_struggle"
    FALLING = "free_fall"
    THROWN = "thrown"
    LAND_SQUISH = "land_squish"
    DIZZY = "dizzy"

    @property
    def animation_name(self) -> str:
        # THROWN is a distinct FSM state (ends in DIZZY) but shares the free-fall sprite
        if self is PetState.THROWN:
            return PetState.FALLING.value
        return self.value


@dataclass(frozen=True)
class WindowSnapshot:
    """Logical-coordinate state of the tracked Claude Desktop window."""

    hwnd: int
    rect: Rect
    minimized: bool = False
    visible: bool = True
    is_thinking: bool = False

    @property
    def is_perchable(self) -> bool:
        return self.visible and not self.minimized and self.rect.width > 0 and self.rect.height > 0


def drop_y_above_window(
    win_rect: Rect,
    body_height: float,
    work_area: Rect,
    clearance: float,
) -> float:
    """Return a spawn/summon y that drops onto ``win_rect``'s top from up to ``clearance`` px above.

    The feet never start below the window top (otherwise the one-way platform is missed and the pet
    falls through). When the window top is closer to the screen top than the pet is tall, the pet
    starts partly above the work area, matching the physics ceiling allowance.
    """
    rest_y = win_rect.top - body_height
    if win_rect.top < work_area.top + body_height * 0.25:
        # Window top is (nearly) at the screen top, e.g. maximized: no room to perch, drop from the top
        return max(work_area.top, rest_y - clearance)
    highest_y = min(work_area.top, rest_y)
    return max(highest_y, rest_y - clearance)


@dataclass(frozen=True)
class ProjectileRenderState:
    """Output state for the kicked laptop flying across the screen to the monitor edge."""

    x: int
    y: int
    animation_name: str
    elapsed_s: float


@dataclass(frozen=True)
class RenderFrameState:
    """Output snapshot consumed by the transparent overlay GUI each frame."""

    x: int
    y: int
    state: PetState
    animation_name: str
    scale_x: float = 1.0
    scale_y: float = 1.0
    flip_x: bool = False
    vx: float = 0.0
    vy: float = 0.0
    locked_hwnd: int | None = None
    window_speed: float = 0.0
    projectile: ProjectileRenderState | None = None


class ClawdletController:
    """Coordinates the FSM, autonomous wandering/climbing, mouse drag/throw, and Claude window perching."""

    SQUISH_DURATION_S: float = 0.40       # land_squish: 4 frames @ 10 FPS
    DIZZY_DURATION_S: float = 1.80
    PUT_AWAY_DURATION_S: float = 1.10
    KICK_LAPTOP_DURATION_S: float = 1.28
    KICK_RELEASE_S: float = 2.0 / 5.5     # kick_laptop frame 2 @ 5.5 FPS: laptop leaves the foot
    POINTER_HISTORY_WINDOW_S: float = 0.10

    def __init__(
        self,
        body: PhysicsBody,
        work_area: Rect,
        physics_cfg: PhysicsConfig | None = None,
        tracker_cfg: TrackerConfig | None = None,
        activity_mode: ActivityMode = ActivityMode.NORMAL,
        rng_seed: int | None = None,
    ) -> None:
        self.body = body
        self.work_area = work_area
        self.physics_cfg = physics_cfg or PhysicsConfig()
        self.tracker_cfg = tracker_cfg or TrackerConfig()
        self.activity_mode: ActivityMode = activity_mode
        self._rng = random.Random(rng_seed)

        self.state: PetState = PetState.FALLING
        self._state_timer: float = 0.0
        self._decision_duration: float = 2.8
        self._pending_dizzy: bool = False

        # Locomotion & climbing state
        self.facing: int = 1  # +1 right, -1 left
        self.manual_typing: bool = False
        self._seeking_claude: bool = False
        self._climb_surface: str | None = None  # "claude_window" | "screen_wall" | None
        self._climb_wall_side: int = -1         # -1 left edge, +1 right edge

        # Perch tracking state
        self._perched_hwnd: int | None = None
        self._perch_offset_x: float = 0.0
        self._prev_win_rect: Rect | None = None
        self._prev_win_hwnd: int | None = None
        self._win_vel: Vec2 = Vec2(0.0, 0.0)

        # Mouse drag tracking state
        self._drag_anchor_offset: Vec2 = Vec2(0.0, 0.0)
        self._pointer_history: deque[tuple[float, float, float]] = deque()
        self._clock: float = 0.0

        # Kicked laptop cross-screen projectile state (Hyper mode)
        self._proj_spawn_pending: bool = False
        self._proj_active: bool = False
        self._proj_phase: str = "flying"  # "flying" | "wall_hit"
        self._proj_x: float = 0.0
        self._proj_y: float = 0.0
        self._proj_vx: float = 0.0
        self._proj_vy: float = 0.0
        self._proj_elapsed_s: float = 0.0

    @property
    def profile(self) -> ActivityProfile:
        return ACTIVITY_PROFILES[self.activity_mode]

    @property
    def effective_walk_speed(self) -> float:
        return self.physics_cfg.walk_speed * self.profile.speed_multiplier

    @property
    def effective_climb_speed(self) -> float:
        return self.physics_cfg.climb_speed * self.profile.speed_multiplier

    @property
    def effective_seek_weight(self) -> float:
        if self.activity_mode == ActivityMode.NORMAL:
            return self.physics_cfg.claude_seek_weight
        return self.profile.claude_seek_weight

    @property
    def effective_wall_climb_chance(self) -> float:
        if self.activity_mode == ActivityMode.NORMAL:
            return self.physics_cfg.wall_climb_chance
        return self.profile.wall_climb_chance

    def set_work_area(self, work_area: Rect) -> None:
        self.work_area = work_area

    def set_activity_mode(self, mode: ActivityMode) -> None:
        """Switch between QUIET, NORMAL, and HYPER activity profiles."""
        self.activity_mode = mode
        # Shorten remaining wait if switching into a more active mode
        self._decision_duration = min(
            self._decision_duration,
            2.5 * self.profile.rest_duration_multiplier,
        )

    def resize_body(self, new_width: float, new_height: float) -> None:
        """Resize bounding box anchored at the bottom-center (feet level)."""
        old_center_x = self.body.center_x
        old_bottom = self.body.bottom
        self.body.width = new_width
        self.body.height = new_height
        self.body.x = self.work_area.clamp_x(old_center_x - new_width * 0.5, new_width)
        self.body.y = old_bottom - new_height
        if self._prev_win_rect is not None and self._perched_hwnd is not None:
            self.body.x = self._prev_win_rect.clamp_x(self.body.x, new_width)
            self._perch_offset_x = self.body.x - self._prev_win_rect.left

    def summon_to_center(self) -> None:
        """Summon Clawdlet back to the center of the primary screen work area."""
        self._detach_to_fall()
        self.body.x = self.work_area.center_x - self.body.width * 0.5
        self.body.y = max(self.work_area.top + 40.0, self.work_area.center_y - 220.0)
        self.body.vx = 0.0
        self.body.vy = 0.0

    def summon_to_claude(self, win: WindowSnapshot | None) -> None:
        """Summon Clawdlet directly above the active Claude Desktop window (or screen center)."""
        if win is not None and win.is_perchable:
            self._detach_to_fall()
            self.body.x = win.rect.clamp_x(
                win.rect.center_x - self.body.width * 0.5,
                self.body.width,
            )
            self.body.y = drop_y_above_window(win.rect, self.body.height, self.work_area, 110.0)
            self.body.vx = 0.0
            self.body.vy = 0.0
        else:
            self.summon_to_center()

    def hop(self, jump_vy: float = -650.0) -> None:
        """Trigger a playful vertical hop from the current position."""
        self.manual_typing = False
        self._detach_to_fall()
        self.body.vy = jump_vy

    def _start_stop_typing(self) -> None:
        """Transition from TYPING into the mode-appropriate laptop put-away animation."""
        self.manual_typing = False
        self.body.vx = 0.0
        if self.activity_mode == ActivityMode.HYPER:
            self.state = PetState.KICK_LAPTOP
            self._decision_duration = self.KICK_LAPTOP_DURATION_S
            self._proj_spawn_pending = True
        else:
            self.state = PetState.PUT_AWAY_LAPTOP
            self._decision_duration = self.PUT_AWAY_DURATION_S
            self._proj_spawn_pending = False
        self._state_timer = 0.0

    def _tick_projectile(self, dt: float) -> None:
        """Advance the kicked laptop flying across the screen into the left monitor edge."""
        if self._proj_spawn_pending:
            if self.state != PetState.KICK_LAPTOP:
                self._proj_spawn_pending = False
            elif self._state_timer >= self.KICK_RELEASE_S:
                # Frame 2 of kick_laptop: the sprite no longer draws the laptop, launch the projectile!
                self._proj_spawn_pending = False
                self._proj_active = True
                self._proj_phase = "flying"
                self._proj_elapsed_s = 0.0
                self._proj_x = max(self.work_area.left, self.body.x - self.body.width * 0.25)
                self._proj_y = self.body.y + self.body.height * 0.10
                dist_to_left = max(40.0, self._proj_x - self.work_area.left)
                self._proj_vx = -max(1850.0, dist_to_left / 0.34)
                self._proj_vy = -240.0
                return

        if not self._proj_active:
            return

        self._proj_elapsed_s += dt
        if self._proj_phase == "flying":
            self._proj_x += self._proj_vx * dt
            self._proj_y += self._proj_vy * dt
            self._proj_vy += 650.0 * dt
            self._proj_y = max(
                self.work_area.top,
                min(self.work_area.bottom - self.body.height, self._proj_y),
            )
            if self._proj_x <= self.work_area.left:
                self._proj_x = self.work_area.left
                self._proj_phase = "wall_hit"
                self._proj_elapsed_s = 0.0
        else:
            # 14 frames at 8.0 FPS = 1.75s rich explosion duration
            if self._proj_elapsed_s >= 1.78:
                self._proj_active = False

    def toggle_typing(self) -> bool:
        """Toggle manual laptop-typing mode when grounded or perched (plays for ~6s then puts away)."""
        self.manual_typing = not self.manual_typing
        if self.manual_typing and self.state in (PetState.PUT_AWAY_LAPTOP, PetState.KICK_LAPTOP):
            pass  # Let the put-away / kick finish; the rest transition pulls the laptop back out
        elif self.manual_typing and self.body.grounded:
            self._enter_manual_typing()
        elif not self.manual_typing and self.state == PetState.TYPING:
            self._start_stop_typing()
        return self.manual_typing

    def begin_drag(self, cursor_x: float, cursor_y: float) -> None:
        """Grab Clawdlet with the left mouse button."""
        self.manual_typing = False
        self.state = PetState.DRAGGED
        self._state_timer = 0.0
        self._pending_dizzy = False
        self._seeking_claude = False
        self._climb_surface = None
        self._perched_hwnd = None
        self.body.grounded = False
        self.body.on_platform_id = None
        self.body.vx = 0.0
        self.body.vy = 0.0
        self._drag_anchor_offset = Vec2(cursor_x - self.body.x, cursor_y - self.body.y)
        self._pointer_history.clear()
        self._pointer_history.append((self._clock, cursor_x, cursor_y))

    def update_drag(self, cursor_x: float, cursor_y: float) -> None:
        """Move Clawdlet while held by the mouse and record pointer trajectory."""
        if self.state != PetState.DRAGGED:
            return
        dx = (cursor_x - self._drag_anchor_offset.x) - self.body.x
        if abs(dx) > 1.0:
            self.facing = 1 if dx > 0 else -1
        self.body.x = cursor_x - self._drag_anchor_offset.x
        self.body.y = cursor_y - self._drag_anchor_offset.y
        self._pointer_history.append((self._clock, cursor_x, cursor_y))
        cutoff = self._clock - self.POINTER_HISTORY_WINDOW_S
        while len(self._pointer_history) > 2 and self._pointer_history[0][0] < cutoff:
            self._pointer_history.popleft()

    def end_drag(self) -> None:
        """Release Clawdlet, imparting throw velocity calculated from pointer trajectory."""
        if self.state != PetState.DRAGGED:
            return

        vx, vy = self._compute_release_velocity()
        speed = (vx * vx + vy * vy) ** 0.5
        max_s = self.physics_cfg.max_throw_speed
        if speed > max_s and speed > 0.0:
            scale = max_s / speed
            vx *= scale
            vy *= scale
            speed = max_s

        if abs(vx) > 20.0:
            self.facing = 1 if vx > 0 else -1

        self.body.vx = vx
        self.body.vy = vy
        self.body.grounded = False
        self.body.on_platform_id = None

        if speed >= 450.0:
            self.state = PetState.THROWN
            self._pending_dizzy = True
        else:
            self.state = PetState.FALLING
            self._pending_dizzy = False
        self._state_timer = 0.0
        self._pointer_history.clear()

    def _compute_release_velocity(self) -> tuple[float, float]:
        if len(self._pointer_history) < 2:
            return (0.0, 0.0)
        t0, x0, y0 = self._pointer_history[0]
        t1, x1, y1 = self._pointer_history[-1]
        dt = t1 - t0
        if dt <= 1e-4:
            return (0.0, 0.0)
        if self._clock - t1 > 0.08:
            return (0.0, 0.0)
        return ((x1 - x0) / dt, (y1 - y0) / dt)

    def _update_window_velocity(self, dt: float, win: WindowSnapshot | None) -> Vec2:
        if win is None or not win.is_perchable or dt <= 0.0:
            self._prev_win_rect = None
            self._prev_win_hwnd = None
            self._win_vel = Vec2(0.0, 0.0)
            return self._win_vel

        if self._prev_win_rect is not None and self._prev_win_hwnd == win.hwnd:
            raw_vx = (win.rect.left - self._prev_win_rect.left) / dt
            raw_vy = (win.rect.top - self._prev_win_rect.top) / dt
            alpha = self.tracker_cfg.velocity_ema_alpha
            self._win_vel = Vec2(
                x=alpha * raw_vx + (1.0 - alpha) * self._win_vel.x,
                y=alpha * raw_vy + (1.0 - alpha) * self._win_vel.y,
            )
        else:
            self._win_vel = Vec2(0.0, 0.0)

        self._prev_win_rect = win.rect
        self._prev_win_hwnd = win.hwnd
        return self._win_vel

    def _is_claude_climbable(self, win: WindowSnapshot | None) -> bool:
        if win is None or not win.is_perchable:
            return False
        return (
            self.work_area.top + self.body.height * 0.25
            <= win.rect.top
            <= self.work_area.bottom - 16.0
        )

    def _choose_claude_climb_x(self, win_rect: Rect) -> tuple[float, int]:
        """Return (target_x, wall_side) for climbing the closer side of the Claude window."""
        left_x = self.work_area.clamp_x(win_rect.left, self.body.width)
        right_x = self.work_area.clamp_x(win_rect.right - self.body.width, self.body.width)
        if abs(self.body.x - left_x) <= abs(self.body.x - right_x):
            return left_x, -1
        return right_x, 1

    def _start_climb_claude(self, win: WindowSnapshot, wall_side: int) -> None:
        self.state = PetState.CLIMB
        self._state_timer = 0.0
        self._seeking_claude = False
        self._climb_surface = "claude_window"
        self._climb_wall_side = wall_side
        self.facing = wall_side
        self._perched_hwnd = win.hwnd
        self._perch_offset_x = self.body.x - win.rect.left
        self.body.vx = 0.0
        self.body.vy = -self.effective_climb_speed
        self.body.grounded = False
        self.body.on_platform_id = None

    def _start_climb_screen_wall(self, wall_side: int) -> None:
        self.state = PetState.CLIMB
        self._state_timer = 0.0
        self._decision_duration = self._rng.uniform(1.6, 3.0)
        self._seeking_claude = False
        self._climb_surface = "screen_wall"
        self._climb_wall_side = wall_side
        self.facing = wall_side
        self._perched_hwnd = None
        self.body.vx = 0.0
        self.body.vy = -self.effective_climb_speed
        self.body.grounded = False
        self.body.on_platform_id = None

    def _enter_manual_typing(self) -> None:
        self.body.vx = 0.0
        self.state = PetState.TYPING
        self._state_timer = 0.0
        self._decision_duration = 6.0

    def _transition_from_floor_rest(self, win: WindowSnapshot | None) -> None:
        """Choose next autonomous state when IDLE, BLOB, or TYPING timer expires on the floor."""
        if self.manual_typing:
            # Double-clicked again while putting the laptop away: pull it straight back out
            self._enter_manual_typing()
            return
        rest_mult = self.profile.rest_duration_multiplier
        r = self._rng.random()
        if self._is_claude_climbable(win) and r < self.effective_seek_weight:
            assert win is not None
            target_x, wall_side = self._choose_claude_climb_x(win.rect)
            if abs(self.body.x - target_x) <= 6.0:
                self.body.x = target_x
                self._start_climb_claude(win, wall_side)
                return
            self._seeking_claude = True
            self.facing = 1 if target_x > self.body.x else -1
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = 10.0
            return

        self._seeking_claude = False
        roll = self._rng.random()
        if roll < self.profile.wander_probability:
            self.facing = self._rng.choice((-1, 1))
            if self.body.x <= self.work_area.left + 4.0:
                self.facing = 1
            elif self.body.x >= self.work_area.right - self.body.width - 4.0:
                self.facing = -1
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.0, 4.2)
        elif roll < 0.85 and self.state != PetState.BLOB:
            self.state = PetState.BLOB
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.5, 4.5) * rest_mult
        else:
            self.state = PetState.IDLE
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.2, 4.0) * rest_mult

    def _transition_from_perch(self, win: WindowSnapshot) -> None:
        """Choose next autonomous state while perched on Claude Desktop's top border."""
        if self.manual_typing:
            self._enter_manual_typing()
            return
        rest_mult = self.profile.rest_duration_multiplier
        roll = self._rng.random()
        can_stroll = win.rect.width >= self.body.width * 1.4
        wander_cut = min(0.75, self.profile.wander_probability * 0.85)
        if can_stroll and roll < wander_cut and self.state != PetState.WANDER:
            self.facing = self._rng.choice((-1, 1))
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.0, 3.8)
        elif roll < 0.72 and self.state != PetState.BLOB:
            self.state = PetState.BLOB
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.4, 4.2) * rest_mult
        else:
            self.state = PetState.PERCH_CLAUDE
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult

    def _tick_climb(self, dt: float, win: WindowSnapshot | None, win_vel: Vec2) -> RenderFrameState:
        """Advance vertical climbing along Claude Desktop's side edge or the screen wall."""
        climb_spd = self.effective_climb_speed
        if self._climb_surface == "claude_window":
            if win is None or not win.is_perchable:
                self._detach_to_fall()
                return self._build_render_state(win, win_vel)
            if win_vel.magnitude > self.tracker_cfg.shake_speed_threshold:
                self._detach_by_shake(win_vel)
                return self._build_render_state(win, win_vel)

            self.body.x = win.rect.clamp_x(win.rect.left + self._perch_offset_x, self.body.width)
            self.body.y -= climb_spd * dt
            self.body.vx = 0.0
            self.body.vy = -climb_spd

            if self.body.bottom <= win.rect.top + 2.0:
                self.body.y = win.rect.top - self.body.height
                self.body.vy = 0.0
                self.body.grounded = True
                self.body.on_platform_id = "claude_window"
                self._perched_hwnd = win.hwnd
                self._perch_offset_x = self.body.x - win.rect.left
                self._climb_surface = None
                self.state = PetState.PERCH_CLAUDE
                self._state_timer = 0.0
                self._decision_duration = (
                    self._rng.uniform(3.0, 5.5) * self.profile.rest_duration_multiplier
                )
            return self._build_render_state(win, win_vel)

        # Climbing the left or right screen edge
        if self._climb_wall_side < 0:
            self.body.x = self.work_area.left
        else:
            self.body.x = max(self.work_area.left, self.work_area.right - self.body.width)

        self.body.y -= climb_spd * dt
        self.body.vx = 0.0
        self.body.vy = -climb_spd

        if (
            win is not None
            and win.is_perchable
            and win.rect.horizontal_overlap(self.body.x, self.body.right)
            >= self.body.width * self.physics_cfg.platform_overlap_ratio
            and abs(self.body.bottom - win.rect.top) <= 6.0
        ):
            self.body.y = win.rect.top - self.body.height
            self.body.vy = 0.0
            self.body.grounded = True
            self.body.on_platform_id = "claude_window"
            self._perched_hwnd = win.hwnd
            self._perch_offset_x = self.body.x - win.rect.left
            self._climb_surface = None
            self.state = PetState.PERCH_CLAUDE
            self._state_timer = 0.0
            self._decision_duration = (
                self._rng.uniform(3.0, 5.5) * self.profile.rest_duration_multiplier
            )
            return self._build_render_state(win, win_vel)

        if (
            self.body.y <= self.work_area.top + 16.0
            or self._state_timer >= self._decision_duration
        ):
            self._climb_surface = None
            self.facing = -self._climb_wall_side
            self.body.vx = float(self.facing) * 140.0
            self.body.vy = -90.0
            self.body.grounded = False
            self.body.on_platform_id = None
            self.state = PetState.FALLING
            self._state_timer = 0.0

        return self._build_render_state(win, win_vel)

    def _tick_perched_on_claude(
        self,
        dt: float,
        win: WindowSnapshot | None,
        win_vel: Vec2,
    ) -> RenderFrameState | None:
        """Handle states attached to Claude Desktop's top border (PERCH_CLAUDE, WANDER, BLOB, TYPING)."""
        if win is None or not win.is_perchable:
            self._detach_to_fall()
            return None
        if win_vel.magnitude > self.tracker_cfg.shake_speed_threshold:
            self._detach_by_shake(win_vel)
            return None

        if win.is_thinking:
            self.state = PetState.TYPING
            self._state_timer = 0.0
            self._decision_duration = 0.8
        elif self.manual_typing and self.state not in (
            PetState.TYPING,
            PetState.PUT_AWAY_LAPTOP,
            PetState.KICK_LAPTOP,
        ):
            self._enter_manual_typing()
        elif self.state == PetState.WANDER:
            step_dx = float(self.facing) * (self.effective_walk_speed * 0.75) * dt
            self._perch_offset_x += step_dx
            max_offset = max(0.0, win.rect.width - self.body.width)
            if self._perch_offset_x <= 0.0:
                self._perch_offset_x = 0.0
                self.facing = 1
            elif self._perch_offset_x >= max_offset:
                self._perch_offset_x = max_offset
                self.facing = -1

        self.body.x = win.rect.clamp_x(win.rect.left + self._perch_offset_x, self.body.width)
        self.body.y = win.rect.top - self.body.height
        self._perch_offset_x = self.body.x - win.rect.left
        self.body.vx = 0.0
        self.body.vy = 0.0
        self.body.grounded = True
        self.body.on_platform_id = "claude_window"

        if not win.is_thinking and self._state_timer >= self._decision_duration:
            if self.state == PetState.TYPING:
                self._start_stop_typing()
            else:
                self._transition_from_perch(win)

        return self._build_render_state(win, win_vel)

    def _tick_recovering_on_claude(
        self,
        win: WindowSnapshot | None,
        win_vel: Vec2,
    ) -> RenderFrameState | None:
        """Keep LAND_SQUISH / DIZZY pinned to Claude Desktop's top border while the window moves."""
        if win is None or not win.is_perchable:
            self._detach_to_fall()
            return None
        if win_vel.magnitude > self.tracker_cfg.shake_speed_threshold:
            self._detach_by_shake(win_vel)
            return None

        self.body.x = win.rect.clamp_x(win.rect.left + self._perch_offset_x, self.body.width)
        self.body.y = win.rect.top - self.body.height
        self._perch_offset_x = self.body.x - win.rect.left
        self.body.vx = 0.0
        self.body.vy = 0.0
        self.body.grounded = True
        self.body.on_platform_id = "claude_window"

        rest_mult = self.profile.rest_duration_multiplier
        if self.state == PetState.LAND_SQUISH and self._state_timer >= self.SQUISH_DURATION_S:
            if self._pending_dizzy:
                self._pending_dizzy = False
                self.state = PetState.DIZZY
                self._state_timer = 0.0
            else:
                self.state = PetState.PERCH_CLAUDE
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult
        elif self.state == PetState.DIZZY and self._state_timer >= self.DIZZY_DURATION_S:
            self.state = PetState.PERCH_CLAUDE
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.5, 4.5) * rest_mult

        return self._build_render_state(win, win_vel)

    def tick(self, dt: float, win: WindowSnapshot | None = None) -> RenderFrameState:
        """Advance FSM and physics by ``dt`` seconds."""
        self._clock += dt
        self._state_timer += dt
        self._tick_projectile(dt)

        win_vel = self._update_window_velocity(dt, win)
        perchable_rect = win.rect if (win is not None and win.is_perchable) else None

        # 1. Handle DRAGGED state (physics suspended while held)
        if self.state == PetState.DRAGGED:
            return self._build_render_state(win, win_vel)

        # 2. Handle CLIMB state (gravity suspended while gripping edge)
        if self.state == PetState.CLIMB:
            return self._tick_climb(dt, win, win_vel)

        # 3. Handle states perched on Claude Desktop's top border
        if self._perched_hwnd is not None and self.state in (
            PetState.PERCH_CLAUDE,
            PetState.WANDER,
            PetState.BLOB,
            PetState.TYPING,
            PetState.PUT_AWAY_LAPTOP,
            PetState.KICK_LAPTOP,
        ):
            perched_render = self._tick_perched_on_claude(dt, win, win_vel)
            if perched_render is not None:
                return perched_render

        # 3a. Landing squish / dizzy on Claude Desktop's top border: follow the window until recovered
        if self._perched_hwnd is not None and self.state in (PetState.LAND_SQUISH, PetState.DIZZY):
            recovering_render = self._tick_recovering_on_claude(win, win_vel)
            if recovering_render is not None:
                return recovering_render

        # 3b. If grounded on floor and Claude is actively thinking (or manual typing just toggled), type!
        # Landing squish and dizzy always play out first.
        claude_thinking = bool(win is not None and win.is_thinking)
        if (
            self.body.grounded
            and self._perched_hwnd is None
            and self.state not in (PetState.LAND_SQUISH, PetState.DIZZY)
        ):
            if claude_thinking:
                self.body.vx = 0.0
                self.body.vy = 0.0
                self.state = PetState.TYPING
                self._state_timer = 0.0
                self._decision_duration = 0.8
                return self._build_render_state(win, win_vel)
            if self.manual_typing and self.state not in (
                PetState.TYPING,
                PetState.PUT_AWAY_LAPTOP,
                PetState.KICK_LAPTOP,
            ):
                self.body.vx = 0.0
                self.body.vy = 0.0
                self.state = PetState.TYPING
                self._state_timer = 0.0
                self._decision_duration = 6.0
                return self._build_render_state(win, win_vel)

        # 4. Handle floor WANDER horizontal velocity before physics integration
        walk_spd = self.effective_walk_speed
        if self.state == PetState.WANDER and self.body.grounded and self._perched_hwnd is None:
            if self._seeking_claude and self._is_claude_climbable(win):
                assert win is not None
                target_x, wall_side = self._choose_claude_climb_x(win.rect)
                dist = target_x - self.body.x
                step_dist = walk_spd * dt
                if abs(dist) <= max(6.0, step_dist * 1.5):
                    self.body.x = target_x
                    self._start_climb_claude(win, wall_side)
                    return self._build_render_state(win, win_vel)
                self.facing = 1 if dist > 0 else -1
            self.body.vx = float(self.facing) * walk_spd

        # 5. Step 2D physics for airborne or floor-grounded states
        impact: ImpactEvent = step_physics(
            body=self.body,
            dt=dt,
            cfg=self.physics_cfg,
            work_area=self.work_area,
            claude_rect=perchable_rect,
        )

        rest_mult = self.profile.rest_duration_multiplier

        # 6. Evaluate state transitions after physics step
        if impact.landed:
            self._seeking_claude = False

            if impact.surface == "claude_window" and win is not None and win.is_perchable:
                self._perched_hwnd = win.hwnd
                self._perch_offset_x = self.body.x - win.rect.left
            else:
                self._perched_hwnd = None

            self.state = PetState.LAND_SQUISH
            self._state_timer = 0.0
        elif self.state == PetState.LAND_SQUISH:
            if self._state_timer >= self.SQUISH_DURATION_S:
                if self._pending_dizzy and self.body.grounded:
                    self._pending_dizzy = False
                    self.state = PetState.DIZZY
                    self._state_timer = 0.0
                elif self.body.on_platform_id == "claude_window" and win is not None and win.is_perchable:
                    self.state = PetState.PERCH_CLAUDE
                    self._perched_hwnd = win.hwnd
                    self._perch_offset_x = self.body.x - win.rect.left
                    self._state_timer = 0.0
                    self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult
                elif self.body.grounded:
                    self.state = PetState.IDLE
                    self._state_timer = 0.0
                    self._decision_duration = self._rng.uniform(2.2, 4.0) * rest_mult
                else:
                    self.state = PetState.THROWN if self._pending_dizzy else PetState.FALLING
                    self._state_timer = 0.0
        elif self.state == PetState.DIZZY:
            if not self.body.grounded:
                self.state = PetState.FALLING
                self._state_timer = 0.0
            elif self._state_timer >= self.DIZZY_DURATION_S:
                if self.body.on_platform_id == "claude_window" and win is not None and win.is_perchable:
                    self.state = PetState.PERCH_CLAUDE
                    self._perched_hwnd = win.hwnd
                    self._perch_offset_x = self.body.x - win.rect.left
                else:
                    self.state = PetState.IDLE
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(2.5, 4.5) * rest_mult
        elif self.body.grounded:
            if self._pending_dizzy:
                self._pending_dizzy = False
                self.state = PetState.DIZZY
                self._state_timer = 0.0
            elif self.body.on_platform_id == "claude_window" and win is not None and win.is_perchable:
                self.state = PetState.PERCH_CLAUDE
                self._perched_hwnd = win.hwnd
                self._perch_offset_x = self.body.x - win.rect.left
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult
            elif self.state == PetState.WANDER:
                at_left_wall = self.body.x <= self.work_area.left + 1.0 and self.facing < 0
                at_right_wall = (
                    self.body.x >= self.work_area.right - self.body.width - 1.0 and self.facing > 0
                )
                if at_left_wall or at_right_wall:
                    wall_side = -1 if at_left_wall else 1
                    if self._rng.random() < self.effective_wall_climb_chance:
                        self._start_climb_screen_wall(wall_side)
                    else:
                        self.facing = -wall_side
                elif self._state_timer >= self._decision_duration:
                    self.body.vx = 0.0
                    self.state = PetState.IDLE
                    self._state_timer = 0.0
                    self._decision_duration = self._rng.uniform(2.0, 4.0) * rest_mult
            elif self.state == PetState.TYPING:
                if self._state_timer >= self._decision_duration:
                    self._start_stop_typing()
            elif self.state in (
                PetState.IDLE,
                PetState.BLOB,
                PetState.PUT_AWAY_LAPTOP,
                PetState.KICK_LAPTOP,
            ):
                if self._state_timer >= self._decision_duration:
                    self._transition_from_floor_rest(win)
            else:
                self.state = PetState.IDLE
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(2.2, 4.0) * rest_mult
        else:
            if self.state not in (PetState.FALLING, PetState.THROWN):
                self.state = PetState.FALLING
                self._state_timer = 0.0

        return self._build_render_state(win, win_vel)

    def _detach_to_fall(self) -> None:
        self._perched_hwnd = None
        self._climb_surface = None
        self._seeking_claude = False
        self.body.grounded = False
        self.body.on_platform_id = None
        self.body.vy = max(self.body.vy, 20.0)
        self.state = PetState.FALLING
        self._pending_dizzy = False
        self._state_timer = 0.0
        self.manual_typing = False

    def _detach_by_shake(self, win_vel: Vec2) -> None:
        self.manual_typing = False
        self._perched_hwnd = None
        self._climb_surface = None
        self._seeking_claude = False
        self.body.grounded = False
        self.body.on_platform_id = None
        max_s = self.physics_cfg.max_throw_speed
        vx = max(-max_s, min(max_s, win_vel.x * 0.85))
        vy = min(-280.0, win_vel.y * 0.45 - 240.0)
        self.body.vx = vx
        self.body.vy = vy
        self.state = PetState.THROWN
        self._pending_dizzy = True
        self._state_timer = 0.0

    def _build_render_state(self, win: WindowSnapshot | None, win_vel: Vec2) -> RenderFrameState:
        # Squash & stretch is hand-drawn in land_squish; extra runtime scaling would clip the sprite
        scale_x = 1.0
        scale_y = 1.0

        if self.state == PetState.CLIMB:
            flip_x = self._climb_wall_side > 0
        elif self.state in (
            PetState.TYPING,
            PetState.PUT_AWAY_LAPTOP,
            PetState.KICK_LAPTOP,
        ):
            flip_x = False
        else:
            flip_x = self.facing < 0

        proj_state: ProjectileRenderState | None = None
        if self._proj_active:
            proj_anim = "laptop_spin" if self._proj_phase == "flying" else "laptop_wall_hit"
            proj_state = ProjectileRenderState(
                x=round(self._proj_x),
                y=round(self._proj_y),
                animation_name=proj_anim,
                elapsed_s=self._proj_elapsed_s,
            )

        return RenderFrameState(
            x=round(self.body.x),
            y=round(self.body.y),
            state=self.state,
            animation_name=self.state.animation_name,
            scale_x=scale_x,
            scale_y=scale_y,
            flip_x=flip_x,
            vx=self.body.vx,
            vy=self.body.vy,
            locked_hwnd=win.hwnd if (win is not None and win.is_perchable) else None,
            window_speed=win_vel.magnitude,
            projectile=proj_state,
        )
