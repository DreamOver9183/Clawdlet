"""Work sessions: turn raw "Claude is busy" signals into typing segments with breaks (pure logic).

The FSM used to force TYPING on every frame that any signal was up, so a noisy Claude Desktop I/O
heuristic meant typing forever. A ``WorkSession`` sits between the signals and the FSM:

* typing comes in segments (length per activity mode) separated by short breaks;
* the signal must be gone for ``quiet_debounce_s`` before the session ends;
* a heuristic-only session is capped at ``heuristic_max_s``, after which the heuristic is ignored
  for ``heuristic_cooldown_s`` (hook / transcript / manual signals still work);
* right after a finished turn the heuristic is ignored for ``done_suppress_s`` (Desktop I/O tail);
* while subagents run, typing and juggling alternate (``juggle_segment_s`` each) instead of breaks.

No Tk / Win32 here; all randomness comes from the injected ``rng``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import random

from clawdlet.config import ActivityMode

PHASE_IDLE = "idle"
PHASE_TYPING = "typing"
PHASE_BREAK = "break"
PHASE_JUGGLE = "juggle"


@dataclass(frozen=True)
class WorkSignal:
    """What says "Claude is working" this tick."""

    heuristic: bool          # Claude Desktop I/O heuristic (noisy)
    agent_busy: bool         # A Claude Code session is busy (hooks / transcript)
    subagents: int = 0
    manual: bool = False     # The user asked for typing (double-click / tray)

    @property
    def strong(self) -> bool:
        return self.agent_busy or self.manual


class WorkDirective(str, Enum):
    """What the FSM should do after a ``WorkSession.update``."""

    NONE = "none"
    START = "start"    # A signal appeared: pull out the laptop
    BREAK = "break"    # Segment over: take a short break (laptop stays out)
    RESUME = "resume"  # Break / juggle over and still working: back to typing (no laptop intro)
    JUGGLE = "juggle"  # Subagents are running: juggle them (laptop stays out)
    END = "end"        # Signal gone long enough: put the laptop away
    CAP = "cap"        # Heuristic-only typing ran too long: put the laptop away, ignore the heuristic


def _default_segments() -> dict[ActivityMode, tuple[float, float]]:
    return {
        ActivityMode.QUIET: (20.0, 40.0),
        ActivityMode.NORMAL: (14.0, 28.0),
        ActivityMode.HYPER: (8.0, 16.0),
    }


@dataclass(frozen=True)
class WorkConfig:
    segment_s: dict[ActivityMode, tuple[float, float]] = field(default_factory=_default_segments)
    break_s: tuple[float, float] = (2.5, 4.5)
    heuristic_max_s: float = 60.0       # Longest heuristic-only session
    heuristic_cooldown_s: float = 90.0  # Heuristic ignored this long after a cap
    quiet_debounce_s: float = 0.8       # Signal must stay gone this long to end the session
    done_suppress_s: float = 8.0        # Heuristic ignored this long after a finished turn
    juggle_segment_s: tuple[float, float] = (6.0, 10.0)  # Typing / juggling turns while subagents run
    juggle_lead_s: float = 2.0          # A fresh segment types this long (the laptop intro) before juggling


class WorkSession:
    """Typing / break bookkeeping for one stretch of Claude activity."""

    def __init__(self, cfg: WorkConfig | None = None, rng: random.Random | None = None) -> None:
        self.cfg = cfg or WorkConfig()
        self._rng = rng or random.Random()
        self.phase: str = PHASE_IDLE
        self.phase_t: float = 0.0       # Time spent in the current segment / break
        self.phase_len: float = 0.0     # Drawn length of the current segment / break
        self._quiet_s: float = 0.0
        self._heuristic_run_s: float = 0.0
        self._cooldown_until: float = float("-inf")
        self._suppress_until: float = float("-inf")
        self._last_sig: WorkSignal = WorkSignal(heuristic=False, agent_busy=False)
        self._juggling: bool = False
        self.last_end: str = ""  # Why the previous session ended ("end" / "cap"), for --debug

    @property
    def active(self) -> bool:
        return self.phase != PHASE_IDLE

    def effective(self, sig: WorkSignal, now: float) -> bool:
        """Is there a work signal once the heuristic cooldown and done suppression are applied?"""
        if sig.strong:
            return True
        return sig.heuristic and now >= self._cooldown_until and now >= self._suppress_until

    def note_done(self, now: float) -> None:
        """A turn just finished: ignore the heuristic's I/O tail for a while."""
        self._suppress_until = max(self._suppress_until, now + self.cfg.done_suppress_s)

    @staticmethod
    def _juggle_wanted(sig: WorkSignal) -> bool:
        # Manual typing never juggles (it has its own timer and takes no breaks)
        return sig.subagents > 0 and not sig.manual

    def begin_segment(self, mode: ActivityMode) -> None:
        """Start a fresh typing segment (also used by the FSM when it re-enters typing).

        While subagents run, typing turns are as short as juggling turns.
        """
        if self._juggle_wanted(self._last_sig):
            lo, hi = self.cfg.juggle_segment_s
        else:
            lo, hi = self.cfg.segment_s[mode]
        self.phase = PHASE_TYPING
        self.phase_t = 0.0
        self.phase_len = self._rng.uniform(lo, hi)

    def _begin_break(self) -> None:
        lo, hi = self.cfg.break_s
        self.phase = PHASE_BREAK
        self.phase_t = 0.0
        self.phase_len = self._rng.uniform(lo, hi)

    def _begin_juggle(self) -> None:
        lo, hi = self.cfg.juggle_segment_s
        self.phase = PHASE_JUGGLE
        self.phase_t = 0.0
        self.phase_len = self._rng.uniform(lo, hi)

    def set_phase_len(self, length: float) -> None:
        """Let the FSM stretch / shorten the current break to the break row it picked."""
        self.phase_len = max(0.0, length)

    def _finish(self, reason: str) -> None:
        self.phase = PHASE_IDLE
        self.phase_t = 0.0
        self.phase_len = 0.0
        self._heuristic_run_s = 0.0
        self.last_end = reason

    def update(self, dt: float, sig: WorkSignal, now: float, mode: ActivityMode) -> WorkDirective:
        """Advance by ``dt`` seconds and return what the FSM should do."""
        self._last_sig = sig
        juggle = self._juggle_wanted(sig)
        started_juggling = juggle and not self._juggling
        self._juggling = juggle
        eff = self.effective(sig, now)
        self._quiet_s = 0.0 if eff else self._quiet_s + dt

        if not self.active:
            if not eff:
                return WorkDirective.NONE
            self._quiet_s = 0.0
            self._heuristic_run_s = 0.0
            self.begin_segment(mode)
            if juggle:
                self.phase_len = min(self.phase_len, self.cfg.juggle_lead_s)  # Intro, then juggle
            return WorkDirective.START

        # Heuristic-only sessions are capped; brief dips inside the debounce still count
        if sig.strong:
            self._heuristic_run_s = 0.0
        else:
            self._heuristic_run_s += dt
            if self._heuristic_run_s >= self.cfg.heuristic_max_s:
                self._cooldown_until = now + self.cfg.heuristic_cooldown_s
                self._finish("cap")
                return WorkDirective.CAP

        if self._quiet_s >= self.cfg.quiet_debounce_s:
            self._finish("end")
            return WorkDirective.END

        self.phase_t += dt
        if self.phase == PHASE_TYPING:
            if started_juggling:
                # Subagents just launched: juggle soon (once a fresh laptop intro has played)
                self.phase_len = min(self.phase_len, max(self.phase_t, self.cfg.juggle_lead_s))
            # Manual typing has its own timer in the FSM and never takes breaks
            if not sig.manual and self.phase_t >= self.phase_len:
                if juggle:
                    self._begin_juggle()
                    return WorkDirective.JUGGLE
                self._begin_break()
                return WorkDirective.BREAK
        elif self.phase == PHASE_JUGGLE:
            if not juggle or self.phase_t >= self.phase_len:
                # Last subagent done (back to a normal segment), or the next typing turn
                self.begin_segment(mode)
                return WorkDirective.RESUME
        elif self.phase_t >= self.phase_len and eff:
            if juggle:
                self._begin_juggle()
                return WorkDirective.JUGGLE
            self.begin_segment(mode)
            return WorkDirective.RESUME
        return WorkDirective.NONE

    def describe(self, now: float) -> str:
        """One-line summary for --debug, e.g. ``work=typing seg=12.3/21.0s src=agent cooldown=0s``."""
        sig = self._last_sig
        if sig.manual:
            src = "manual"
        elif sig.agent_busy:
            src = "agent"
        elif sig.heuristic:
            src = "io"
        else:
            src = "-"
        label = {PHASE_BREAK: "brk", PHASE_JUGGLE: "jug"}.get(self.phase, "seg")
        parts = [f"work={self.phase}"]
        if self.active:
            parts.append(f"{label}={self.phase_t:.1f}/{self.phase_len:.1f}s")
        parts.append(f"src={src}")
        if sig.subagents:
            parts.append(f"sub={sig.subagents}")
        parts.append(f"cooldown={max(0.0, self._cooldown_until - now):.0f}s")
        if now < self._suppress_until:
            parts.append(f"done_mute={self._suppress_until - now:.0f}s")
        if self.last_end:
            parts.append(f"last={self.last_end}")
        return " ".join(parts)
