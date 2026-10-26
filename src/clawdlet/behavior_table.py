"""External behavior table: which idle animation Clawd picks, from a JSON file (Shimeji style).

The bundled ``assets/behaviors/behaviors.default.json`` is merged with the user's optional
``%APPDATA%\\Clawdlet\\behaviors.json``. The FSM keeps every engine-driven decision (dragging,
falling, reactions, work, seeking Claude, wandering); the table only decides what happens in the
idle tail of a rest decision, plus the lists later phases read (work breaks, fidgets, entrances).

Rows
    ``id`` (``[a-z0-9_]+``, unique), ``state`` (a ``PetState`` name from ``SELECTABLE_STATES``),
    ``weight`` (number or ``{"quiet", "normal", "hyper"}``; a missing mode weighs 0), ``chance``
    (0..1: an opportunistic row rolled before the weighted pick), ``on`` (trigger), ``when``
    (conditions, all must hold), ``duration_s`` ``[lo, hi]``, ``scale_by_rest``, ``cooldown_s``,
    ``next`` ``[{id, weight}]`` with ``next_mode`` ``add`` | ``replace``, ``enabled``,
    ``description``. Keys starting with ``$`` are ignored everywhere.

Merging (user file)
    ``extends: "default"`` (the default): a row whose ``id`` already exists is shallow-patched
    (``{"id": "floor_blob", "enabled": false}``; a ``when`` / ``next`` / ``weight`` value replaces
    the old one wholesale), new ids are appended; ``settings`` are deep-merged.
    ``extends: "none"``: the user file replaces the bundled table.

Validation is forgiving: an invalid row is dropped with a warning naming it, unknown keys warn,
and a user file that does not parse (or has the wrong version) is ignored with one message.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import random
import re
import shutil
import sys
from typing import Any

from clawdlet.config import ActivityMode
from clawdlet.paths import behaviors_schema_path, default_behaviors_path, user_behaviors_path
from clawdlet.states import PetState
from clawdlet.work_session import WorkConfig

FORMAT_VERSION = 1
MODES = ("quiet", "normal", "hyper")
SURFACES = ("floor", "perch")
SPOTS = ("open", "screen_edge", "window_side", "perch_edge", "perch_middle")
TRIGGERS = ("rest", "wall_bump", "window_side")
EXTENDS = ("default", "none")
NEXT_MODES = ("add", "replace")
FIDGET_POSES = ("look_left", "look_right", "blink", "look_claude")
ENTRANCE_KINDS = ("drop_on_claude", "drop_top", "walk_in_side")
MAX_CHAIN_DEPTH = 4
DURATION_RANGE = (0.2, 120.0)
SECTIONS = ("behaviors", "work_breaks", "fidgets", "entrances")

# States the table may start. Later phases extend these as their states land in PetState.
SELECTABLE_STATES = frozenset({"IDLE", "BLOB", "PERCH_CLAUDE"})
WORK_BREAK_STATES = frozenset({"WORK_GLANCE", "WORK_STRETCH", "WORK_THINK", "WORK_SIP"})

# Surfaces each selectable state can be shown on (intersected with ``when.surface``)
_STATE_SURFACES: dict[str, tuple[str, ...]] = {
    "IDLE": ("floor",),
    "BLOB": ("floor", "perch"),
    "PERCH_CLAUDE": ("perch",),
    "WORK_GLANCE": ("floor", "perch"),
    "WORK_STRETCH": ("floor", "perch"),
    "WORK_THINK": ("floor", "perch"),
    "WORK_SIP": ("floor", "perch"),
}

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_TOP_KEYS = frozenset({"version", "extends", "settings", *SECTIONS})
_WHEN_KEYS = frozenset({
    "surface",
    "spot",
    "mode",
    "min_idle_level",
    "max_idle_level",
    "user_idle_s",
    "claude_visible",
    "claude_foreground",
    "cursor_in_window",
    "prev_state",
    "not_prev_state",
})
_BEHAVIOR_KEYS = frozenset({
    "id",
    "state",
    "weight",
    "chance",
    "on",
    "when",
    "duration_s",
    "scale_by_rest",
    "cooldown_s",
    "next",
    "next_mode",
    "enabled",
    "description",
})
_FIDGET_KEYS = frozenset(
    {"id", "pose", "weight", "when", "duration_s", "cooldown_s", "enabled", "description"}
)
_ENTRANCE_KEYS = frozenset({"id", "kind", "weight", "when", "enabled", "description"})
_SETTINGS_KEYS = frozenset({
    "idle_level2_after_s",
    "fidget_interval_s",
    "mail_away_s",
    "alert_escalate_s",
    "work",
})
_WORK_KEYS = frozenset(
    {"segment_s", "break_s", "heuristic_max_s", "heuristic_cooldown_s", "juggle_segment_s"}
)


class _RowError(ValueError):
    """A row is unusable and gets dropped."""


# --- Data model ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class IdleContext:
    """What the selector may condition on at one decision."""

    surface: str                       # "floor" | "perch"
    spots: frozenset[str] = frozenset()
    mode: str = "normal"
    idle_level: int = 1                # 1 active, 2 after idle_level2_after_s, 3 once sleepy
    user_idle_s: float | None = None   # None without a pointer sample
    claude_visible: bool = False
    claude_foreground: bool = False
    cursor_in_window: bool = False
    prev_state: str | None = None      # PetState name being left
    trigger: str = "rest"
    now: float = 0.0                   # Controller clock, for cooldowns


@dataclass(frozen=True)
class Condition:
    """``when`` block: every given key must hold (lists match any of their entries)."""

    surface: tuple[str, ...] | None = None
    spot: tuple[str, ...] | None = None
    mode: tuple[str, ...] | None = None
    min_idle_level: int | None = None
    max_idle_level: int | None = None
    user_idle_s: tuple[float, float | None] | None = None
    claude_visible: bool | None = None
    claude_foreground: bool | None = None
    cursor_in_window: bool | None = None
    prev_state: tuple[str, ...] | None = None
    not_prev_state: tuple[str, ...] | None = None

    def matches(self, ctx: IdleContext) -> bool:
        if self.surface is not None and ctx.surface not in self.surface:
            return False
        if self.spot is not None and not any(s in ctx.spots for s in self.spot):
            return False
        if self.mode is not None and ctx.mode not in self.mode:
            return False
        if self.min_idle_level is not None and ctx.idle_level < self.min_idle_level:
            return False
        if self.max_idle_level is not None and ctx.idle_level > self.max_idle_level:
            return False
        if self.user_idle_s is not None:
            lo, hi = self.user_idle_s
            if ctx.user_idle_s is None or ctx.user_idle_s < lo or (hi is not None and ctx.user_idle_s > hi):
                return False
        if self.claude_visible is not None and ctx.claude_visible != self.claude_visible:
            return False
        if self.claude_foreground is not None and ctx.claude_foreground != self.claude_foreground:
            return False
        if self.cursor_in_window is not None and ctx.cursor_in_window != self.cursor_in_window:
            return False
        if self.prev_state is not None and ctx.prev_state not in self.prev_state:
            return False
        if self.not_prev_state is not None and ctx.prev_state in self.not_prev_state:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in (
            "surface",
            "spot",
            "mode",
            "min_idle_level",
            "max_idle_level",
            "user_idle_s",
            "claude_visible",
            "claude_foreground",
            "cursor_in_window",
            "prev_state",
            "not_prev_state",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = list(value) if isinstance(value, tuple) else value
        return out


Weight = float | dict[str, float]


def _weight_for(weight: Weight, mode: str) -> float:
    if isinstance(weight, dict):
        return float(weight.get(mode, 0.0))
    return float(weight)


@dataclass(frozen=True)
class NextRef:
    id: str
    weight: float = 1.0


@dataclass(frozen=True)
class BehaviorRow:
    """One selectable behavior (also used for ``work_breaks``)."""

    id: str
    state: str
    weight: Weight = 1.0
    chance: float | None = None
    on: str = "rest"
    when: Condition = field(default_factory=Condition)
    duration_s: tuple[float, float] | None = None
    scale_by_rest: bool = True
    cooldown_s: float = 0.0
    next: tuple[NextRef, ...] = ()
    next_mode: str = "add"
    enabled: bool = True
    description: str = ""
    surfaces: tuple[str, ...] = SURFACES  # when.surface ∩ the state's allowed surfaces

    def weight_for(self, mode: str) -> float:
        return _weight_for(self.weight, mode)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "state": self.state, "weight": self.weight}
        if self.chance is not None:
            out["chance"] = self.chance
        out["on"] = self.on
        when = self.when.to_dict()
        if when:
            out["when"] = when
        if self.duration_s is not None:
            out["duration_s"] = list(self.duration_s)
        out["scale_by_rest"] = self.scale_by_rest
        if self.cooldown_s:
            out["cooldown_s"] = self.cooldown_s
        if self.next:
            out["next"] = [{"id": n.id, "weight": n.weight} for n in self.next]
            out["next_mode"] = self.next_mode
        out["enabled"] = self.enabled
        if self.description:
            out["description"] = self.description
        return out


WorkBreakRow = BehaviorRow


@dataclass(frozen=True)
class FidgetRow:
    """A cosmetic pose played over a resting state (Phase 5)."""

    id: str
    pose: str
    weight: Weight = 1.0
    when: Condition = field(default_factory=Condition)
    duration_s: tuple[float, float] = (0.6, 1.4)
    cooldown_s: float = 0.0
    enabled: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "pose": self.pose, "weight": self.weight}
        when = self.when.to_dict()
        if when:
            out["when"] = when
        out["duration_s"] = list(self.duration_s)
        if self.cooldown_s:
            out["cooldown_s"] = self.cooldown_s
        out["enabled"] = self.enabled
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class EntranceRow:
    """How Clawd arrives on screen at startup / summon (Phase 6)."""

    id: str
    kind: str
    weight: Weight = 1.0
    when: Condition = field(default_factory=Condition)
    enabled: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "weight": self.weight}
        when = self.when.to_dict()
        if when:
            out["when"] = when
        out["enabled"] = self.enabled
        if self.description:
            out["description"] = self.description
        return out


def _default_segments() -> dict[str, tuple[float, float]]:
    return {mode.value: span for mode, span in WorkConfig().segment_s.items()}


@dataclass(frozen=True)
class WorkSettings:
    """``settings.work``: typing segment / break lengths and the heuristic cap."""

    segment_s: dict[str, tuple[float, float]] = field(default_factory=_default_segments)
    break_s: tuple[float, float] = WorkConfig().break_s
    heuristic_max_s: float = WorkConfig().heuristic_max_s
    heuristic_cooldown_s: float = WorkConfig().heuristic_cooldown_s
    juggle_segment_s: tuple[float, float] = WorkConfig().juggle_segment_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_s": {mode: list(span) for mode, span in self.segment_s.items()},
            "break_s": list(self.break_s),
            "heuristic_max_s": self.heuristic_max_s,
            "heuristic_cooldown_s": self.heuristic_cooldown_s,
            "juggle_segment_s": list(self.juggle_segment_s),
        }


@dataclass(frozen=True)
class BehaviorSettings:
    idle_level2_after_s: float = 45.0
    fidget_interval_s: tuple[float, float] = (4.0, 9.0)
    mail_away_s: float = 30.0
    alert_escalate_s: float = 10.0
    work: WorkSettings = field(default_factory=WorkSettings)

    def work_config(self) -> WorkConfig:
        """The ``WorkConfig`` for the controller's work session (the table is its single source)."""
        base = WorkConfig()
        return WorkConfig(
            segment_s={ActivityMode(mode): span for mode, span in self.work.segment_s.items()},
            break_s=self.work.break_s,
            heuristic_max_s=self.work.heuristic_max_s,
            heuristic_cooldown_s=self.work.heuristic_cooldown_s,
            quiet_debounce_s=base.quiet_debounce_s,
            done_suppress_s=base.done_suppress_s,
            juggle_segment_s=self.work.juggle_segment_s,
            juggle_lead_s=base.juggle_lead_s,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "idle_level2_after_s": self.idle_level2_after_s,
            "fidget_interval_s": list(self.fidget_interval_s),
            "mail_away_s": self.mail_away_s,
            "alert_escalate_s": self.alert_escalate_s,
            "work": self.work.to_dict(),
        }


