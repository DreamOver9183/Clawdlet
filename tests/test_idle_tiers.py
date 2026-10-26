"""Milestone 6 phase 5: tiered idle (L1 fidgets, L2 idle actions) and position-aware actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.behavior_table import (
    _STATE_SURFACES,
    SELECTABLE_STATES,
    WORK_BREAK_STATES,
    BehaviorTable,
    build_behavior_table,
    load_bundled_behavior_table,
)
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.paths import behaviors_schema_path, default_behaviors_path
from clawdlet.physics import PhysicsBody

REPO = Path(__file__).resolve().parents[1]
DT = 1.0 / 60.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
FLOOR_WIN = WindowSnapshot(hwnd=0x14084, rect=Rect(250.0, 360.0, 950.0, 780.0))
L2_STATES = frozenset({PetState.READ_SCROLL, PetState.POLISH_CLAW, PetState.DIG_SAND})
PHASE5_ROWS = {
    "read_scroll": "READ_SCROLL",
    "polish_claw": "POLISH_CLAW",
    "dig_sand": "DIG_SAND",
    "perch_dangle": "PERCH_DANGLE",
    "perch_peer": "PERCH_PEER",
    "edge_scratch": "WALL_SCRATCH",
    "window_drum": "WALL_SCRATCH",
}


def _far(idle_s: float) -> PointerSnapshot:
    """Cursor parked on another monitor (no gaze, chase or pounce), idle for ``idle_s``."""
    return PointerSnapshot(3500.0, 100.0, idle_s=idle_s)


def _bundled_raw() -> dict[str, Any]:
    return json.loads(default_behaviors_path().read_text(encoding="utf-8"))


def _patched(rows: dict[str, dict[str, Any]] | None = None, fidgets: list[dict] | None = None) -> BehaviorTable:
    """The bundled table with some rows patched (by id) and optionally other fidgets."""
    raw = _bundled_raw()
    for row in raw["behaviors"]:
        row.update((rows or {}).get(row["id"], {}))
    if fidgets is not None:
        raw["fidgets"] = fidgets
    table = build_behavior_table(raw)
    assert table.warnings == ()
    return table


def _floor(
    table: BehaviorTable | None = None,
    x: float = 600.0,
    seed: int = 3,
    mode: ActivityMode = ActivityMode.NORMAL,
) -> ClawdletController:
    body = PhysicsBody(x=x, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=seed, behavior_table=table)
    ctrl.state = PetState.IDLE
    return ctrl


def _perched(table: BehaviorTable | None = None, offset: float = 400.0, seed: int = 3) -> ClawdletController:
    body = PhysicsBody(x=WIN_RECT.left + offset, y=WIN_RECT.top - 128.0, width=128.0, height=128.0, grounded=True)
    body.on_platform_id = "claude_window"
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, rng_seed=seed, behavior_table=table)
    ctrl._perched_hwnd = SNAP.hwnd
    ctrl._perch_offset_x = offset
    ctrl._last_win = SNAP
    ctrl.state = PetState.PERCH_CLAUDE
    return ctrl


def _run(ctrl: ClawdletController, seconds: float, win: WindowSnapshot | None, pointer: PointerSnapshot | None):
    return [ctrl.tick(DT, win, pointer) for _ in range(int(round(seconds / DT)))]


# --- L2: idle actions after the user has been away ----------------------------------------------


@pytest.mark.parametrize("seed", (3, 5, 11, 42))
def test_level1_idle_never_starts_an_l2_action(seed: int) -> None:
    for ctrl, win in ((_floor(seed=seed), None), (_perched(seed=seed), SNAP)):
        states = {frame.state for frame in _run(ctrl, 120.0, win, _far(10.0))}
        assert not states & L2_STATES


def test_level2_idle_starts_l2_actions_and_level3_still_yawns_from_them() -> None:
    ctrl = _floor(seed=5)
    seen: list[PetState] = []
    for _ in range(int(120.0 / DT)):
        state = ctrl.tick(DT, None, _far(50.0)).state
        seen.append(state)
        if state in L2_STATES:
            break
    assert seen[-1] in L2_STATES
    # Sleepy (idle level 3): the L2 action is a resting state, so Clawd yawns straight out of it
    assert ctrl.tick(DT, None, _far(95.0)).state == PetState.YAWN


def test_read_scroll_chains_into_polishing_a_claw() -> None:
    table = load_bundled_behavior_table()
    scroll = table.row("read_scroll")
    assert [(ref.id, scroll.next_mode) for ref in scroll.next] == [("polish_claw", "add")]
    picks = {}
    for chain_from in (None, scroll):
        ctrl = _floor(table)
        ctrl._pointer = _far(60.0)
        # Normal floor pool at L2: blob .23, idle .15, scroll .15, polish .12 (+.25 chained), dig .10
        ctrl._start_idle_row(None, perched=False, roll=0.7, lo=0.0, chain_from=chain_from)
        picks[chain_from is not None] = ctrl.state
    assert picks == {False: PetState.READ_SCROLL, True: PetState.POLISH_CLAW}


# --- L1: fidgets (cosmetic) ---------------------------------------------------------------------


def test_fidgets_glance_and_blink_without_touching_the_fsm_rng() -> None:
    ctrl = _floor()
    ctrl._decision_duration = 999.0
    before = ctrl._rng.getstate()
    frames = _run(ctrl, 40.0, None, None)
    gaze = [f for f in frames if f.animation_name == "gaze"]
    assert gaze, "no fidget in 40 s"
    # No Claude window: only look_left_right (3, 4, 5) and blink (9, 4)
    assert {f.frame_index for f in gaze} <= {3, 4, 5, 9}
    assert all(f.flip_x is False and f.state == PetState.IDLE for f in gaze)
    assert ctrl._rng.getstate() == before


def test_fidgets_do_not_change_the_state_sequence() -> None:
    plain = _floor(_patched(fidgets=[]), seed=11)
    fidgety = _floor(seed=11)
    a = [f.state for f in _run(plain, 60.0, FLOOR_WIN, None)]
    b_frames = _run(fidgety, 60.0, FLOOR_WIN, None)
    assert a == [f.state for f in b_frames]
    assert any(f.animation_name == "gaze" for f in b_frames)
    assert plain._rng.getstate() == fidgety._rng.getstate()


def test_glance_claude_looks_toward_the_window_or_down_when_perched() -> None:
    only_glance = _patched(fidgets=[{"id": "glance_claude", "target": "claude_window", "hold_s": 1.0,
                                     "when": {"claude_visible": True}}])
    # On the floor right of the window: look left (index 3)
    ctrl = _floor(only_glance, x=1300.0)
    ctrl._decision_duration = 999.0
    indices = {f.frame_index for f in _run(ctrl, 12.0, FLOOR_WIN, None) if f.animation_name == "gaze"}
    assert indices == {3}
    # Without a visible window the row is not eligible
    ctrl = _floor(only_glance)
    ctrl._decision_duration = 999.0
    assert not any(f.animation_name == "gaze" for f in _run(ctrl, 12.0, None, None))
    # Perched on the window: look down at it (index 7)
    ctrl = _perched(only_glance)
    ctrl._decision_duration = 999.0
    indices = {f.frame_index for f in _run(ctrl, 12.0, SNAP, None) if f.animation_name == "gaze"}
    assert indices == {7}


def test_fidgets_only_play_over_idle_and_perch() -> None:
    ctrl = _floor()
    ctrl.state = PetState.BLOB
    ctrl._decision_duration = 999.0
    assert not any(f.animation_name == "gaze" for f in _run(ctrl, 20.0, None, None))


# --- Position-aware actions ---------------------------------------------------------------------


@pytest.mark.parametrize(("offset", "end", "facing"), ((10.0, 0.0, -1), (760.0, 772.0, 1)))
def test_perch_dangle_snaps_to_the_bar_end_and_hangs_over_it(offset: float, end: float, facing: int) -> None:
    ctrl = _perched(_patched({"perch_dangle": {"chance": 1.0}}), offset=offset)
    ctrl.state = PetState.WANDER  # Just strolled: the whole roll goes to the table
    ctrl._transition_from_perch(SNAP)
    assert ctrl.state == PetState.PERCH_DANGLE
    assert ctrl._perch_offset_x == end and ctrl.facing == facing
    frame = ctrl.tick(DT, SNAP, None)
    assert frame.animation_name == "perch_dangle"
    assert ctrl.body.y == WIN_RECT.top - 128.0  # Physics body stays on the title bar
    assert frame.y == round(ctrl.body.y + 8 * ctrl.body.height / 64.0)
    assert frame.x == round(WIN_RECT.left + end)
    assert frame.flip_x is (facing < 0)  # The strip faces right: mirrored at the left end


def test_perch_dangle_needs_the_perch_edge() -> None:
    ctrl = _perched(_patched({"perch_dangle": {"chance": 1.0}}), offset=400.0)
    ctrl.state = PetState.WANDER
    ctrl._transition_from_perch(SNAP)
    assert ctrl.state != PetState.PERCH_DANGLE
    # In a natural run, every dangle sits at one end of the bar
    ctrl = _perched(_patched({"perch_dangle": {"chance": 1.0, "cooldown_s": 0.0}}), seed=2)
    dangles = 0
    for _ in range(int(90.0 / DT)):
        if ctrl.tick(DT, SNAP, None).state == PetState.PERCH_DANGLE:
            dangles += 1
            assert ctrl._perch_offset_x in (0.0, WIN_RECT.width - 128.0)
    assert dangles


def test_perch_peer_requires_the_cursor_in_the_window() -> None:
    table = _patched({"perch_peer": {"chance": 1.0}})
    above = _perched(table)
    above._pointer = PointerSnapshot(764.0, 300.0, idle_s=0.5)  # Over the title bar, not in the window
    above.state = PetState.WANDER
    above._transition_from_perch(SNAP)
    assert above.state != PetState.PERCH_PEER

    inside = _perched(table)
    inside._pointer = PointerSnapshot(764.0, 560.0, idle_s=0.5)  # Straight below: no chase
    inside.state = PetState.WANDER
    inside._transition_from_perch(SNAP)
    assert inside.state == PetState.PERCH_PEER
    frame = inside.tick(DT, SNAP, PointerSnapshot(764.0, 560.0, idle_s=0.5))
    assert frame.animation_name == "perch_peer"
    assert frame.y == round(inside.body.y + 10 * inside.body.height / 64.0)


def _at_right_wall(table: BehaviorTable) -> ClawdletController:
    ctrl = _floor(table, x=WORK_AREA.right - 128.0, mode=ActivityMode.QUIET)  # Quiet never climbs walls
    ctrl.state = PetState.WANDER
    ctrl.facing = 1
    ctrl._decision_duration = 999.0
    return ctrl


def test_wall_bump_scratches_facing_the_wall_then_turns_around() -> None:
    plain = _at_right_wall(_patched({"edge_scratch": {"enabled": False}}))
    plain.tick(DT, None, None)
    assert plain.state == PetState.WANDER and plain.facing == -1

    ctrl = _at_right_wall(_patched({"edge_scratch": {"chance": 1.0}}))
    frame = ctrl.tick(DT, None, None)
    assert frame.state == PetState.WALL_SCRATCH
    assert ctrl.facing == 1 and frame.flip_x is False  # The strip faces right, at the right wall
    assert ctrl._rng.getstate() == plain._rng.getstate()  # The climb roll was drawn, nothing more
    duration = ctrl._decision_duration
    assert 1.5 <= duration <= 3.0  # scale_by_rest is off: no Quiet stretching
    states = [f.state for f in _run(ctrl, duration + 0.1, None, None)]
    assert states[-1] != PetState.WALL_SCRATCH
    assert ctrl.facing == -1  # Turned away from the wall (rest or a stroll back into the screen)


def test_wall_bump_at_the_left_wall_faces_left() -> None:
    ctrl = _floor(_patched({"edge_scratch": {"chance": 1.0}}), x=0.0, mode=ActivityMode.QUIET)
    ctrl.state = PetState.WANDER
    ctrl.facing = -1
    ctrl._decision_duration = 999.0
    frame = ctrl.tick(DT, None, None)
    assert frame.state == PetState.WALL_SCRATCH and frame.flip_x is True


def test_window_side_arrival_drums_then_climbs() -> None:
    ctrl = _floor(_patched({"window_drum": {"chance": 1.0}}), x=FLOOR_WIN.rect.left - 3.0)
    ctrl.state = PetState.WANDER
    ctrl._seeking_claude = True
    ctrl._decision_duration = 999.0
    frame = ctrl.tick(DT, FLOOR_WIN, None)
    assert frame.state == PetState.WALL_SCRATCH
    assert ctrl.body.x == FLOOR_WIN.rect.left and ctrl.facing == -1
    assert ctrl._climb_after_scratch
    states = [f.state for f in _run(ctrl, ctrl._decision_duration + 0.1, FLOOR_WIN, None)]
    after = [s for s in states if s != PetState.WALL_SCRATCH]
    assert after and after[0] == PetState.CLIMB
    assert ctrl._climb_surface == "claude_window"


def test_interrupted_window_drum_does_not_climb_later() -> None:
    ctrl = _floor(_patched({"window_drum": {"chance": 1.0}}), x=FLOOR_WIN.rect.left - 3.0)
    ctrl.state = PetState.WANDER
    ctrl._seeking_claude = True
    ctrl._decision_duration = 999.0
    ctrl.tick(DT, FLOOR_WIN, None)
    assert ctrl.state == PetState.WALL_SCRATCH
    assert ctrl.poke()
    ctrl.tick(DT, FLOOR_WIN, None)
    assert not ctrl._climb_after_scratch


def test_no_window_drum_climbs_straight_away() -> None:
    ctrl = _floor(_patched({"window_drum": {"enabled": False}}), x=FLOOR_WIN.rect.left - 3.0)
    ctrl.state = PetState.WANDER
    ctrl._seeking_claude = True
    ctrl._decision_duration = 999.0
    assert ctrl.tick(DT, FLOOR_WIN, None).state == PetState.CLIMB


# --- Safety net: no selectable state falls through tick() --------------------------------------


def _surface_cases() -> list[tuple[str, str]]:
    return [
        (state, surface)
        for state in sorted(SELECTABLE_STATES | WORK_BREAK_STATES)
        for surface in ("floor", "perch")
        if surface in _STATE_SURFACES[state]
    ]


@pytest.mark.parametrize(("state", "surface"), _surface_cases())
def test_selectable_states_never_hit_the_tick_fallback(state: str, surface: str) -> None:
    if surface == "floor":
        ctrl, win = _floor(), None
    else:
        ctrl, win = _perched(), SNAP
    ctrl.state = PetState[state]
    ctrl._decision_duration = 1.0  # Let the timer run out inside the window
    for _ in range(int(3.0 / DT)):
        ctrl.tick(DT, win, None)
    assert ctrl.unhandled_state_resets == 0


# --- Table ---------------------------------------------------------------------------------------


def test_bundled_phase5_rows_validate_and_have_sprites() -> None:
    table = load_bundled_behavior_table()
    assert table.warnings == ()
    manifest = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))
    for row_id, state in PHASE5_ROWS.items():
        row = table.row(row_id)
        assert row is not None and row.state == state, row_id
        assert PetState[state].animation_name in manifest["animations"], row_id
    assert table.row("edge_scratch").on == "wall_bump"
    assert table.row("window_drum").on == "window_side"
    assert {r.id for r in table.fidgets} == {"look_left_right", "blink", "glance_claude"}
    for row in table.fidgets:
        assert all(0 <= index < manifest["animations"]["gaze"]["frames"] for index, _ in row.poses)


def test_l2_rows_need_idle_level_2_and_dig_sand_stays_on_the_floor() -> None:
    table = load_bundled_behavior_table()
    for row_id in ("read_scroll", "polish_claw", "dig_sand"):
        assert table.row(row_id).when.min_idle_level == 2
    assert table.row("dig_sand").surfaces == ("floor",)
    assert table.row("perch_dangle").surfaces == ("perch",)
    assert table.row("edge_scratch").surfaces == ("floor",)


def test_fidget_row_validation() -> None:
    table = build_behavior_table({
        "version": 1,
        "fidgets": [
            {"id": "ok", "poses": [[3, 0.5], [9, 0.1]]},
            {"id": "both", "poses": [[3, 0.5]], "target": "claude_window"},
            {"id": "neither", "weight": 1},
            {"id": "bad_index", "poses": [[10, 0.5]]},
            {"id": "bad_hold", "poses": [[3, 0.0]]},
            {"id": "bad_target", "target": "cursor"},
            {"id": "look", "target": "claude_window", "hold_s": 1.2},
        ],
    })
    assert [r.id for r in table.fidgets] == ["ok", "look"]
    assert table.fidgets[1].hold_s == (1.2, 1.2)
    assert len(table.warnings) == 5


def test_schema_lists_the_selectable_states() -> None:
    schema = json.loads(behaviors_schema_path().read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert set(defs["stateName"]["enum"]) == set(PetState.__members__)
    assert set(defs["behavior"]["properties"]["state"]["enum"]) == set(SELECTABLE_STATES)
    assert set(defs["workBreak"]["properties"]["state"]["enum"]) == set(WORK_BREAK_STATES)
    assert set(defs["fidget"]["properties"]) >= {"poses", "target", "hold_s", "when"}
