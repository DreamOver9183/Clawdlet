"""Golden FSM traces: seeded per-tick state sequences that refactors must not change.

Each trace runs a seeded controller for 120 simulated seconds at 60 Hz and run-length encodes the
state name of every tick. The fixture was generated from the FSM before the idle decisions moved
into the external behavior table; any drift in rng call order, thresholds or durations shows up
here as a changed run.

Regenerate (only for an intentional behavior change) with::

    CLAWDLET_REGEN_GOLDEN=1 python -m pytest tests/test_behavior_golden.py

``golden_traces_pre_phase5.json`` is the fixture as it was before the tiered-idle / position-aware
rows joined the bundled table. With exactly those rows disabled the traces must still match it,
which shows that the rest of the Phase 5 code (new states, hooks, fidgets) leaves the FSM alone
and that every difference in ``golden_traces.json`` comes from those rows firing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.behavior_table import BehaviorTable, build_behavior_table
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.paths import default_behaviors_path
from clawdlet.physics import PhysicsBody

FIXTURE = Path(__file__).parent / "fixtures" / "golden_traces.json"
PRE_PHASE5_FIXTURE = Path(__file__).parent / "fixtures" / "golden_traces_pre_phase5.json"
# Bundled rows added with tiered idle (L2) and the position-aware actions
L2_ROWS = frozenset({"read_scroll", "polish_claw", "dig_sand"})
POSITIONAL_ROWS = frozenset({"perch_dangle", "perch_peer", "edge_scratch", "window_drum"})
DT = 1.0 / 60.0
DURATION_S = 120.0
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
SEEDS = (3, 5, 11, 42)
MODES = (ActivityMode.QUIET, ActivityMode.NORMAL, ActivityMode.HYPER)

# name -> (window rect or None, spawn x, spawn y)
SURFACES: dict[str, tuple[Rect | None, float, float]] = {
    "floor": (None, 600.0, 500.0),
    "floor_window": (Rect(250.0, 360.0, 950.0, 780.0), 1300.0, 500.0),
    "perch": (Rect(300.0, 360.0, 1200.0, 780.0), 600.0, 150.0),
    "perch_narrow": (Rect(700.0, 360.0, 860.0, 780.0), 716.0, 150.0),  # Too narrow to stroll
}

# Cursor stops the "roam" pointer visits (one every 8 s); far from nothing, near the pet
_ROAM_POINTS = ((700.0, 300.0), (520.0, 640.0), (1100.0, 250.0), (760.0, 560.0), (300.0, 680.0))


def _pointer(kind: str, t: float) -> PointerSnapshot | None:
    if kind == "none":
        return None
    if kind == "far":
        # Parked on another monitor; the user idles for 110 s at a time (yawn, sleep, wake)
        return PointerSnapshot(3500.0, 100.0, idle_s=(t + 3.0) % 110.0)
    if kind == "roam":
        x, y = _ROAM_POINTS[int(t // 8.0) % len(_ROAM_POINTS)]
        return PointerSnapshot(x, y, idle_s=t % 8.0)
    raise ValueError(kind)


POINTERS: dict[str, tuple[str, ...]] = {
    "floor": ("none", "far", "roam"),
    "floor_window": ("none", "far"),
    "perch": ("none", "far", "roam"),
    "perch_narrow": ("none", "far"),
}


def _trace(
    seed: int, mode: ActivityMode, surface: str, pointer: str, table: BehaviorTable | None = None
) -> list[list]:
    rect, x, y = SURFACES[surface]
    body = PhysicsBody(x=x, y=y, width=128.0, height=128.0)
    ctrl = ClawdletController(
        body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=seed, behavior_table=table
    )
    win = None if rect is None else WindowSnapshot(hwnd=0x14084, rect=rect)
    runs: list[list] = []
    for i in range(int(round(DURATION_S / DT))):
        state = ctrl.tick(DT, win, _pointer(pointer, (i + 1) * DT)).state
        assert ctrl.unhandled_state_resets == 0
        if runs and runs[-1][0] == state.name:
            runs[-1][1] += 1
        else:
            runs.append([state.name, 1])
    return runs


def _cases() -> list[str]:
    return [
        f"{surface}/{pointer}/{mode.value}/{seed}"
        for surface, pointers in POINTERS.items()
        for pointer in pointers
        for mode in MODES
        for seed in SEEDS
    ]


def _run_case(key: str, table: BehaviorTable | None = None) -> list[list]:
    surface, pointer, mode, seed = key.split("/")
    return _trace(int(seed), ActivityMode(mode), surface, pointer, table)


def _load_fixture(path: Path = FIXTURE) -> dict[str, list[list]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bundled_without(row_ids: frozenset[str]) -> BehaviorTable:
    raw = json.loads(default_behaviors_path().read_text(encoding="utf-8"))
    ids = {row["id"] for row in raw["behaviors"]}
    assert row_ids <= ids
    for row in raw["behaviors"]:
        if row["id"] in row_ids:
            row["enabled"] = False
    table = build_behavior_table(raw)
    assert table.warnings == ()
    return table


def _ticks(runs: list[list]) -> list[str]:
    return [name for name, count in runs for _ in range(count)]


def test_regenerate_golden_traces() -> None:
    if not os.environ.get("CLAWDLET_REGEN_GOLDEN"):
        pytest.skip("set CLAWDLET_REGEN_GOLDEN=1 to rewrite the fixture")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    data = {key: _run_case(key) for key in _cases()}
    lines = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in data.items())
    FIXTURE.write_text("{\n" + lines + "\n}\n", encoding="utf-8")


def test_fixture_covers_every_case() -> None:
    assert sorted(_load_fixture()) == sorted(_cases())


@pytest.mark.parametrize("key", _cases())
def test_golden_trace_unchanged(key: str) -> None:
    expected = _load_fixture()[key]
    actual = _run_case(key)
    assert sum(n for _, n in actual) == int(round(DURATION_S / DT))
    if actual != expected:
        for i, (a, e) in enumerate(zip(actual, expected)):
            if a != e:
                pytest.fail(f"{key}: first divergence at run {i}: got {a}, expected {e}")
        pytest.fail(f"{key}: run count differs ({len(actual)} vs {len(expected)})")


def test_golden_traces_exercise_the_idle_tail() -> None:
    """Sanity: the traces actually visit the states the behavior table now decides."""
    seen = {name for runs in _load_fixture().values() for name, _ in runs}
    assert {PetState.IDLE.name, PetState.BLOB.name, PetState.PERCH_CLAUDE.name, PetState.WANDER.name} <= seen


@pytest.mark.parametrize("key", _cases())
def test_phase5_rows_disabled_reproduce_the_pre_phase5_traces(key: str) -> None:
    """New states, trigger hooks and fidgets change nothing until a Phase 5 row actually fires."""
    table = _bundled_without(L2_ROWS | POSITIONAL_ROWS)
    assert _run_case(key, table) == _load_fixture(PRE_PHASE5_FIXTURE)[key]


def test_l2_rows_change_only_far_pointer_traces_after_45_s_idle() -> None:
    """L2 rows join the weighted pool (reusing the legacy roll) only at idle level 2: with the
    positional rows off, a trace differs from the pre-Phase-5 one only once the user has been idle
    for ``idle_level2_after_s``, which among the fixture's pointers only the parked "far" one is."""
    table = _bundled_without(POSITIONAL_ROWS)
    level2_s = table.settings.idle_level2_after_s
    old = _load_fixture(PRE_PHASE5_FIXTURE)
    changed = []
    for key in _cases():
        if key.split("/")[1] == "none":
            continue  # No pointer sample: always idle level 1 (and pinned by the main fixture)
        new, before = _ticks(_run_case(key, table)), _ticks(old[key])
        if new == before:
            continue
        first = next(i for i, (a, b) in enumerate(zip(new, before)) if a != b)
        pointer = _pointer(key.split("/")[1], (first + 1) * DT)
        assert pointer is not None and pointer.idle_s >= level2_s, (key, first)
        changed.append(key)
    assert changed and all(key.split("/")[1] == "far" for key in changed)
