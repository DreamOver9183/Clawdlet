"""Finite State Machine and window-aware behavior controller (Concept Doc §3.1)."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import random

from clawdlet.agent_events import HOOK_SOURCE, AgentActivity, AgentEvent
from clawdlet.behavior_table import (
    WORK_BREAK_TRIGGER,
    BehaviorRow,
    BehaviorSelector,
    BehaviorTable,
    FidgetRow,
    IdleContext,
    load_bundled_behavior_table,
)
from clawdlet.config import (
    ACTIVITY_PROFILES,
    ActivityMode,
    ActivityProfile,
    InteractionConfig,
    PhysicsConfig,
    TrackerConfig,
)
from clawdlet.geometry import Rect, Vec2
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import ImpactEvent, PhysicsBody, step_physics
from clawdlet.states import PetState
from clawdlet.window_push import (
    PUSH_MAX_DISTANCE_PX,
    PUSH_MAX_DURATION_S,
    PUSH_SPEED_PX_S,
    PushPlan,
    plan_window_push,
)
from clawdlet.work_session import PHASE_JUGGLE, PHASE_TYPING, WorkDirective, WorkSession, WorkSignal

# Offsets of the side random streams from ``rng_seed`` (keeps them apart from ``_rng``)
_WORK_RNG_OFFSET = 7919
_BEHAVIOR_RNG_OFFSET = 104729
_FIDGET_RNG_OFFSET = 15485863

# Spot detection for the behavior table's ``when.spot``
_SCREEN_EDGE_PX = 4.0
_WINDOW_SIDE_PX = 8.0
_PERCH_EDGE_FRACTION = 0.25  # Of the body width, from either end of the title bar

# Strips drawn lower than the body box (sprite px of 64): legs / head hang over the title bar's
# edge while the physics body stays on top of it
_RENDER_DY_SPRITE_PX = {"perch_dangle": 8, "perch_peer": 10}

# Gaze strip poses used outside the 3x3 look grid
_GAZE_AHEAD = 4
_GAZE_DOWN = 7
_GAZE_BLINK = 9

# Animation names the controller renders instead of ``state.animation_name`` (a test checks that
# every one of them is in the sprite manifest, so SpriteSheetBank never silently falls back)
RENDER_OVERRIDE_ANIMATIONS = (
    "typing_loop",
    "peek_out",
    "gaze",
    "alert_wave",
    "alert_tap",
    "laptop_spin",
    "laptop_wall_hit",
    "molt_shell",
)


# Table-driven idle actions (L2 idle and position-aware actions). They rest like IDLE: they may
# doze off, pounce, start work, and decide on a new activity when their timer runs out.
_IDLE_ACTION_STATES = frozenset({
    PetState.READ_SCROLL,
    PetState.POLISH_CLAW,
    PetState.DIG_SAND,
    PetState.PERCH_DANGLE,
    PetState.PERCH_PEER,
    PetState.WALL_SCRATCH,
})

# Resting states that may doze off, pounce, or decide on a new activity
_REST_STATES = frozenset({PetState.IDLE, PetState.BLOB, PetState.PERCH_CLAUDE, *_IDLE_ACTION_STATES})

# Resting states that idly glance around and blink (L1 fidgets, cosmetic only)
_FIDGET_STATES = frozenset({PetState.IDLE, PetState.PERCH_CLAUDE})

# Timer-driven reactions that stand still on the floor or on Claude Desktop's top border
_STATIONARY_STATES = frozenset({
    PetState.POUNCE,
    PetState.POKED,
    PetState.PETTED,
    PetState.FLAIL,
    PetState.YAWN,
    PetState.SLEEP,
    PetState.WAKE_STARTLE,
    PetState.PEEK_EDGE,
    PetState.CELEBRATE,
    PetState.ERROR_SWEAT,
    PetState.ALERT,
    PetState.MOLT,
    PetState.CONFUSED,
    PetState.SAD,
})

# States a work session (Claude thinking / agent busy / manual typing) may interrupt to pull out the
# laptop. User reactions, ALERT, CELEBRATE, ERROR_SWEAT and PEEK_EDGE play out instead; a laptop
# being put away finishes first and then comes straight back out.
_WORK_ENTRY_STATES = frozenset({
    PetState.IDLE,
    PetState.BLOB,
    PetState.WANDER,
    PetState.PERCH_CLAUDE,
    PetState.CHASE_CURSOR,
    PetState.YAWN,
    PetState.SLEEP,
    PetState.WAKE_STARTLE,
    PetState.PUSH_WINDOW,
    *_IDLE_ACTION_STATES,
})

# Short breaks between typing segments (laptop stays out). Add new break states here.
_WORK_BREAK_STATES = frozenset({
    PetState.WORK_GLANCE,
    PetState.WORK_STRETCH,
    PetState.WORK_THINK,
    PetState.WORK_SIP,
})
# Typing, juggling subagents (alternates with typing instead of breaks) and the breaks
_WORK_STATES = frozenset({PetState.TYPING, PetState.JUGGLE, *_WORK_BREAK_STATES})

# States in which clicks, petting, and agent events are ignored (held, airborne, or mid-climb)
_UNREACTIVE_STATES = frozenset({
    PetState.DRAGGED,
    PetState.FALLING,
    PetState.THROWN,
    PetState.LAND_SQUISH,
    PetState.DIZZY,
    PetState.CLIMB,
    PetState.POUNCE,
})

# Laptop out (never flipped; tantrums and petting are ignored)
_LAPTOP_STATES = frozenset({*_WORK_STATES, PetState.PUT_AWAY_LAPTOP, PetState.KICK_LAPTOP})

# States from which a finished turn celebrates right away
_BUSY_INTERRUPTIBLE = _WORK_ENTRY_STATES | _LAPTOP_STATES

# States ticked by the "perched on Claude Desktop's top border" path while a window is locked
_PERCH_TICK_STATES = frozenset({
    PetState.PERCH_CLAUDE,
    PetState.WANDER,
    PetState.BLOB,
    PetState.CHASE_CURSOR,
    *_IDLE_ACTION_STATES,
    *_LAPTOP_STATES,
    *_STATIONARY_STATES,
})


@dataclass(frozen=True)
class WindowSnapshot:
    """Logical-coordinate state of the tracked Claude Desktop window."""

    hwnd: int
    rect: Rect
    minimized: bool = False
    visible: bool = True
    is_thinking: bool = False
    maximized: bool = False
    foreground: bool = False  # Claude Desktop is the active (foreground) window

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
    flip_x: bool = False


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
    frame_index: int | None = None  # Controller-chosen pose (e.g. gaze direction); None = time-based
    shell: ProjectileRenderState | None = None  # Shed molt shell lying where Clawd molted


class ClawdletController:
    """Coordinates the FSM, autonomous wandering/climbing, mouse drag/throw, and Claude window perching."""

    SQUISH_DURATION_S: float = 0.40       # land_squish: 4 frames @ 10 FPS
    DIZZY_DURATION_S: float = 1.80
    PUT_AWAY_DURATION_S: float = 1.10
    KICK_LAPTOP_DURATION_S: float = 1.28
    KICK_RELEASE_S: float = 2.0 / 5.5     # kick_laptop frame 2 @ 5.5 FPS: laptop leaves the foot
    POINTER_HISTORY_WINDOW_S: float = 0.10
    # Milestone 5 reaction timings
    POUNCE_CROUCH_S: float = 0.35        # pounce: 3 frames @ 8.6 FPS
    POUNCE_MAX_JUMP_VY: float = 1100.0   # ~252px apex at gravity 2400
    POUNCE_MAX_VX: float = 900.0
    POKED_DURATION_S: float = 0.60       # poked: 4 frames @ 6.6 FPS
    PET_LINGER_S: float = 1.00           # PETTED keeps glowing this long after the last stroke
    FLAIL_DURATION_S: float = 1.60
    YAWN_DURATION_S: float = 1.20        # yawn: 6 frames @ 5 FPS
    WAKE_DURATION_S: float = 0.60        # wake_startle: 4 frames @ 6.6 FPS
    CELEBRATE_DURATION_S: float = 1.60   # celebrate: 8 frames @ 5 FPS
    ERROR_SWEAT_DURATION_S: float = 1.40 # error_sweat: 6 frames @ 4.3 FPS
    WORK_GLANCE_POKE_S: float = 0.75     # Poked while typing: look up briefly, then keep typing
    # Milestone 6 Claude Code reactions
    CONFUSED_DURATION_S: float = 1.50    # confused: 6 frames @ 4 FPS
    SAD_DURATION_S: float = 2.00         # sad: 6 frames @ 3 FPS
    ERROR_STREAK_WINDOW_S: float = 90.0  # Tool failures this close together escalate
    MOLT_DURATION_S: float = 1.60        # molt: 8 frames @ 5 FPS
    MOLT_SHED_S: float = 0.80            # molt frame 4: the empty shell stands at the old spot
    MOLT_SHIFT_SPRITE_PX: float = 8.0    # Clawd steps out this far (sprite px, toward facing)
    SHELL_LINGER_S: float = 4.0          # The shed shell stays this long, then is gone
    SHELL_STRIP_S: float = 3.0           # molt_shell: 6 frames @ 2 FPS (fades on frames 4-5)
    ALERT_WAVE_S: float = 2.0            # Unanswered '!': wave this long...
    ALERT_HOLD_S: float = 4.0            # ...then plain '!' this long, repeating
    GAZE_BLINK_S: float = 0.12
    CHASE_SPEED_MULT: float = 1.40       # Chasing the cursor is an excited trot

    def __init__(
        self,
        body: PhysicsBody,
        work_area: Rect,
        physics_cfg: PhysicsConfig | None = None,
        tracker_cfg: TrackerConfig | None = None,
        activity_mode: ActivityMode = ActivityMode.NORMAL,
        rng_seed: int | None = None,
        interaction_cfg: InteractionConfig | None = None,
        behavior_table: BehaviorTable | None = None,
    ) -> None:
        self.body = body
        self.work_area = work_area
        self.physics_cfg = physics_cfg or PhysicsConfig()
        self.tracker_cfg = tracker_cfg or TrackerConfig()
        self.interaction_cfg = interaction_cfg or InteractionConfig()
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
        # Edge-peek mode (tray toggle): hide half-visible at the nearest screen edge
        self.peek_mode: bool = False
        self._peek_side: int = -1        # -1 left edge, +1 right edge
        self._peek_seek_side: int = 0    # Side currently being walked to (0 = not seeking)
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

        # Shed molt shell prop (follows Claude Desktop's title bar when shed while perched)
        self._molt_shed: bool = False
        self._shell_active: bool = False
        self._shell_t: float = 0.0
        self._shell_x: float = 0.0
        self._shell_y: float = 0.0
        self._shell_h: float = 0.0
        self._shell_flip: bool = False
        self._shell_hwnd: int | None = None
        self._shell_off_x: float = 0.0

        # Global cursor & user-idle state (Milestone 5)
        self._pointer: PointerSnapshot | None = None
        self._cursor_still_s: float = 0.0
        self._cursor_anchor: Vec2 | None = None
        # Cosmetic randomness (blinks) uses its own stream so it never perturbs FSM decisions
        self._fx_rng = random.Random(rng_seed)
        self._gaze_blink_until: float = 0.0
        self._gaze_next_blink_at: float = 0.0
        self._pounce_ready_at: float = 0.0

        # Claude Code hook activity (Milestone 5)
        self.agent = AgentActivity(self.interaction_cfg.agent_busy_expiry_s)
        # Idle choices come from the behavior table (bundled only: tests never read %APPDATA%).
        # Its opportunistic rolls use their own stream; weighted picks reuse the FSM's rest roll.
        self.behavior_table: BehaviorTable = behavior_table or load_bundled_behavior_table()
        self._behavior_rng = random.Random(None if rng_seed is None else rng_seed + _BEHAVIOR_RNG_OFFSET)
        self._selector = BehaviorSelector(self.behavior_table, self._behavior_rng)
        self._active_row: BehaviorRow | None = None   # Row that started the current state (chains)
        self._active_row_state: PetState | None = None
        # After a wall_bump / window_side row: turn away from the wall, or climb Claude's window
        self._turn_after_scratch: bool = False
        self._climb_after_scratch: bool = False
        self._scratch_state: PetState | None = None
        # L1 fidgets (glances, blinks): cosmetic, so their own stream (never ``_rng``)
        self._fidget_rng = random.Random(None if rng_seed is None else rng_seed + _FIDGET_RNG_OFFSET)
        self._fidget_next_at: float = 0.0  # 0 = not scheduled yet
        self._fidget_steps: tuple[tuple[int, float], ...] = ()
        self._fidget_started_at: float = 0.0
        # Debug hook: times tick() fell back to IDLE for a state no branch handles (tests assert 0)
        self.unhandled_state_resets: int = 0

        # Work sessions (typing segments, breaks, heuristic cap) draw from their own stream
        self._work_rng = random.Random(None if rng_seed is None else rng_seed + _WORK_RNG_OFFSET)
        self._work = WorkSession(self.behavior_table.settings.work_config(), self._work_rng)
        self._work_directive: WorkDirective = WorkDirective.NONE
        self._work_sig: WorkSignal = WorkSignal(heuristic=False, agent_busy=False)
        self._typing_resumed: bool = False   # TYPING renders typing_loop (no laptop intro)
        self._glance_return_s: float = 0.0   # Manual typing time left when a poke glance started
        self._alert_session: str | None = None
        # Tool-failure streaks per session: (count, time of the latest failure)
        self._error_streak: dict[str, tuple[int, float]] = {}
        self._pending_celebrate: bool = False
        self._last_win: WindowSnapshot | None = None

        # Opt-in window pushing (Hyper only). The app injects the Win32 nudger; tests inject a fake.
        self.push_window_enabled: bool = False
        self.window_nudger: Callable[[int, int], None] | None = None
        self._seeking_push: bool = False
        self._push_plan: PushPlan | None = None
        self._last_push_t: float | None = None
        self._push_moved_px: int = 0
        self._push_carry_px: float = 0.0

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

    def set_behavior_table(self, table: BehaviorTable) -> None:
        """Swap in a (re)loaded behavior table; the current state plays out unchanged."""
        self.behavior_table = table
        self._selector.set_table(table)
        self._work.cfg = table.settings.work_config()
        self._active_row = None

    def set_peek_mode(self, enabled: bool) -> None:
        """Toggle edge-peek mode; turning it off pops the pet back out to idle."""
        self.peek_mode = bool(enabled)
        if not self.peek_mode:
            self._peek_seek_side = 0
            if self.state == PetState.PEEK_EDGE:
                self._set_state(PetState.IDLE, 1.0)

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
        self._shell_active = False  # Sized for the old scale
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

    def _shed_shell(self) -> None:
        """Leave the empty shell at the pre-molt spot and step out of it (once per molt).

        Matches the molt strip: the husk is drawn at the canonical position and Clawd ends
        ``MOLT_SHIFT_SPRITE_PX`` further along; mirrored when facing left.
        """
        if self._molt_shed:
            return
        self._molt_shed = True
        win = self._last_win
        flip = self.facing < 0
        perched = (
            self._perched_hwnd is not None
            and win is not None
            and win.is_perchable
            and win.hwnd == self._perched_hwnd
        )
        self._shell_active = True
        self._shell_t = 0.0
        self._shell_x = self.body.x
        self._shell_y = self.body.y
        self._shell_h = self.body.height
        self._shell_flip = flip
        shift = self.MOLT_SHIFT_SPRITE_PX * self.body.width / 64.0 * (-1.0 if flip else 1.0)
        if perched:
            assert win is not None
            self._shell_hwnd = win.hwnd
            self._shell_off_x = self.body.x - win.rect.left
            self.body.x = win.rect.clamp_x(self.body.x + shift, self.body.width)
            self._perch_offset_x = self.body.x - win.rect.left
        else:
            self._shell_hwnd = None
            self.body.x = self.work_area.clamp_x(self.body.x + shift, self.body.width)

    def _tick_shell(self, dt: float, win: WindowSnapshot | None) -> None:
        """Age the shed shell; one shed on Claude's title bar rides along with the window."""
        if not self._shell_active:
            return
        self._shell_t += dt
        if self._shell_t >= self.SHELL_LINGER_S:
            self._shell_active = False
            return
        if self._shell_hwnd is not None:
            if win is None or not win.is_perchable or win.hwnd != self._shell_hwnd:
                self._shell_active = False  # Window gone / minimized: the shell goes with it
                return
            self._shell_x = win.rect.left + self._shell_off_x
            self._shell_y = win.rect.top - self._shell_h

    def toggle_typing(self) -> bool:
        """Toggle manual laptop-typing mode when grounded or perched (plays for ~6s then puts away)."""
        self.manual_typing = not self.manual_typing
        if self.manual_typing and self.state in (PetState.PUT_AWAY_LAPTOP, PetState.KICK_LAPTOP):
            pass  # Let the put-away / kick finish; the rest transition pulls the laptop back out
        elif self.manual_typing and self.body.grounded:
            self._enter_manual_typing()
        elif not self.manual_typing and self.state in _WORK_STATES:
            self._start_stop_typing()
        return self.manual_typing

    def poke(self) -> bool:
        """Single click: flinch (or wake up / acknowledge an alert). Return True if it reacted.

        While typing, Clawd only glances up from the laptop and then keeps working.
        """
        if not self._can_react() or self.state == PetState.MOLT:
            return False
        if self.state in _WORK_STATES:
            self._start_poke_glance()
            return True
        if self.state in _LAPTOP_STATES:
            return False
        if self.state in (PetState.PETTED, PetState.FLAIL):
            return False  # Stronger reactions keep playing
        if self.state in (PetState.YAWN, PetState.SLEEP):
            self._set_state(PetState.WAKE_STARTLE)
            return True
        self._alert_session = None
        self._set_state(PetState.POKED)
        return True

    def flail(self) -> bool:
        """Rapid clicks: throw a tantrum. Return True if it reacted."""
        if not self._can_react() or self.state in _LAPTOP_STATES or self.state == PetState.MOLT:
            return False
        self._alert_session = None
        self._set_state(PetState.FLAIL)
        return True

    def pet(self) -> bool:
        """Stroking: glow with hearts; repeated strokes keep it going. Return True if it reacted."""
        if not self._can_react() or self.state in _LAPTOP_STATES:
            return False
        if self.state in (PetState.FLAIL, PetState.ALERT, PetState.MOLT):
            return False
        if self.state == PetState.PETTED:
            self._state_timer = 0.0
            return True
        self._set_state(PetState.PETTED)
        return True

    def handle_agent_event(self, event: AgentEvent, session_id: str, source: str = HOOK_SOURCE) -> None:
        """React to a Claude Code activity event (busy typing is picked up through the work session).

        Hooks are authoritative: once a session has sent a hook event, transcript-derived events
        for it are dropped so the two sources can never double-celebrate or flip-flop. The one
        exception is INTERRUPTED, which only the transcript can see (no Stop hook fires on Esc).
        (So a transcript ``compact_boundary`` only molts sessions without hooks: PreCompact covers
        the others.)
        """
        if source == HOOK_SOURCE:
            self.agent.hook_sessions.add(session_id)
        elif session_id in self.agent.hook_sessions and event != AgentEvent.INTERRUPTED:
            return
        win = self._last_win
        was_busy = self.agent.note(event, session_id, self._clock, source)
        if event == AgentEvent.DONE:
            self._work.note_done(self._clock)  # Ignore the Claude Desktop I/O tail of the finished turn
        if event in (AgentEvent.DONE, AgentEvent.FAILED, AgentEvent.END):
            self._error_streak.pop(session_id, None)  # The turn is over: the next failure starts fresh

        # Any follow-up from the session that raised the alert means it is no longer waiting
        if event not in (AgentEvent.ATTENTION, AgentEvent.WAITING) and session_id == self._alert_session:
            self._alert_session = None
            if self.state == PetState.ALERT:
                self._return_to_rest(win)

        if event == AgentEvent.ATTENTION:
            if self._can_react():
                self._alert_session = session_id
                self._set_state(PetState.ALERT)
        elif event == AgentEvent.ERROR:
            # Escalates within a streak: puzzled, then nervous sweat, then dejected
            reaction, duration = {
                1: (PetState.CONFUSED, self.CONFUSED_DURATION_S),
                2: (PetState.ERROR_SWEAT, self.ERROR_SWEAT_DURATION_S),
            }.get(self._note_error(session_id), (PetState.SAD, self.SAD_DURATION_S))
            if self._can_react() and self.state not in (PetState.ALERT, PetState.FLAIL, PetState.MOLT):
                self._set_state(reaction, duration)
        elif event == AgentEvent.FAILED:
            if self._can_react() and self.state not in (PetState.ALERT, PetState.FLAIL, PetState.MOLT):
                self._set_state(PetState.SAD, self.SAD_DURATION_S)
        elif event == AgentEvent.COMPACT:
            if self._can_react() and self.state not in (PetState.ALERT, PetState.FLAIL, PetState.MOLT):
                self._start_molt()
        elif event == AgentEvent.DONE and was_busy:
            if self.state in _WORK_STATES and not self._still_working(win):
                # Laptop away first (tuck or kick by mode), then the victory dance
                self._start_stop_typing()
                self._pending_celebrate = True
            elif self._can_react() and self.state in _BUSY_INTERRUPTIBLE:
                self._set_state(PetState.CELEBRATE)
            else:
                self._pending_celebrate = True
        elif event == AgentEvent.INTERRUPTED and was_busy:
            # Like DONE, minus the victory dance (WAITING needs nothing: the session just stops
            # counting as busy, so typing winds down on its own)
            if self.state in _WORK_STATES and not self._still_working(win):
                self._start_stop_typing()

    def _note_error(self, session_id: str) -> int:
        """Count a tool failure into the session's streak; return its place in the streak."""
        count, last_t = self._error_streak.get(session_id, (0, float("-inf")))
        count = count + 1 if self._clock - last_t <= self.ERROR_STREAK_WINDOW_S else 1
        self._error_streak[session_id] = (count, self._clock)
        return count

    def _start_molt(self) -> None:
        self._molt_shed = False
        self._set_state(PetState.MOLT, self.MOLT_DURATION_S)

    def begin_drag(self, cursor_x: float, cursor_y: float) -> None:
        """Grab Clawdlet with the left mouse button."""
        self.manual_typing = False
        self._seeking_push = False
        self._push_plan = None
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
        self._typing_resumed = False

    # --- Milestone 6: work sessions (typing segments and breaks) --------------------------------

    def work_description(self) -> str:
        """Work-session summary for --debug, e.g. ``work=typing seg=12.3/21.0s src=agent cooldown=0s``."""
        return self._work.describe(self._clock)

    def _work_signal(self, win: WindowSnapshot | None, manual: bool | None = None) -> WorkSignal:
        """Claude Desktop heuristic thinking (tracker), busy Claude Code sessions (hooks) and manual typing."""
        busy = self.agent.busy(self._clock)
        return WorkSignal(
            heuristic=bool(win is not None and win.is_thinking),
            agent_busy=busy,
            subagents=self.agent.subagents(self._clock),
            manual=self.manual_typing if manual is None else manual,
        )

    def _still_working(self, win: WindowSnapshot | None) -> bool:
        """Is Claude still working (ignoring manual typing) after an agent event?"""
        return self._work.effective(self._work_signal(win, manual=False), self._clock)

    def _work_wanted(self) -> bool:
        """The work session is running and its signal is up this tick: the laptop should be out."""
        return self._work.active and self._work.effective(self._work_sig, self._clock)

    def _enter_work_typing(self, new_segment: bool = True) -> None:
        """Pull out the laptop (full intro) for a work-session typing segment."""
        if new_segment:
            self._work.begin_segment(self.activity_mode)
        self.body.vx = 0.0
        self._set_state(PetState.TYPING, self._work.phase_len)
        self._typing_resumed = False

    def _resume_typing(self) -> None:
        """Back from a break / glance: keep typing without replaying the laptop intro."""
        duration = self._glance_return_s if self.manual_typing else self._work.phase_len
        self.body.vx = 0.0
        self._set_state(PetState.TYPING, duration)
        self._typing_resumed = True

    def _pick_work_break_row(self) -> BehaviorRow | None:
        """Draw a ``work_breaks`` row (``_behavior_rng`` via the selector, never ``_rng``)."""
        ctx = self._idle_context(
            self._last_win, perched=self._perched_hwnd is not None, trigger=WORK_BREAK_TRIGGER
        )
        return self._selector.pick_work_break(ctx)

    def _pick_work_break(self) -> PetState:
        row = self._pick_work_break_row()
        return PetState.WORK_GLANCE if row is None else PetState[row.state]

    def _start_work_break(self) -> None:
        """Take the break the table picks; its ``duration_s`` (if any) replaces ``break_s``.

        One-shot strips shorter than the break (stretch 1.5 s, sip 2 s) hold their last frame,
        which is drawn as the resting typing pose.
        """
        row = self._pick_work_break_row()
        state = PetState.WORK_GLANCE if row is None else PetState[row.state]
        if row is not None and row.duration_s is not None:
            self._work.set_phase_len(self._work_rng.uniform(*row.duration_s))
        self.body.vx = 0.0
        self._set_state(state, self._work.phase_len)

    def _start_juggle(self) -> None:
        """Subagents are running: juggle at the open laptop (no intro, same desk layout)."""
        self.body.vx = 0.0
        self._set_state(PetState.JUGGLE, self._work.phase_len)

    def _start_poke_glance(self) -> None:
        if self.state == PetState.TYPING:
            self._glance_return_s = max(0.0, self._decision_duration - self._state_timer)
        self.body.vx = 0.0
        self._set_state(PetState.WORK_GLANCE, self.WORK_GLANCE_POKE_S)

    def _step_work(self, win: WindowSnapshot | None) -> bool:
        """Apply this tick's work-session directive to the FSM. Return True when the state changed.

        Typing only starts from a work-entry state, so reactions, alerts and landings play out
        first. Never touches the state timer of an ongoing segment.
        """
        directive = self._work_directive
        self._work_directive = WorkDirective.NONE
        state = self.state
        if state in _WORK_STATES:
            if not self._work.active and not self.manual_typing:
                # END / CAP (or the session wound down while Clawd was busy with something else)
                self._start_stop_typing()
                return True
            if directive == WorkDirective.BREAK and state == PetState.TYPING:
                self._start_work_break()
                return True
            if directive == WorkDirective.JUGGLE and state != PetState.JUGGLE:
                self._start_juggle()
                return True
            if directive == WorkDirective.RESUME and (state in _WORK_BREAK_STATES or state == PetState.JUGGLE):
                self._resume_typing()
                return True
            return False
        if state in _WORK_ENTRY_STATES and self._work_wanted():
            if self.manual_typing:
                self._enter_manual_typing()
            else:
                # START already drew this segment; re-entering after a reaction starts a fresh one
                self._enter_work_typing(new_segment=directive != WorkDirective.START)
            return True
        return False

    def _advance_typing(self, win: WindowSnapshot | None) -> None:
        """Manual typing stops after its timer; work typing is ended by the work session."""
        if not self.manual_typing or self._state_timer < self._decision_duration:
            return
        if self._still_working(win):
            # Claude is working anyway: hand the laptop over to the work session
            self.manual_typing = False
            self._work.begin_segment(self.activity_mode)
            self._decision_duration = self._work.phase_len
        else:
            self._start_stop_typing()

    def _advance_work_break(self, win: WindowSnapshot | None) -> None:
        """End a break / poke glance once its timer runs out."""
        if self._state_timer < self._decision_duration:
            return
        if self.manual_typing or self._work.phase == PHASE_TYPING:
            self._resume_typing()
        elif self._work.phase == PHASE_JUGGLE:
            self._start_juggle()  # Poked mid-juggle: back to the balls
        elif not self._work.active:
            self._start_stop_typing()
        # Otherwise the session is still on its break: keep glancing until RESUME / END

    def _advance_juggle(self, win: WindowSnapshot | None) -> None:
        """Juggling follows the work session's directives; this only catches a missed hand-off."""
        if self.manual_typing or self._work.phase == PHASE_TYPING:
            self._resume_typing()
        elif not self._work.active:
            self._start_stop_typing()

    # --- Milestone 5: shared reaction plumbing --------------------------------------------------

    @property
    def clock(self) -> float:
        """Simulation time in seconds (the clock agent events are stamped with)."""
        return self._clock

    def _set_state(self, state: PetState, duration: float | None = None) -> None:
        if self.state == PetState.MOLT and state != PetState.MOLT and self._state_timer >= self.MOLT_SHED_S:
            self._shed_shell()  # Cut short (e.g. an alert) after the shell already split off
        self.state = state
        self._state_timer = 0.0
        if duration is not None:
            self._decision_duration = duration
        if state in _STATIONARY_STATES:
            self.body.vx = 0.0

    def _return_to_rest(self, win: WindowSnapshot | None) -> None:
        """End a reaction: perch again on Claude Desktop's top border, or idle on the floor."""
        rest_mult = self.profile.rest_duration_multiplier
        if self._perched_hwnd is not None and win is not None and win.is_perchable:
            self._set_state(PetState.PERCH_CLAUDE, 2.0 * rest_mult)
        else:
            self._set_state(PetState.IDLE, 1.5 * rest_mult)

    def _can_react(self) -> bool:
        return self.body.grounded and self.state not in _UNREACTIVE_STATES

    def _user_active(self) -> bool:
        p = self._pointer
        return p is not None and p.idle_s < self.interaction_cfg.wake_idle_s

    def _maybe_start_idle_behavior(self, win: WindowSnapshot | None) -> bool:
        """Per-tick checks from a resting state: doze off, or pounce at a resting cursor.

        Return True when the state changed.
        """
        if self._should_sleep(win):
            self._set_state(PetState.YAWN)
            return True
        if self._can_pounce():
            p = self._pointer
            assert p is not None
            self.facing = 1 if p.x >= self.body.center_x else -1
            self._set_state(PetState.POUNCE)
            return True
        return False

    def _should_sleep(self, win: WindowSnapshot | None) -> bool:
        p = self._pointer
        return (
            p is not None
            and p.idle_s >= self.interaction_cfg.sleep_after_idle_s
            # A capped / muted heuristic no longer keeps Clawd awake
            and not self._work.effective(self._work_signal(win), self._clock)
        )

    def _head_to_peek_edge(self) -> None:
        """Walk to the nearest screen edge, or settle into PEEK_EDGE if already there."""
        self._seeking_claude = False
        side = -1 if self.body.center_x < self.work_area.center_x else 1
        edge_x = self.work_area.left if side < 0 else self.work_area.right - self.body.width
        if abs(self.body.x - edge_x) <= 2.0:
            self.body.x = edge_x
            self._peek_side = side
            self._peek_seek_side = 0
            self._set_state(PetState.PEEK_EDGE)
            return
        self._peek_seek_side = side
        self.facing = side
        self._set_state(PetState.WANDER, 30.0)

    def _hop_off_perch(self, win: WindowSnapshot) -> None:
        """Jump off the nearer end of Claude Desktop's top border (used when edge-peek is enabled)."""
        side = -1 if self.body.center_x < win.rect.center_x else 1
        self.facing = side
        self._perched_hwnd = None
        self.body.grounded = False
        self.body.on_platform_id = None
        self.body.vx = side * 420.0
        self.body.vy = -520.0
        self._pending_dizzy = False
        self._set_state(PetState.FALLING)

    def _plan_push(self, win: WindowSnapshot | None) -> PushPlan | None:
        p = self._pointer
        perchable = win is not None and win.is_perchable
        return plan_window_push(
            mode=self.activity_mode,
            enabled=self.push_window_enabled and self.window_nudger is not None,
            perched=self._perched_hwnd is not None,
            win_rect=win.rect if perchable else None,
            win_maximized=bool(win is not None and win.maximized),
            body_x=self.body.x,
            body_y=self.body.y,
            body_w=self.body.width,
            body_h=self.body.height,
            work_area=self.work_area,
            idle_s=None if p is None else p.idle_s,
            last_push_t=self._last_push_t,
            now=self._clock,
        )

    def _step_seek_push(self, dt: float, win: WindowSnapshot | None) -> None:
        """Walk to the side of the window; start shoving on arrival, give up if conditions lapse."""
        plan = self._plan_push(win)
        if plan is None or self._state_timer >= self._decision_duration:
            self._seeking_push = False
            self.body.vx = 0.0
            self._set_state(PetState.IDLE, 1.5)
            return
        diff = plan.stand_x - self.body.x
        if abs(diff) <= max(4.0, self.effective_walk_speed * dt * 1.5):
            self.body.x = plan.stand_x
            self.body.vx = 0.0
            self._seeking_push = False
            self._push_plan = plan
            self._last_push_t = self._clock
            self._push_moved_px = 0
            self._push_carry_px = 0.0
            self.facing = plan.direction
            self._set_state(PetState.PUSH_WINDOW)
            return
        self.facing = 1 if diff > 0 else -1
        self.body.vx = float(self.facing) * self.effective_walk_speed

    def _step_push_window(self, dt: float, win: WindowSnapshot | None) -> None:
        """Shove the Claude window a few pixels; stop at once if anything looks off."""
        plan = self._push_plan
        p = self._pointer
        self.body.vx = 0.0
        if (
            plan is None
            or win is None
            or not win.is_perchable
            or win.maximized
            or self.window_nudger is None
            or not self.push_window_enabled
            or p is None
            or p.idle_s < 1.0  # The user touched the mouse or keyboard: back off immediately
            or self._push_moved_px >= PUSH_MAX_DISTANCE_PX
            or self._state_timer >= PUSH_MAX_DURATION_S
        ):
            self._push_plan = None
            self._set_state(PetState.IDLE, 1.5 * self.profile.rest_duration_multiplier)
            return
        if plan.direction > 0:
            edge_room = self.work_area.right - win.rect.right
        else:
            edge_room = win.rect.left - self.work_area.left
        self._push_carry_px += PUSH_SPEED_PX_S * dt
        step = int(min(self._push_carry_px, PUSH_MAX_DISTANCE_PX - self._push_moved_px, edge_room))
        if step > 0:
            self._push_carry_px -= step
            self._push_moved_px += step
            self.window_nudger(win.hwnd, plan.direction * step)
        # Keep leaning on the side of the window as it slides away
        self.body.x = (win.rect.left - self.body.width) if plan.direction > 0 else win.rect.right
        self.body.x += plan.direction * max(0, step)

    def _can_pounce(self) -> bool:
        p = self._pointer
        if p is None or not self.profile.pounce_enabled or self.peek_mode:
            return False
        if self._clock < self._pounce_ready_at:
            return False
        if self._cursor_still_s < self.interaction_cfg.pounce_still_s or p.idle_s >= 30.0:
            return False
        rise = self.body.y - p.y  # cursor height above the pet's head
        return 40.0 <= rise <= 250.0 and abs(p.x - self.body.center_x) <= 300.0

    def _launch_pounce(self, win: WindowSnapshot | None) -> None:
        """Leave the crouch on a ballistic arc whose apex reaches the cursor."""
        p = self._pointer
        self._pounce_ready_at = self._clock + self.interaction_cfg.pounce_cooldown_s
        if p is None:
            self._return_to_rest(win)
            return
        g = self.physics_cfg.gravity
        rise = max(0.0, self.body.center_y - p.y)
        vy = -min(self.POUNCE_MAX_JUMP_VY, (2.0 * g * (rise + 20.0)) ** 0.5)
        t_apex = max(0.15, -vy / g)
        vx = max(-self.POUNCE_MAX_VX, min(self.POUNCE_MAX_VX, (p.x - self.body.center_x) / t_apex))
        if abs(vx) > 1.0:
            self.facing = 1 if vx > 0 else -1
        self._perched_hwnd = None
        self._climb_surface = None
        self.body.grounded = False
        self.body.on_platform_id = None
        self.body.vx = vx
        self.body.vy = vy
        self._pending_dizzy = False
        self._set_state(PetState.FALLING)

    def _try_start_chase(self, win: WindowSnapshot | None, perched: bool) -> bool:
        """At a rest decision, maybe trot toward a nearby cursor along the current surface."""
        p = self._pointer
        chance = self.profile.chase_chance
        if p is None or chance <= 0.0 or self.peek_mode:
            return False
        dx = p.x - self.body.center_x
        if abs(dx) > self.interaction_cfg.chase_radius or abs(dx) < self.body.width * 0.5:
            return False
        if perched:
            if win is None or not (win.rect.left <= p.x <= win.rect.right):
                return False
        elif p.y > self.body.bottom:
            return False  # Cursor is below the floor (e.g. on the taskbar)
        if self._rng.random() >= chance:
            return False
        self.facing = 1 if dx > 0 else -1
        self._set_state(PetState.CHASE_CURSOR, self.interaction_cfg.chase_timeout_s)
        return True

    def _step_perch_chase(self, dt: float, win: WindowSnapshot) -> None:
        p = self._pointer
        if p is None:
            self._return_to_rest(win)
            return
        max_offset = max(0.0, win.rect.width - self.body.width)
        target = max(0.0, min(max_offset, p.x - win.rect.left - self.body.width * 0.5))
        diff = target - self._perch_offset_x
        step = self.effective_walk_speed * self.CHASE_SPEED_MULT * dt
        if abs(diff) <= step:
            self._perch_offset_x = target
            self._return_to_rest(win)
            return
        self.facing = 1 if diff > 0 else -1
        self._perch_offset_x += self.facing * step
        if self._state_timer >= self._decision_duration:
            self._return_to_rest(win)

    def _step_floor_chase(self, dt: float, win: WindowSnapshot | None) -> None:
        p = self._pointer
        if p is None:
            self._return_to_rest(win)
            return
        target = self.work_area.clamp_x(p.x - self.body.width * 0.5, self.body.width)
        diff = target - self.body.x
        speed = self.effective_walk_speed * self.CHASE_SPEED_MULT
        if abs(diff) <= max(4.0, speed * dt * 1.5):
            self.body.vx = 0.0
            self._return_to_rest(win)
            return
        self.facing = 1 if diff > 0 else -1
        self.body.vx = float(self.facing) * speed

    def _advance_reaction(self, win: WindowSnapshot | None) -> None:
        """Advance timers and exits of the stationary reaction states."""
        s = self.state
        t = self._state_timer
        timed = {
            PetState.POKED: self.POKED_DURATION_S,
            PetState.PETTED: self.PET_LINGER_S,
            PetState.FLAIL: self.FLAIL_DURATION_S,
            PetState.WAKE_STARTLE: self.WAKE_DURATION_S,
            PetState.CELEBRATE: self.CELEBRATE_DURATION_S,
            PetState.ERROR_SWEAT: self.ERROR_SWEAT_DURATION_S,
            PetState.CONFUSED: self.CONFUSED_DURATION_S,
            PetState.SAD: self.SAD_DURATION_S,
        }
        if s in timed:
            if t >= timed[s]:
                self._return_to_rest(win)  # The work session pulls the laptop back out if still busy
        elif s == PetState.MOLT:
            if t >= self.MOLT_DURATION_S:
                self._shed_shell()
                self._return_to_rest(win)
        elif s == PetState.POUNCE:
            if t >= self.POUNCE_CROUCH_S:
                self._launch_pounce(win)
        elif s == PetState.YAWN:
            if self._user_active():
                self._return_to_rest(win)
            elif t >= self.YAWN_DURATION_S:
                self._set_state(PetState.SLEEP)
        elif s == PetState.SLEEP:
            if self._user_active():
                self._set_state(PetState.WAKE_STARTLE)
        elif s == PetState.PEEK_EDGE:
            if not self.peek_mode:
                self._return_to_rest(win)
        elif s == PetState.ALERT:
            if t >= self.interaction_cfg.alert_timeout_s:
                self._alert_session = None
                self._return_to_rest(win)

    def _cursor_near(self, margin: float) -> bool:
        """True when the cursor is within ``margin`` px of the pet's bounding box."""
        p = self._pointer
        if p is None:
            return False
        dx = max(self.body.x - p.x, 0.0, p.x - self.body.right)
        dy = max(self.body.y - p.y, 0.0, p.y - self.body.bottom)
        return dx * dx + dy * dy <= margin * margin

    def _gaze_frame(self, win: WindowSnapshot | None) -> int | None:
        """Return the gaze pose index toward the cursor while resting, or None to keep the base animation."""
        p = self._pointer
        if p is None or self.state not in (PetState.IDLE, PetState.PERCH_CLAUDE):
            return None
        if self.state == PetState.PERCH_CLAUDE and win is not None and p.y >= win.rect.top:
            return None  # Cursor is inside the Claude window: keep peeking down at the chat
        eye_x = self.body.center_x
        eye_y = self.body.y + self.body.height * 0.36
        dx = p.x - eye_x
        dy = p.y - eye_y
        if dx * dx + dy * dy > self.interaction_cfg.gaze_radius ** 2:
            return None
        if self._gaze_next_blink_at <= 0.0:
            self._gaze_next_blink_at = self._clock + self._fx_rng.uniform(3.0, 5.0)
        elif self._clock >= self._gaze_next_blink_at:
            self._gaze_blink_until = self._clock + self.GAZE_BLINK_S
            self._gaze_next_blink_at = self._clock + self._fx_rng.uniform(3.0, 5.0)
        if self._clock < self._gaze_blink_until:
            return _GAZE_BLINK
        return self._gaze_index(dx, dy)

    def _gaze_index(self, dx: float, dy: float) -> int:
        """Gaze strip pose for a look along (dx, dy) from the eyes: ``(qy + 1) * 3 + (qx + 1)``."""
        dead_x = self.body.width * 0.35
        dead_y = self.body.height * 0.35
        qx = 0 if abs(dx) < dead_x or abs(dy) > 2.0 * abs(dx) else (1 if dx > 0 else -1)
        qy = 0 if abs(dy) < dead_y or abs(dx) > 2.0 * abs(dy) else (1 if dy > 0 else -1)
        return (qy + 1) * 3 + (qx + 1)

    def _fidget_frame(self, win: WindowSnapshot | None) -> int | None:
        """L1 idle: now and then glance around or blink while resting (gaze strip pose, or None).

        Every ``uniform(*settings.fidget_interval_s)`` seconds a ``fidgets`` row is picked and its
        pose script played. Purely cosmetic: draws only ``_fidget_rng``, never changes the state.
        """
        if self.state not in _FIDGET_STATES:
            self._fidget_steps = ()
            self._fidget_next_at = 0.0  # Rescheduled when Clawd settles down again
            return None
        lo_s, hi_s = self.behavior_table.settings.fidget_interval_s
        if self._fidget_steps:
            t = self._clock - self._fidget_started_at
            for index, hold_s in self._fidget_steps:
                if t < hold_s:
                    return index
                t -= hold_s
            self._fidget_steps = ()
            self._fidget_next_at = self._clock + self._fidget_rng.uniform(lo_s, hi_s)
            return None
        if self._fidget_next_at <= 0.0:
            self._fidget_next_at = self._clock + self._fidget_rng.uniform(lo_s, hi_s)
            return None
        if self._clock < self._fidget_next_at:
            return None
        perched = self._perched_hwnd is not None
        row = self._selector.pick_fidget(
            self._idle_context(win, perched=perched, trigger="fidget"), self._fidget_rng
        )
        if row is None:
            self._fidget_next_at = self._clock + self._fidget_rng.uniform(lo_s, hi_s)
            return None
        self._fidget_steps = self._fidget_script(row, win)
        self._fidget_started_at = self._clock
        return self._fidget_steps[0][0]

    def _fidget_script(self, row: FidgetRow, win: WindowSnapshot | None) -> tuple[tuple[int, float], ...]:
        if row.target is None:
            return row.poses
        hold_s = self._fidget_rng.uniform(*row.hold_s)
        index = _GAZE_AHEAD
        if row.target == "claude_window" and win is not None and win.is_perchable:
            if self._perched_hwnd is not None:
                index = _GAZE_DOWN  # Sitting on it: look down at the chat
            else:
                eye_y = self.body.y + self.body.height * 0.36
                index = self._gaze_index(win.rect.center_x - self.body.center_x, win.rect.center_y - eye_y)
        return ((index, hold_s),)

    # --- Milestone 6: data-driven idle choices (behavior table) ---------------------------------

    def _idle_context(self, win: WindowSnapshot | None, perched: bool, trigger: str = "rest") -> IdleContext:
        """Describe the current situation for the behavior table's ``when`` conditions."""
        body = self.body
        p = self._pointer
        perchable = win is not None and win.is_perchable
        spots: set[str] = set()
        if perched and perchable:
            assert win is not None
            max_offset = max(0.0, win.rect.width - body.width)
            edge = body.width * _PERCH_EDGE_FRACTION
            if self._perch_offset_x <= edge or self._perch_offset_x >= max_offset - edge:
                spots.add("perch_edge")
            else:
                spots.add("perch_middle")
        elif not perched:
            area = self.work_area
            if body.x <= area.left + _SCREEN_EDGE_PX or body.right >= area.right - _SCREEN_EDGE_PX:
                spots.add("screen_edge")
            if perchable:
                assert win is not None
                overlap = max(0.0, min(body.bottom, win.rect.bottom) - max(body.y, win.rect.top))
                near_side = min(
                    abs(body.right - win.rect.left),
                    abs(body.x - win.rect.left),
                    abs(body.x - win.rect.right),
                    abs(body.right - win.rect.right),
                )
                if overlap >= body.height * 0.5 and near_side <= _WINDOW_SIDE_PX:
                    spots.add("window_side")
            if not spots:
                spots.add("open")
        if p is None:
            idle_level = 1
        elif p.idle_s >= self.interaction_cfg.sleep_after_idle_s:
            idle_level = 3
        elif p.idle_s >= self.behavior_table.settings.idle_level2_after_s:
            idle_level = 2
        else:
            idle_level = 1
        cursor_in_window = bool(
            p is not None
            and perchable
            and win is not None
            and win.rect.left <= p.x <= win.rect.right
            and win.rect.top <= p.y <= win.rect.bottom
        )
        return IdleContext(
            surface="perch" if perched else "floor",
            spots=frozenset(spots),
            mode=self.activity_mode.value,
            idle_level=idle_level,
            user_idle_s=None if p is None else p.idle_s,
            claude_visible=perchable,
            claude_foreground=bool(win is not None and win.foreground),
            cursor_in_window=cursor_in_window,
            prev_state=self.state.name,
            trigger=trigger,
            now=self._clock,
        )

    def _take_active_row(self) -> BehaviorRow | None:
        """The row that started the state being left (for ``next`` chains); consumed by the call."""
        row = self._active_row if self._active_row_state == self.state else None
        self._active_row = None
        return row

    def _start_idle_row(
        self,
        win: WindowSnapshot | None,
        perched: bool,
        roll: float,
        lo: float,
        chain_from: BehaviorRow | None,
    ) -> None:
        """Hand the rest roll's remainder ``[lo, 1)`` to the behavior table as ``u`` in ``[0, 1)``."""
        u = (roll - lo) / (1.0 - lo) if lo < 1.0 else 0.0
        row = self._selector.pick("rest", self._idle_context(win, perched), u, chain_from=chain_from)
        if row is not None:
            self._start_row(row)
            return
        # Nothing eligible (e.g. every row disabled): plain rest
        lo_s, hi_s = (3.0, 5.5) if perched else (2.2, 4.0)
        self.state = PetState.PERCH_CLAUDE if perched else PetState.IDLE
        self._state_timer = 0.0
        self._decision_duration = self._rng.uniform(lo_s, hi_s) * self.profile.rest_duration_multiplier

    def _start_row(self, row: BehaviorRow, rng: random.Random | None = None) -> None:
        """Enter a table row's state; exactly one ``uniform`` call on ``rng`` (default ``_rng``,
        like the rest-decision code it replaced; trigger hooks pass ``_behavior_rng``)."""
        lo_s, hi_s = row.duration_s or (2.2, 4.0)  # The loader requires duration_s on behavior rows
        duration = (rng or self._rng).uniform(lo_s, hi_s)
        if row.scale_by_rest:
            duration *= self.profile.rest_duration_multiplier
        self.state = PetState[row.state]
        self._state_timer = 0.0
        self._decision_duration = duration
        self._active_row = row
        self._active_row_state = self.state
        if self.state in _IDLE_ACTION_STATES:
            self.body.vx = 0.0
        if self.state == PetState.PERCH_DANGLE:
            self._snap_to_perch_end()

    def _snap_to_perch_end(self) -> None:
        """PERCH_DANGLE: shuffle to the nearer end of the title bar and face out over it."""
        win = self._last_win
        if self._perched_hwnd is None or win is None or not win.is_perchable:
            return
        max_offset = max(0.0, win.rect.width - self.body.width)
        at_left = self._perch_offset_x <= max_offset * 0.5
        self._perch_offset_x = 0.0 if at_left else max_offset
        self.body.x = win.rect.left + self._perch_offset_x
        self.facing = -1 if at_left else 1

    def _start_trigger_row(self, trigger: str, win: WindowSnapshot | None, wall_side: int) -> bool:
        """``on: wall_bump`` / ``on: window_side`` rows: maybe stop and face the wall for a while.

        Draws only ``_behavior_rng`` (chance roll and duration), so the FSM's ``_rng`` sequence
        is the same whether or not a row fires. Return True when a row was started.
        """
        row = self._selector.pick(trigger, self._idle_context(win, perched=False, trigger=trigger))
        if row is None:
            return False
        self._start_row(row, rng=self._behavior_rng)
        self.facing = wall_side
        self.body.vx = 0.0
        self._seeking_claude = False
        self._scratch_state = self.state
        self._turn_after_scratch = trigger == "wall_bump"
        self._climb_after_scratch = trigger == "window_side"
        return True

    def _climb_claude_or_scratch(self, win: WindowSnapshot, wall_side: int) -> None:
        """At the side of Claude's window: maybe drum on it first (``window_side``), else climb."""
        if not self._start_trigger_row("window_side", win, wall_side):
            self._start_climb_claude(win, wall_side)

    def _finish_floor_rest(self, win: WindowSnapshot | None) -> None:
        """A floor rest / idle action ran out: follow up a wall scratch, else decide anew."""
        if self._climb_after_scratch:
            self._climb_after_scratch = False
            if self._is_claude_climbable(win):
                assert win is not None
                target_x, wall_side = self._choose_claude_climb_x(win.rect)
                if abs(self.body.x - target_x) <= 6.0:
                    self._active_row = None
                    self.body.x = target_x
                    self._start_climb_claude(win, wall_side)
                    return
        elif self._turn_after_scratch:
            self._turn_after_scratch = False
            self.facing = -self.facing
        self._transition_from_floor_rest(win)

    def _transition_from_floor_rest(self, win: WindowSnapshot | None) -> None:
        """Choose next autonomous state when IDLE, BLOB, or TYPING timer expires on the floor."""
        chain_from = self._take_active_row()
        if self.manual_typing:
            # Double-clicked again while putting the laptop away: pull it straight back out
            self._enter_manual_typing()
            return
        if self._pending_celebrate:
            self._pending_celebrate = False
            self._set_state(PetState.CELEBRATE)
            return
        if self._work_wanted():
            # Claude got busy again while the laptop was being put away: straight back to work
            self._enter_work_typing()
            return
        if self.peek_mode:
            self._head_to_peek_edge()
            return
        if self._plan_push(win) is not None:
            self._seeking_push = True
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = 10.0
            return
        if self._try_start_chase(win, perched=False):
            return
        r = self._rng.random()
        if self._is_claude_climbable(win) and r < self.effective_seek_weight:
            assert win is not None
            target_x, wall_side = self._choose_claude_climb_x(win.rect)
            if abs(self.body.x - target_x) <= 6.0:
                self.body.x = target_x
                self._climb_claude_or_scratch(win, wall_side)
                return
            self._seeking_claude = True
            self.facing = 1 if target_x > self.body.x else -1
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = 10.0
            return

        self._seeking_claude = False
        roll = self._rng.random()
        wander_p = self.profile.wander_probability
        if roll < wander_p:
            self.facing = self._rng.choice((-1, 1))
            if self.body.x <= self.work_area.left + 4.0:
                self.facing = 1
            elif self.body.x >= self.work_area.right - self.body.width - 4.0:
                self.facing = -1
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.0, 4.2)
        else:
            # Idle tail: the behavior table decides (the default table reproduces BLOB below 0.85
            # unless already BLOB, else IDLE)
            self._start_idle_row(win, perched=False, roll=roll, lo=wander_p, chain_from=chain_from)

    def _transition_from_perch(self, win: WindowSnapshot) -> None:
        """Choose next autonomous state while perched on Claude Desktop's top border."""
        chain_from = self._take_active_row()
        if self.manual_typing:
            self._enter_manual_typing()
            return
        if self._pending_celebrate:
            self._pending_celebrate = False
            self._set_state(PetState.CELEBRATE)
            return
        if self._work_wanted():
            self._enter_work_typing()
            return
        if self.peek_mode:
            self._hop_off_perch(win)
            return
        if self._try_start_chase(win, perched=True):
            return
        roll = self._rng.random()
        can_stroll = win.rect.width >= self.body.width * 1.4
        wander_cut = min(0.75, self.profile.wander_probability * 0.85)
        if can_stroll and roll < wander_cut and self.state != PetState.WANDER:
            self.facing = self._rng.choice((-1, 1))
            self.state = PetState.WANDER
            self._state_timer = 0.0
            self._decision_duration = self._rng.uniform(2.0, 3.8)
        elif not can_stroll:
            # Engine branch: a title bar under 1.4 body widths (unreachable with the tracker's 300 px
            # minimum window width) keeps the original fixed choice
            rest_mult = self.profile.rest_duration_multiplier
            if roll < 0.72 and self.state != PetState.BLOB:
                self.state = PetState.BLOB
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(2.4, 4.2) * rest_mult
            else:
                self.state = PetState.PERCH_CLAUDE
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult
        else:
            # Idle tail: the behavior table decides. Right after a stroll no stroll was on offer,
            # so the whole roll goes to the table (its *_after_stroll rows)
            lo = 0.0 if self.state == PetState.WANDER else wander_cut
            self._start_idle_row(win, perched=True, roll=roll, lo=lo, chain_from=chain_from)

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
        """Handle states attached to Claude Desktop's top border (rest, stroll, typing, and reactions)."""
        if win is None or not win.is_perchable:
            self._detach_to_fall()
            return None
        if win_vel.magnitude > self.tracker_cfg.shake_speed_threshold:
            self._detach_by_shake(win_vel)
            return None

        if self._step_work(win):
            pass
        elif self.manual_typing and self.state in _WORK_ENTRY_STATES:
            self._enter_manual_typing()
        elif self.state == PetState.CHASE_CURSOR:
            self._step_perch_chase(dt, win)
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

        if self.state in _STATIONARY_STATES:
            self._advance_reaction(win)
        elif self.state in _REST_STATES and self._maybe_start_idle_behavior(win):
            pass
        elif self.state == PetState.TYPING:
            self._advance_typing(win)
        elif self.state in _WORK_BREAK_STATES:
            self._advance_work_break(win)
        elif self.state == PetState.JUGGLE:
            self._advance_juggle(win)
        elif self._state_timer >= self._decision_duration:
            self._transition_from_perch(win)
        if self._perched_hwnd is None:
            return None  # Launched off the border (pounce / hop): physics takes over this tick

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

    def _update_pointer(self, dt: float, pointer: PointerSnapshot | None) -> None:
        # Stillness is measured against an anchor so slow drift still counts as movement
        anchor = self._cursor_anchor
        if pointer is None:
            self._cursor_anchor = None
            self._cursor_still_s = 0.0
        elif anchor is None or abs(pointer.x - anchor.x) + abs(pointer.y - anchor.y) > 3.0:
            self._cursor_anchor = Vec2(pointer.x, pointer.y)
            self._cursor_still_s = 0.0
        else:
            self._cursor_still_s += dt
        self._pointer = pointer

    def tick(
        self,
        dt: float,
        win: WindowSnapshot | None = None,
        pointer: PointerSnapshot | None = None,
    ) -> RenderFrameState:
        """Advance FSM and physics by ``dt`` seconds."""
        render = self._tick(dt, win, pointer)
        if self._active_row is not None and self.state != self._active_row_state:
            self._active_row = None  # Left the table-chosen state: no chain from it
        if self.state != self._scratch_state:
            # Interrupted (or done): a later rest must not turn around / climb on its behalf
            self._turn_after_scratch = False
            self._climb_after_scratch = False
            self._scratch_state = None
        return render

    def _tick(
        self,
        dt: float,
        win: WindowSnapshot | None,
        pointer: PointerSnapshot | None,
    ) -> RenderFrameState:
        self._clock += dt
        self._state_timer += dt
        self._update_pointer(dt, pointer)
        self._last_win = win
        self._tick_projectile(dt)
        self._tick_shell(dt, win)
        # Work session first: the perch / floor paths below apply its directive via _step_work
        self._work_sig = self._work_signal(win)
        self._work_directive = self._work.update(dt, self._work_sig, self._clock, self.activity_mode)

        win_vel = self._update_window_velocity(dt, win)
        perchable_rect = win.rect if (win is not None and win.is_perchable) else None

        # 1. Handle DRAGGED state (physics suspended while held)
        if self.state == PetState.DRAGGED:
            return self._build_render_state(win, win_vel)

        # 2. Handle CLIMB state (gravity suspended while gripping edge)
        if self.state == PetState.CLIMB:
            return self._tick_climb(dt, win, win_vel)

        # 3. Handle states perched on Claude Desktop's top border
        if self._perched_hwnd is not None and self.state in _PERCH_TICK_STATES:
            perched_render = self._tick_perched_on_claude(dt, win, win_vel)
            if perched_render is not None:
                return perched_render

        # 3a. Landing squish / dizzy on Claude Desktop's top border: follow the window until recovered
        if self._perched_hwnd is not None and self.state in (PetState.LAND_SQUISH, PetState.DIZZY):
            recovering_render = self._tick_recovering_on_claude(win, win_vel)
            if recovering_render is not None:
                return recovering_render

        # 3b. Grounded on the floor: follow the work session (start / break / resume / end typing),
        # or manual typing that was just toggled. Landing squish and dizzy always play out first.
        on_floor = self.body.grounded and self._perched_hwnd is None
        if on_floor:
            if self._step_work(win):
                if self.state == PetState.TYPING:
                    self.body.vy = 0.0
                    return self._build_render_state(win, win_vel)
            elif self.manual_typing and self.state in _WORK_ENTRY_STATES:
                self.body.vy = 0.0
                self._enter_manual_typing()
                return self._build_render_state(win, win_vel)

        # 3c. Resting on the floor: maybe pounce at a resting cursor
        if on_floor and self.state in _REST_STATES:
            self._maybe_start_idle_behavior(win)

        # 4. Handle floor WANDER / CHASE horizontal velocity before physics integration
        walk_spd = self.effective_walk_speed
        if on_floor and (self.state in _STATIONARY_STATES or self.state in _IDLE_ACTION_STATES):
            self.body.vx = 0.0
        if on_floor and self.state == PetState.CHASE_CURSOR:
            self._step_floor_chase(dt, win)
        if on_floor and self.state == PetState.WANDER and self._seeking_push:
            self._step_seek_push(dt, win)
        if on_floor and self.state == PetState.PUSH_WINDOW:
            self._step_push_window(dt, win)
        if (
            self.state == PetState.WANDER
            and self.body.grounded
            and self._perched_hwnd is None
            and not self._seeking_push
        ):
            if self._seeking_claude and self._is_claude_climbable(win):
                assert win is not None
                target_x, wall_side = self._choose_claude_climb_x(win.rect)
                dist = target_x - self.body.x
                step_dist = walk_spd * dt
                if abs(dist) <= max(6.0, step_dist * 1.5):
                    self.body.x = target_x
                    self._climb_claude_or_scratch(win, wall_side)
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
            elif self.state in _STATIONARY_STATES:
                self._advance_reaction(win)
            elif self.body.on_platform_id == "claude_window" and win is not None and win.is_perchable:
                self.state = PetState.PERCH_CLAUDE
                self._perched_hwnd = win.hwnd
                self._perch_offset_x = self.body.x - win.rect.left
                self._state_timer = 0.0
                self._decision_duration = self._rng.uniform(3.0, 5.5) * rest_mult
            elif self.state == PetState.CHASE_CURSOR:
                if self._state_timer >= self._decision_duration:
                    self._return_to_rest(win)
            elif self.state == PetState.PUSH_WINDOW or (
                self.state == PetState.WANDER and self._seeking_push
            ):
                pass  # Advanced before the physics step
            elif self.state == PetState.WANDER:
                at_left_wall = self.body.x <= self.work_area.left + 1.0 and self.facing < 0
                at_right_wall = (
                    self.body.x >= self.work_area.right - self.body.width - 1.0 and self.facing > 0
                )
                if at_left_wall or at_right_wall:
                    wall_side = -1 if at_left_wall else 1
                    if self.peek_mode and self._peek_seek_side == wall_side:
                        self._head_to_peek_edge()
                    elif not self.peek_mode and self._rng.random() < self.effective_wall_climb_chance:
                        self._start_climb_screen_wall(wall_side)
                    elif not self.peek_mode and self._start_trigger_row("wall_bump", win, wall_side):
                        pass  # Scratch at the wall (facing it), then turn around
                    else:
                        self.facing = -wall_side
                elif self._state_timer >= self._decision_duration:
                    self.body.vx = 0.0
                    self.state = PetState.IDLE
                    self._state_timer = 0.0
                    self._decision_duration = self._rng.uniform(2.0, 4.0) * rest_mult
            elif self.state == PetState.TYPING:
                self._advance_typing(win)
            elif self.state in _WORK_BREAK_STATES:
                self._advance_work_break(win)
            elif self.state == PetState.JUGGLE:
                self._advance_juggle(win)
            elif self.state in (
                PetState.IDLE,
                PetState.BLOB,
                PetState.PUT_AWAY_LAPTOP,
                PetState.KICK_LAPTOP,
            ) or self.state in _IDLE_ACTION_STATES:
                if self._state_timer >= self._decision_duration:
                    self._finish_floor_rest(win)
            else:
                self.unhandled_state_resets += 1
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
        elif self.state in _LAPTOP_STATES:
            flip_x = False
        else:
            flip_x = self.facing < 0
        if self.state == PetState.PEEK_EDGE:
            # Peek sprites hide at the left frame edge; mirror them for the right screen edge
            flip_x = self._peek_side > 0

        proj_state: ProjectileRenderState | None = None
        if self._proj_active:
            proj_anim = "laptop_spin" if self._proj_phase == "flying" else "laptop_wall_hit"
            proj_state = ProjectileRenderState(
                x=round(self._proj_x),
                y=round(self._proj_y),
                animation_name=proj_anim,
                elapsed_s=self._proj_elapsed_s,
            )

        shell_state: ProjectileRenderState | None = None
        if self._shell_active:
            shell_state = ProjectileRenderState(
                x=round(self._shell_x),
                y=round(self._shell_y),
                animation_name="molt_shell",
                # Sits whole for the first second, then the strip plays out (fading on frames 4-5)
                elapsed_s=max(0.0, self._shell_t - (self.SHELL_LINGER_S - self.SHELL_STRIP_S)),
                flip_x=self._shell_flip,
            )

        animation_name = self.state.animation_name
        if self.state == PetState.ALERT:
            animation_name = self._alert_animation()
        if self.state == PetState.TYPING and self._typing_resumed:
            animation_name = "typing_loop"  # Laptop already out: skip the intro frames
        if self.state == PetState.PEEK_EDGE and self._cursor_near(120.0):
            animation_name = "peek_out"
        frame_index = self._gaze_frame(win)
        if frame_index is None:
            frame_index = self._fidget_frame(win)
        if frame_index is not None:
            # Gaze poses are drawn facing the viewer; flipping would mirror the look direction
            animation_name = "gaze"
            flip_x = False
        dy_px = _RENDER_DY_SPRITE_PX.get(animation_name, 0)

        return RenderFrameState(
            x=round(self.body.x),
            y=round(self.body.y + dy_px * self.body.height / 64.0),
            state=self.state,
            animation_name=animation_name,
            frame_index=frame_index,
            scale_x=scale_x,
            scale_y=scale_y,
            flip_x=flip_x,
            vx=self.body.vx,
            vy=self.body.vy,
            locked_hwnd=win.hwnd if (win is not None and win.is_perchable) else None,
            window_speed=win_vel.magnitude,
            projectile=proj_state,
            shell=shell_state,
        )

    def _alert_animation(self) -> str:
        """Unanswered '!' escalates (MS Agent GetAttention -> Continued -> Return): after
        ``alert_escalate_s`` it waves for ``ALERT_WAVE_S`` (knocks on the title bar when perched),
        then shows the plain '!' for ``ALERT_HOLD_S``, repeating. Render-only: the state stays
        ALERT and the body stays put (alert_wave's hop frames are drawn inside the sprite).
        """
        t = self._state_timer - self.behavior_table.settings.alert_escalate_s
        if t < 0.0 or t % (self.ALERT_WAVE_S + self.ALERT_HOLD_S) >= self.ALERT_WAVE_S:
            return PetState.ALERT.animation_name
        return "alert_tap" if self._perched_hwnd is not None else "alert_wave"