@dataclass(frozen=True)
class BehaviorTable:
    settings: BehaviorSettings = field(default_factory=BehaviorSettings)
    behaviors: tuple[BehaviorRow, ...] = ()
    work_breaks: tuple[BehaviorRow, ...] = ()
    fidgets: tuple[FidgetRow, ...] = ()
    entrances: tuple[EntranceRow, ...] = ()
    warnings: tuple[str, ...] = ()
    origin: str = ""

    def row(self, row_id: str) -> BehaviorRow | None:
        for row in self.behaviors:
            if row.id == row_id:
                return row
        return None

    def to_dict(self) -> dict[str, Any]:
        """The merged table in file format (``extends: none``), e.g. for ``--dump-behaviors``."""
        return {
            "version": FORMAT_VERSION,
            "extends": "none",
            "settings": self.settings.to_dict(),
            "behaviors": [r.to_dict() for r in self.behaviors],
            "work_breaks": [r.to_dict() for r in self.work_breaks],
            "fidgets": [r.to_dict() for r in self.fidgets],
            "entrances": [r.to_dict() for r in self.entrances],
        }


# --- Validation ---------------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unknown_keys(raw: Mapping[str, Any], allowed: frozenset[str], where: str, warnings: list[str]) -> None:
    for key in raw:
        if key not in allowed and not str(key).startswith("$"):
            warnings.append(f"{where}: unknown key '{key}' ignored")


