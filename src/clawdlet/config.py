"""Configuration dataclasses for physics simulation, FSM autonomy, and window tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActivityMode(str, Enum):
    """Milestone 3 activity level presets controlled via the System Tray menu."""

    QUIET = "quiet"
    NORMAL = "normal"
    HYPER = "hyper"


@dataclass(frozen=True)
class ActivityProfile:
    """Multipliers and probabilities associated with an ActivityMode."""

    speed_multiplier: float
    rest_duration_multiplier: float
    wander_probability: float
    claude_seek_weight: float
    wall_climb_chance: float
    chase_chance: float = 0.0          # Probability of chasing a nearby cursor at each rest decision
    pounce_enabled: bool = False       # Hyper only: pounce at a cursor resting above the pet


ACTIVITY_PROFILES: dict[ActivityMode, ActivityProfile] = {
    ActivityMode.QUIET: ActivityProfile(
        speed_multiplier=0.60,
        rest_duration_multiplier=2.50,
        wander_probability=0.22,
        claude_seek_weight=0.50,
        wall_climb_chance=0.0,
        chase_chance=0.15,
    ),
    ActivityMode.NORMAL: ActivityProfile(
        speed_multiplier=1.00,
        rest_duration_multiplier=1.00,
        wander_probability=0.62,
        claude_seek_weight=0.72,
        wall_climb_chance=0.55,
        chase_chance=0.35,
    ),
    ActivityMode.HYPER: ActivityProfile(
        speed_multiplier=1.65,
        rest_duration_multiplier=0.42,
        wander_probability=0.88,
        claude_seek_weight=0.88,
        wall_climb_chance=0.85,
        chase_chance=0.60,
        pounce_enabled=True,
    ),
}


@dataclass(frozen=True)
class PhysicsConfig:
    """Parameters governing 2D kinematics, damping, collision, and autonomous locomotion."""

    gravity: float = 2400.0            # Downward acceleration g (logical px/s^2)
    air_drag: float = 0.992            # Per-step horizontal air drag multiplier (mu_drag)
    ground_friction: float = 0.80      # Per-step horizontal friction when grounded
    restitution: float = 0.35          # Bounce coefficient on walls and high-speed floor hits
    bounce_speed_threshold: float = 260.0  # Minimum vertical impact speed to trigger bounce
    max_throw_speed: float = 2800.0    # Maximum initial velocity magnitude on release (px/s)
    platform_overlap_ratio: float = 0.45  # Minimum horizontal overlap fraction to land on window top
    walk_speed: float = 95.0           # Base horizontal walking speed during WANDER (px/s)
    climb_speed: float = 115.0         # Base vertical climbing speed during CLIMB (px/s)
    claude_seek_weight: float = 0.72   # Default probability of actively seeking Claude window
    wall_climb_chance: float = 0.55    # Default probability of climbing screen edge
    dt: float = 1.0 / 60.0             # Fixed simulation step (16.67 ms)


@dataclass(frozen=True)
class TrackerConfig:
    """Parameters governing Win32 Claude Desktop window discovery and tracking."""

    target_exe_name: str = "claude.exe"
    allowed_classes: tuple[str, ...] = ("Chrome_WidgetWin_1",)
    excluded_path_keywords: tuple[str, ...] = ("claude-code",)
    excluded_titles: tuple[str, ...] = ("OleMainThreadWndName",)
    min_width: int = 300
    min_height: int = 200
    rescan_interval_s: float = 0.5     # 2 Hz full EnumWindows scan when unlocked or verifying
    shake_speed_threshold: float = 1600.0  # Window drag velocity threshold v_threshold (px/s)
    velocity_ema_alpha: float = 0.45   # Exponential smoothing factor for window velocity
    # Claude Desktop "thinking" = sustained I/O in the Claude window's own process tree: the window
    # owner (Electron browser process) plus its direct network-service utility children. Renderer,
    # GPU, node/MCP utilities and the native Claude Code CLI are never sampled.
    thinking_io_threshold: float = 8192.0  # Bytes/s for a sample to count as active
    thinking_utility_subtypes: tuple[str, ...] = ("network.mojom.NetworkService",)
    thinking_tree_refresh_s: float = 5.0   # Rebuild the sampled process set this often
    thinking_window_s: float = 3.0     # Sliding window of recent samples
    thinking_min_active: int = 3       # Active samples needed inside the window
    thinking_hold_s: float = 1.5       # Keep typing this long after the last positive window


@dataclass(frozen=True)
class InteractionConfig:
    """Milestone 5 cursor awareness, touch reactions, sleep schedule, and agent-hook timings."""

    gaze_radius: float = 600.0         # Eyes follow the cursor within this distance (logical px)
    chase_radius: float = 700.0        # Horizontal distance within which the pet may chase the cursor
    chase_timeout_s: float = 4.0       # Give up chasing after this long
    pounce_still_s: float = 1.2        # Cursor must rest this long before a Hyper pounce
    pounce_cooldown_s: float = 8.0
    sleep_after_idle_s: float = 90.0   # User idle time (GetLastInputInfo) before yawning
    wake_idle_s: float = 1.0           # Any input more recent than this wakes the pet
    alert_timeout_s: float = 30.0      # ALERT ("!") gives up after this long without a follow-up event
    agent_busy_expiry_s: float = 180.0 # A busy agent session with no events for this long is idle
