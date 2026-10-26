"""Golden FSM traces: seeded per-tick state sequences that refactors must not change.

Each trace runs a seeded controller for 120 simulated seconds at 60 Hz and run-length encodes the
state name of every tick. The fixture was generated from the FSM before the idle decisions moved
into the external behavior table; any drift in rng call order, thresholds or durations shows up
here as a changed run.

Regenerate (only for an intentional behavior change) with::

    CLAWDLET_REGEN_GOLDEN=1 python -m pytest tests/test_behavior_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody

FIXTURE = Path(__file__).parent / "fixtures" / "golden_traces.json"
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


def _trace(seed: int, mode: ActivityMode, surface: str, pointer: str) -> list[list]:
    rect, x, y = SURFACES[surface]
    body = PhysicsBody(x=x, y=y, width=128.0, height=128.0)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, activity_mode=mode, rng_seed=seed)
    win = None if rect is None else WindowSnapshot(hwnd=0x14084, rect=rect)
    runs: list[list] = []
    for i in range(int(round(DURATION_S / DT))):
        state = ctrl.tick(DT, win, _pointer(pointer, (i + 1) * DT)).state
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


def _run_case(key: str) -> list[list]:
    surface, pointer, mode, seed = key.split("/")
    return _trace(int(seed), ActivityMode(mode), surface, pointer)


def _load_fixture() -> dict[str, list[list]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


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