def _str_list(value: Any, allowed: tuple[str, ...] | frozenset[str], what: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise _RowError(f"{what} must be a non-empty list")
    for item in value:
        if item not in allowed:
            raise _RowError(f"{what}: unknown value {item!r}")
    return tuple(value)


def _span(value: Any, what: str, lo_min: float, hi_max: float | None = None) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2 or not all(_is_number(v) for v in value):
        raise _RowError(f"{what} must be [min, max]")
    lo, hi = float(value[0]), float(value[1])
    if lo < lo_min or lo > hi or (hi_max is not None and hi > hi_max):
        bound = f"{lo_min}..{hi_max}" if hi_max is not None else f">= {lo_min}"
        raise _RowError(f"{what} must satisfy {bound} with min <= max, got {value}")
    return (lo, hi)


def _weight(value: Any, what: str = "weight") -> Weight:
    if _is_number(value):
        if value < 0:
            raise _RowError(f"{what} must be >= 0, got {value}")
        return float(value)
    if isinstance(value, dict):
        out: dict[str, float] = {}
        for mode, w in value.items():
            if mode not in MODES:
                raise _RowError(f"{what}: unknown mode {mode!r}")
            if not _is_number(w) or w < 0:
                raise _RowError(f"{what}.{mode} must be a number >= 0, got {w!r}")
            out[mode] = float(w)
        return out
    raise _RowError(f"{what} must be a number or {{quiet, normal, hyper}}")


def _bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _RowError(f"{what} must be true or false")
    return value


def _number(value: Any, what: str, minimum: float = 0.0) -> float:
    if not _is_number(value) or value < minimum:
        raise _RowError(f"{what} must be a number >= {minimum}")
    return float(value)


def _parse_condition(raw: Any, where: str, warnings: list[str]) -> Condition:
    if raw is None:
        return Condition()
    if not isinstance(raw, dict):
        raise _RowError("when must be an object")
    _unknown_keys(raw, _WHEN_KEYS, f"{where}.when", warnings)
    state_names = tuple(PetState.__members__)
    kw: dict[str, Any] = {}
    if "surface" in raw:
        kw["surface"] = _str_list(raw["surface"], SURFACES, "when.surface")
    if "spot" in raw:
        kw["spot"] = _str_list(raw["spot"], SPOTS, "when.spot")
    if "mode" in raw:
        kw["mode"] = _str_list(raw["mode"], MODES, "when.mode")
    for key in ("min_idle_level", "max_idle_level"):
        if key in raw:
            level = raw[key]
            if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 3:
                raise _RowError(f"when.{key} must be 1, 2 or 3")
            kw[key] = level
    if "user_idle_s" in raw:
        span = raw["user_idle_s"]
        if (
            not isinstance(span, list)
            or len(span) != 2
            or not _is_number(span[0])
            or not (span[1] is None or _is_number(span[1]))
            or span[0] < 0
            or (span[1] is not None and span[1] < span[0])
        ):
            raise _RowError("when.user_idle_s must be [min, max or null]")
        kw["user_idle_s"] = (float(span[0]), None if span[1] is None else float(span[1]))
    for key in ("claude_visible", "claude_foreground", "cursor_in_window"):
        if key in raw:
            kw[key] = _bool(raw[key], f"when.{key}")
    for key in ("prev_state", "not_prev_state"):
        if key in raw:
            kw[key] = _str_list(raw[key], state_names, f"when.{key}")
    return Condition(**kw)


def _parse_id(raw: dict[str, Any], seen: set[str]) -> str:
    row_id = raw.get("id")
    if not isinstance(row_id, str) or not _ID_RE.match(row_id):
        raise _RowError(f"id must match [a-z0-9_]+, got {row_id!r}")
    if row_id in seen:
        raise _RowError("duplicate id")
    return row_id


def _parse_behavior_row(
    raw: Any,
    section: str,
    allowed_states: frozenset[str],
    seen: set[str],
    warnings: list[str],
) -> BehaviorRow:
    if not isinstance(raw, dict):
        raise _RowError("row must be an object")
    row_id = _parse_id(raw, seen)
    where = f"{section}[{row_id}]"
    _unknown_keys(raw, _BEHAVIOR_KEYS, where, warnings)
    state = raw.get("state")
    if state not in allowed_states:
        allowed = ", ".join(sorted(allowed_states))
        raise _RowError(f"state {state!r} is not selectable here (allowed: {allowed})")
    when = _parse_condition(raw.get("when"), where, warnings)
    surfaces = tuple(
        s for s in _STATE_SURFACES.get(state, SURFACES) if when.surface is None or s in when.surface
    )
    if not surfaces:
        raise _RowError(f"state {state} cannot appear on surface {list(when.surface or ())}")
    duration: tuple[float, float] | None = None
    if "duration_s" in raw:
        duration = _span(raw["duration_s"], "duration_s", *DURATION_RANGE)
    elif section == "behaviors":
        raise _RowError("duration_s is required")
    chance = None
    if raw.get("chance") is not None:
        chance = raw["chance"]
        if not _is_number(chance) or not 0.0 <= chance <= 1.0:
            raise _RowError(f"chance must be between 0 and 1, got {chance!r}")
        chance = float(chance)
    trigger = raw.get("on", "rest")
    if trigger not in TRIGGERS:
        raise _RowError(f"on must be one of {', '.join(TRIGGERS)}, got {trigger!r}")
    next_mode = raw.get("next_mode", "add")
    if next_mode not in NEXT_MODES:
        raise _RowError(f"next_mode must be add or replace, got {next_mode!r}")
    refs: list[NextRef] = []
    next_raw = raw.get("next", [])
    if not isinstance(next_raw, list):
        raise _RowError("next must be a list of {id, weight}")
    for entry in next_raw:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise _RowError("next entries must be {id, weight}")
        refs.append(NextRef(entry["id"], _number(entry.get("weight", 1.0), "next.weight")))
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise _RowError("description must be a string")
    return BehaviorRow(
        id=row_id,
        state=state,
        weight=_weight(raw.get("weight", 1.0)),
        chance=chance,
        on=trigger,
        when=when,
        duration_s=duration,
        scale_by_rest=_bool(raw.get("scale_by_rest", True), "scale_by_rest"),
        cooldown_s=_number(raw.get("cooldown_s", 0.0), "cooldown_s"),
        next=tuple(refs),
        next_mode=next_mode,
        enabled=_bool(raw.get("enabled", True), "enabled"),
        description=description,
        surfaces=surfaces,
    )


def _parse_fidget_row(raw: Any, seen: set[str], warnings: list[str]) -> FidgetRow:
    if not isinstance(raw, dict):
        raise _RowError("row must be an object")
    row_id = _parse_id(raw, seen)
    where = f"fidgets[{row_id}]"
    _unknown_keys(raw, _FIDGET_KEYS, where, warnings)
    pose = raw.get("pose")
    if pose not in FIDGET_POSES:
        raise _RowError(f"pose must be one of {', '.join(FIDGET_POSES)}, got {pose!r}")
    return FidgetRow(
        id=row_id,
        pose=pose,
        weight=_weight(raw.get("weight", 1.0)),
        when=_parse_condition(raw.get("when"), where, warnings),
        duration_s=_span(raw.get("duration_s", [0.6, 1.4]), "duration_s", 0.05, DURATION_RANGE[1]),
        cooldown_s=_number(raw.get("cooldown_s", 0.0), "cooldown_s"),
        enabled=_bool(raw.get("enabled", True), "enabled"),
        description=str(raw.get("description", "")),
    )


def _parse_entrance_row(raw: Any, seen: set[str], warnings: list[str]) -> EntranceRow:
    if not isinstance(raw, dict):
        raise _RowError("row must be an object")
    row_id = _parse_id(raw, seen)
    where = f"entrances[{row_id}]"
    _unknown_keys(raw, _ENTRANCE_KEYS, where, warnings)
    kind = raw.get("kind")
    if kind not in ENTRANCE_KINDS:
        raise _RowError(f"kind must be one of {', '.join(ENTRANCE_KINDS)}, got {kind!r}")
    return EntranceRow(
        id=row_id,
        kind=kind,
        weight=_weight(raw.get("weight", 1.0)),
        when=_parse_condition(raw.get("when"), where, warnings),
        enabled=_bool(raw.get("enabled", True), "enabled"),
        description=str(raw.get("description", "")),
    )


def _row_label(section: str, raw: Any, index: int) -> str:
    row_id = raw.get("id") if isinstance(raw, dict) else None
    return f"{section}[{row_id}]" if isinstance(row_id, str) else f"{section}[#{index}]"


def _parse_rows(raw_rows: Any, section: str, warnings: list[str]) -> tuple[Any, ...]:
    if raw_rows is None:
        return ()
    if not isinstance(raw_rows, list):
        warnings.append(f"{section}: must be a list; section ignored")
        return ()
    seen: set[str] = set()
    rows: list[Any] = []
    for index, raw in enumerate(raw_rows):
        try:
            if section == "behaviors":
                row = _parse_behavior_row(raw, section, SELECTABLE_STATES, seen, warnings)
            elif section == "work_breaks":
                row = _parse_behavior_row(raw, section, WORK_BREAK_STATES, seen, warnings)
            elif section == "fidgets":
                row = _parse_fidget_row(raw, seen, warnings)
            else:
                row = _parse_entrance_row(raw, seen, warnings)
        except _RowError as exc:
            warnings.append(f"{_row_label(section, raw, index)} dropped: {exc}")
            continue
        seen.add(row.id)
        rows.append(row)
    return tuple(rows)


def _resolve_next(
    rows: tuple[BehaviorRow, ...], section: str, warnings: list[str]
) -> tuple[BehaviorRow, ...]:
    ids = {row.id for row in rows}
    out: list[BehaviorRow] = []
    for row in rows:
        kept = tuple(ref for ref in row.next if ref.id in ids)
        for ref in row.next:
            if ref.id not in ids:
                warnings.append(f"{section}[{row.id}]: next id '{ref.id}' does not exist; ignored")
        out.append(row if len(kept) == len(row.next) else replace(row, next=kept))
    return tuple(out)


def _parse_settings(raw: Any, warnings: list[str]) -> BehaviorSettings:
    default = BehaviorSettings()
    if raw is None:
        return default
    if not isinstance(raw, dict):
        warnings.append("settings: must be an object; defaults used")
        return default
    _unknown_keys(raw, _SETTINGS_KEYS, "settings", warnings)
    kw: dict[str, Any] = {}

    def _try(key: str, parse: Any) -> None:
        if key not in raw:
            return
        try:
            kw[key] = parse(raw[key])
        except _RowError as exc:
            warnings.append(f"settings.{key}: {exc}; default kept")

    _try("idle_level2_after_s", lambda v: _number(v, "value"))
    _try("fidget_interval_s", lambda v: _span(v, "value", 0.1))
    _try("mail_away_s", lambda v: _number(v, "value"))
    _try("alert_escalate_s", lambda v: _number(v, "value"))
    work_raw = raw.get("work")
    if work_raw is not None:
        if not isinstance(work_raw, dict):
            warnings.append("settings.work: must be an object; defaults used")
        else:
            kw["work"] = _parse_work_settings(work_raw, warnings)
    return BehaviorSettings(**kw)


def _parse_work_settings(raw: dict[str, Any], warnings: list[str]) -> WorkSettings:
    _unknown_keys(raw, _WORK_KEYS, "settings.work", warnings)
    default = WorkSettings()
    kw: dict[str, Any] = {}
    if "segment_s" in raw:
        seg = raw["segment_s"]
        segments = dict(default.segment_s)
        if not isinstance(seg, dict):
            warnings.append("settings.work.segment_s: must be {quiet, normal, hyper}; default kept")
        else:
            for mode, span in seg.items():
                if mode not in MODES:
                    warnings.append(f"settings.work.segment_s: unknown mode '{mode}' ignored")
                    continue
                try:
                    segments[mode] = _span(span, "value", 0.5)
                except _RowError as exc:
                    warnings.append(f"settings.work.segment_s.{mode}: {exc}; default kept")
        kw["segment_s"] = segments
    for key, parse in (
        ("break_s", lambda v: _span(v, "value", 0.2)),
        ("heuristic_max_s", lambda v: _number(v, "value", 1.0)),
        ("heuristic_cooldown_s", lambda v: _number(v, "value")),
        ("juggle_segment_s", lambda v: _span(v, "value", 0.5)),
    ):
        if key in raw:
            try:
                kw[key] = parse(raw[key])
            except _RowError as exc:
                warnings.append(f"settings.work.{key}: {exc}; default kept")
    return WorkSettings(**kw)


def build_behavior_table(data: Mapping[str, Any], origin: str = "") -> BehaviorTable:
    """Validate an already merged table document (rows that fail are dropped with a warning)."""
    warnings: list[str] = []
    _unknown_keys(data, _TOP_KEYS, "top level", warnings)
    settings = _parse_settings(data.get("settings"), warnings)
    behaviors = _resolve_next(
        _parse_rows(data.get("behaviors"), "behaviors", warnings), "behaviors", warnings
    )
    work_breaks = _resolve_next(
        _parse_rows(data.get("work_breaks"), "work_breaks", warnings), "work_breaks", warnings
    )
    fidgets = _parse_rows(data.get("fidgets"), "fidgets", warnings)
    entrances = _parse_rows(data.get("entrances"), "entrances", warnings)
    return BehaviorTable(
        settings=settings,
        behaviors=behaviors,
        work_breaks=work_breaks,
        fidgets=fidgets,
        entrances=entrances,
        warnings=tuple(warnings),
        origin=origin,
    )


# --- Loading and merging ------------------------------------------------------------------------


# The pre-table idle tail as data. Used when the bundled file is missing or broken, and kept
# identical to behaviors.default.json (a test checks it). Weights are in "legacy roll" units: the
# FSM maps the part of its rest roll left after wandering onto these rows, so e.g. on the floor in
# Normal mode (wander below 0.62) BLOB covers 0.62..0.85 and IDLE 0.85..1.0 exactly as before.
LEGACY_DATA: dict[str, Any] = {
    "version": FORMAT_VERSION,
    "settings": BehaviorSettings().to_dict(),
    "behaviors": [
        {
            "id": "floor_blob",
            "state": "BLOB",
            "weight": {"quiet": 0.63, "normal": 0.23, "hyper": 0.0},
            "when": {"surface": ["floor"], "not_prev_state": ["BLOB"]},
            "duration_s": [2.5, 4.5],
            "scale_by_rest": True,
            "description": "Melt into a puddle on the floor (never twice in a row).",
        },
        {
            "id": "floor_idle",
            "state": "IDLE",
            "weight": 0.15,
            "when": {"surface": ["floor"]},
            "duration_s": [2.2, 4.0],
            "scale_by_rest": True,
            "description": "Stand and breathe on the floor.",
        },
        {
            "id": "perch_blob",
            "state": "BLOB",
            "weight": {"quiet": 0.533, "normal": 0.193, "hyper": 0.0},
            "when": {"surface": ["perch"], "not_prev_state": ["BLOB", "WANDER"]},
            "duration_s": [2.4, 4.2],
            "scale_by_rest": True,
            "description": "Melt on Claude's title bar when a stroll was possible but not chosen.",
        },
        {
            "id": "perch_look_down",
            "state": "PERCH_CLAUDE",
            "weight": 0.28,
            "when": {"surface": ["perch"], "not_prev_state": ["WANDER"]},
            "duration_s": [3.0, 5.5],
            "scale_by_rest": True,
            "description": "Sit on the title bar and peek down at the chat.",
        },
        {
            "id": "perch_blob_after_stroll",
            "state": "BLOB",
            "weight": 0.72,
            "when": {"surface": ["perch"], "prev_state": ["WANDER"]},
            "duration_s": [2.4, 4.2],
            "scale_by_rest": True,
            "description": "Melt on the title bar after a stroll along it.",
        },
        {
            "id": "perch_look_down_after_stroll",
            "state": "PERCH_CLAUDE",
            "weight": 0.28,
            "when": {"surface": ["perch"], "prev_state": ["WANDER"]},
            "duration_s": [3.0, 5.5],
            "scale_by_rest": True,
            "description": "Sit and peek down at the chat after a stroll along the title bar.",
        },
    ],
    "work_breaks": [
        {
            "id": "stretch",
            "state": "WORK_STRETCH",
            "weight": 3.0,
            "duration_s": [2.5, 3.5],
            "cooldown_s": 60.0,
            "description": "Stretch both claws overhead beside the laptop (the strip holds its last, resting frame).",
        },
        {
            "id": "think",
            "state": "WORK_THINK",
            "weight": 4.0,
            "duration_s": [3.0, 5.0],
            "description": "Rest the chin on a claw and ponder, '...' over the head.",
        },
        {
            "id": "sip",
            "state": "WORK_SIP",
            "weight": 3.0,
            "duration_s": [2.5, 4.0],
            "cooldown_s": 90.0,
            "description": "Sip from the steaming mug next to the laptop.",
        },
        {
            "id": "glance",
            "state": "WORK_GLANCE",
            "weight": 2.0,
            "duration_s": [1.5, 2.5],
            "description": "Look up from the laptop for a moment.",
        },
    ],
    "fidgets": [],
    "entrances": [],
}

LEGACY_TABLE = build_behavior_table(LEGACY_DATA, origin="legacy")


def _report(message: str) -> None:
    print(f"[Clawdlet] {message}", file=sys.stderr)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"cannot read ({exc.strerror or exc})"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno} column {exc.colno} ({exc.msg})"
    if not isinstance(data, dict):
        return None, "top level must be an object"
    version = data.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        return None, f"unsupported version {version!r} (expected {FORMAT_VERSION})"
    return data, None


def _merge_settings(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            out[key] = _merge_settings(base.get(key), value)
        return out
    return copy.deepcopy(patch)


def _merge_rows(base_rows: Any, user_rows: Any) -> Any:
    if not isinstance(user_rows, list) or not isinstance(base_rows, list):
        return copy.deepcopy(user_rows)
    merged = [copy.deepcopy(row) for row in base_rows]
    index = {row.get("id"): i for i, row in enumerate(merged) if isinstance(row, dict)}
    for row in user_rows:
        row_id = row.get("id") if isinstance(row, dict) else None
        if isinstance(row_id, str) and row_id in index:
            merged[index[row_id]].update(copy.deepcopy(row))
        else:
            if isinstance(row_id, str):
                index[row_id] = len(merged)
            merged.append(copy.deepcopy(row))
    return merged


def merge_behavior_data(base: Mapping[str, Any], user: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply a user document over the bundled one (see the module docstring for the rules)."""
    warnings: list[str] = []
    extends = user.get("extends", "default")
    if extends not in EXTENDS:
        warnings.append(f"extends must be 'default' or 'none', got {extends!r}; using 'default'")
        extends = "default"
    if extends == "none":
        return {k: copy.deepcopy(v) for k, v in user.items() if k != "extends"}, warnings
    merged: dict[str, Any] = {k: copy.deepcopy(v) for k, v in base.items()}
    for key, value in user.items():
        if key in ("extends", "version") or str(key).startswith("$"):
            continue
        if key == "settings":
            merged["settings"] = _merge_settings(base.get("settings", {}), value)
        elif key in SECTIONS:
            merged[key] = _merge_rows(base.get(key, []), value)
        else:
            merged[key] = copy.deepcopy(value)  # Unknown: the validator warns about it
    return merged, warnings


def _bundled_data(default_path: Path | None) -> tuple[dict[str, Any], str, list[str]]:
    path = default_path or default_behaviors_path()
    data, error = _read_json(path)
    if data is None:
        return copy.deepcopy(LEGACY_DATA), "legacy", [f"bundled {path.name}: {error}; built-in table used"]
    table = build_behavior_table(data)
    if table.warnings:
        notes = [f"bundled {path.name}: {w}" for w in table.warnings]
        return copy.deepcopy(LEGACY_DATA), "legacy", [*notes, "bundled table invalid; built-in table used"]
    return data, str(path), []


def load_bundled_behavior_table(default_path: Path | None = None) -> BehaviorTable:
    """The table shipped with Clawdlet (never reads the user's APPDATA). Falls back to ``LEGACY_TABLE``."""
    data, origin, notes = _bundled_data(default_path)
    table = build_behavior_table(data, origin=origin)
    if notes:
        table = _with_warnings(table, notes + list(table.warnings))
    return table


def load_behavior_table(user_path: Path | None = None, default_path: Path | None = None) -> BehaviorTable:
    """The bundled table merged with the user's file (``%APPDATA%\\Clawdlet\\behaviors.json`` by default).

    A missing default user file is normal. A user file that cannot be read, is not valid JSON or
    has the wrong version is ignored with one message on stderr. Row-level problems end up in
    ``table.warnings`` with the offending rows dropped.
    """
    base, origin, notes = _bundled_data(default_path)
    explicit = user_path is not None
    path = user_path if user_path is not None else user_behaviors_path()
    if not explicit and not path.exists():
        table = build_behavior_table(base, origin=origin)
        return _with_warnings(table, notes + list(table.warnings)) if notes else table
    user, error = _read_json(path)
    if user is None:
        _report(f"{path.name}: {error} — using defaults")
        table = build_behavior_table(base, origin=origin)
        return _with_warnings(table, notes + list(table.warnings)) if notes else table
    merged, merge_notes = merge_behavior_data(base, user)
    table = build_behavior_table(merged, origin=f"{origin} + {path}")
    return _with_warnings(table, notes + merge_notes + [f"{path.name}: {w}" for w in table.warnings])


def _with_warnings(table: BehaviorTable, warnings: list[str]) -> BehaviorTable:
    return replace(table, warnings=tuple(warnings))


def dump_behavior_table(table: BehaviorTable) -> str:
    return json.dumps(table.to_dict(), indent=2, ensure_ascii=False)


USER_TEMPLATE: dict[str, Any] = {
    "$schema": "./behaviors.schema.json",
    "version": FORMAT_VERSION,
    "extends": "default",
    "behaviors": [],
}


def ensure_user_config(config_dir: Path) -> Path:
    """Create ``behaviors.json`` (an empty ``extends: default`` override) if missing.

    The editor schema is copied next to it so ``"$schema": "./behaviors.schema.json"`` gives
    autocompletion in VS Code. Returns the user file path.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    schema = behaviors_schema_path()
    try:
        if schema.exists():
            shutil.copyfile(schema, config_dir / schema.name)
    except OSError:
        pass
    path = config_dir / "behaviors.json"
    if not path.exists():
        path.write_text(json.dumps(USER_TEMPLATE, indent=2) + "\n", encoding="utf-8")
    return path


# --- Selection ----------------------------------------------------------------------------------

WORK_BREAK_TRIGGER = "work_break"
_WORK_BREAK_KEY = "work_breaks/"  # Cooldown keys: a break id may repeat a behavior id


class BehaviorSelector:
    """Pick behavior rows: opportunistic ``chance`` rows first, then a weighted pick.

    ``u`` lets the FSM reuse the uniform roll it already drew, so the default table reproduces
    the old thresholds exactly; only ``chance`` rows (and picks without ``u``) draw from ``rng``.
    """

    def __init__(self, table: BehaviorTable, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._last_start: dict[str, float] = {}
        self._chain_depth = 0
        self.set_table(table)

    def set_table(self, table: BehaviorTable) -> None:
        self.table = table
        self._by_id = {row.id: row for row in table.behaviors}
        self._chain_depth = 0

    def _eligible(
        self, row: BehaviorRow, trigger: str | None, ctx: IdleContext, key: str | None = None
    ) -> bool:
        if not row.enabled or (trigger is not None and row.on != trigger):
            return False
        if ctx.surface not in row.surfaces or not row.when.matches(ctx):
            return False
        last = self._last_start.get(row.id if key is None else key)
        return row.cooldown_s <= 0.0 or last is None or ctx.now - last >= row.cooldown_s

    def _weighted(self, pool: list[tuple[BehaviorRow, float]], u: float | None) -> BehaviorRow | None:
        total = sum(w for _, w in pool if w > 0.0)
        if total <= 0.0:
            return None
        if u is None:
            u = self.rng.random()
        target = u * total
        cum = 0.0
        last: BehaviorRow | None = None
        for row, w in pool:
            if w <= 0.0:
                continue
            cum += w
            last = row
            if target < cum:
                return row
        return last

    @staticmethod
    def _merge_pool(pool: list[tuple[BehaviorRow, float]]) -> list[tuple[BehaviorRow, float]]:
        out: dict[str, list[Any]] = {}
        for row, w in pool:
            if row.id in out:
                out[row.id][1] += w
            else:
                out[row.id] = [row, w]
        return [(row, w) for row, w in out.values()]

    def pick(
        self,
        trigger: str,
        ctx: IdleContext,
        u: float | None = None,
        chain_from: BehaviorRow | None = None,
    ) -> BehaviorRow | None:
        """Return the row to start, or None when nothing is eligible (the caller falls back)."""
        rows = self.table.behaviors
        for row in rows:
            if row.chance is not None and self._eligible(row, trigger, ctx):
                if self.rng.random() < row.chance:
                    self._chain_depth = 0
                    return self._started(row, ctx)
        pool = [
            (row, row.weight_for(ctx.mode))
            for row in rows
            if row.chance is None and self._eligible(row, trigger, ctx)
        ]
        chained = False
        if chain_from is not None and chain_from.next and self._chain_depth < MAX_CHAIN_DEPTH:
            nxt = [
                (self._by_id[ref.id], ref.weight)
                for ref in chain_from.next
                if ref.id in self._by_id and self._eligible(self._by_id[ref.id], trigger, ctx)
            ]
            if chain_from.next_mode == "replace":
                if sum(w for _, w in nxt) > 0.0:
                    pool = self._merge_pool(nxt)
                    chained = True
            else:
                pool = self._merge_pool(pool + nxt)
                chained = bool(nxt)
        picked = self._weighted(pool, u)
        if picked is None:
            return None
        self._chain_depth = self._chain_depth + 1 if chained else 0
        return self._started(picked, ctx)

    def pick_work_break(self, ctx: IdleContext) -> BehaviorRow | None:
        """Weighted pick from ``work_breaks`` (the ``work_break`` trigger: the section itself).

        Always draws from ``rng``; rows on cooldown sit out. None when nothing is eligible.
        """
        pool = [
            (row, row.weight_for(ctx.mode))
            for row in self.table.work_breaks
            if self._eligible(row, None, ctx, key=_WORK_BREAK_KEY + row.id)
        ]
        picked = self._weighted(pool, None)
        return None if picked is None else self._started(picked, ctx, key=_WORK_BREAK_KEY + picked.id)

    def _started(self, row: BehaviorRow, ctx: IdleContext, key: str | None = None) -> BehaviorRow:
        self._last_start[row.id if key is None else key] = ctx.now
        return row
