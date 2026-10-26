"""Milestone 6 phase 3: the external behavior table (loader, merge, validator, selector, wiring)."""

from __future__ import annotations

import json
from pathlib import Path
import random
import sys

import pytest

from clawdlet import paths
from clawdlet.agent_events import default_token_path
from clawdlet.app import dispatch_tray_action, main
from clawdlet.behavior import ClawdletController, PetState, WindowSnapshot
from clawdlet.behavior_table import (
    LEGACY_TABLE,
    MAX_CHAIN_DEPTH,
    SELECTABLE_STATES,
    WORK_BREAK_STATES,
    BehaviorSelector,
    BehaviorTable,
    IdleContext,
    build_behavior_table,
    load_behavior_table,
    load_bundled_behavior_table,
)
from clawdlet.config import ActivityMode
from clawdlet.geometry import Rect
from clawdlet.input_state import PointerSnapshot
from clawdlet.physics import PhysicsBody
from clawdlet.sprites import default_sprites_dir
from clawdlet.tools.build_exe import pyinstaller_command, validate_default_behaviors
from clawdlet.tray import CMD_OPEN_CONFIG_DIR, CMD_RELOAD_BEHAVIORS, TrayMenuState
from clawdlet.work_session import WorkConfig

REPO = Path(__file__).resolve().parents[1]
WORK_AREA = Rect(0.0, 0.0, 1536.0, 816.0)
WIN_RECT = Rect(300.0, 360.0, 1200.0, 780.0)
SNAP = WindowSnapshot(hwnd=0x14084, rect=WIN_RECT)
FLOOR = IdleContext(surface="floor", spots=frozenset({"open"}), mode="normal", prev_state="IDLE")


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never read or write the developer's real %APPDATA%."""
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def _user(tmp_path: Path, **doc: object) -> BehaviorTable:
    doc.setdefault("version", 1)
    return load_behavior_table(_write(tmp_path / "behaviors.json", doc))


def _floor_ctrl(table: BehaviorTable | None = None, seed: int = 3) -> ClawdletController:
    body = PhysicsBody(x=600.0, y=WORK_AREA.bottom - 128.0, width=128.0, height=128.0, grounded=True)
    ctrl = ClawdletController(body=body, work_area=WORK_AREA, rng_seed=seed, behavior_table=table)
    ctrl.state = PetState.IDLE
    return ctrl


def _row(**fields: object) -> dict:
    row = {"id": "x", "state": "IDLE", "weight": 1, "duration_s": [1, 2]}
    row.update(fields)
    return row


def _table(*rows: dict) -> BehaviorTable:
    table = build_behavior_table({"version": 1, "behaviors": list(rows)})
    assert table.warnings == ()
    return table


# --- Bundled table ------------------------------------------------------------------------------


def test_default_table_validates_without_warnings_and_matches_legacy() -> None:
    table = load_bundled_behavior_table()
    assert table.warnings == ()
    assert table.origin == str(paths.default_behaviors_path())
    assert table.behaviors == LEGACY_TABLE.behaviors
    assert table.work_breaks == LEGACY_TABLE.work_breaks
    assert table.settings == LEGACY_TABLE.settings
    assert validate_default_behaviors(REPO) == []


def test_default_rows_are_selectable_and_have_sprites() -> None:
    manifest = json.loads((REPO / "assets" / "sprites" / "manifest.json").read_text(encoding="utf-8"))
    assert SELECTABLE_STATES | WORK_BREAK_STATES <= set(PetState.__members__)
    table = load_bundled_behavior_table()
    for row in table.behaviors:
        assert row.state in SELECTABLE_STATES
        assert PetState[row.state].animation_name in manifest["animations"], row.id
    for row in table.work_breaks:
        assert row.state in WORK_BREAK_STATES
        assert PetState[row.state].animation_name in manifest["animations"], row.id


def test_work_settings_are_the_single_source_of_work_config() -> None:
    table = load_bundled_behavior_table()
    assert table.settings.work_config() == WorkConfig()
    ctrl = _floor_ctrl()
    assert ctrl._work.cfg == WorkConfig()
    raw = json.loads(paths.default_behaviors_path().read_text(encoding="utf-8"))
    raw["settings"]["work"]["break_s"] = [1.0, 1.5]
    ctrl.set_behavior_table(build_behavior_table(raw))
    assert ctrl._work.cfg.break_s == (1.0, 1.5)


def test_bundled_file_missing_or_broken_falls_back_to_legacy(tmp_path: Path) -> None:
    missing = load_bundled_behavior_table(tmp_path / "nope.json")
    assert missing.origin == "legacy"
    assert missing.behaviors == LEGACY_TABLE.behaviors
    assert missing.warnings
    broken = _write(tmp_path / "broken.json", {"version": 1, "behaviors": [_row(state="NOPE")]})
    table = load_bundled_behavior_table(broken)
    assert table.origin == "legacy"
    assert table.behaviors == LEGACY_TABLE.behaviors


# --- User overrides -----------------------------------------------------------------------------


def test_missing_user_file_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    table = load_behavior_table()
    assert table.warnings == ()
    assert table.behaviors == LEGACY_TABLE.behaviors
    assert capsys.readouterr().err == ""


def test_user_file_in_appdata_is_merged(_isolated_appdata: Path) -> None:
    _isolated_appdata.mkdir(parents=True)
    path = paths.user_behaviors_path()
    assert path == _isolated_appdata / "Clawdlet" / "behaviors.json"
    path.parent.mkdir()
    _write(path, {"version": 1, "behaviors": [{"id": "floor_idle", "weight": 0.5}]})
    table = load_behavior_table()
    assert table.row("floor_idle").weight == 0.5
    assert str(path) in table.origin


def test_patch_by_id_keeps_other_fields(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[{"id": "floor_blob", "weight": 2, "cooldown_s": 5}])
    row = table.row("floor_blob")
    assert row.weight == 2.0 and row.cooldown_s == 5.0
    assert row.duration_s == (2.5, 4.5)
    assert row.when.not_prev_state == ("BLOB",)
    assert [r.id for r in table.behaviors] == [r.id for r in LEGACY_TABLE.behaviors]
    assert table.warnings == ()


def test_disabling_a_row_removes_it_from_selection(tmp_path: Path) -> None:
    table = _user(tmp_path, extends="default", behaviors=[{"id": "floor_blob", "enabled": False}])
    selector = BehaviorSelector(table, random.Random(1))
    for u in (0.0, 0.3, 0.6, 0.99):
        assert selector.pick("rest", FLOOR, u).id == "floor_idle"


def test_added_row_is_appended_and_selectable(tmp_path: Path) -> None:
    nap = _row(id="floor_nap", state="BLOB", weight=100, when={"surface": ["floor"]})
    table = _user(tmp_path, behaviors=[nap])
    assert table.behaviors[-1].id == "floor_nap"
    assert len(table.behaviors) == len(LEGACY_TABLE.behaviors) + 1
    assert BehaviorSelector(table).pick("rest", FLOOR, 0.5).id == "floor_nap"


def test_extends_none_replaces_the_table(tmp_path: Path) -> None:
    table = _user(tmp_path, extends="none", behaviors=[_row(id="only")])
    assert [r.id for r in table.behaviors] == ["only"]
    assert table.work_breaks == ()
    assert table.settings == LEGACY_TABLE.settings  # Missing settings fall back to the defaults


def test_settings_are_deep_merged(tmp_path: Path) -> None:
    table = _user(tmp_path, settings={"work": {"segment_s": {"hyper": [5, 6]}}, "mail_away_s": 12})
    assert table.settings.mail_away_s == 12.0
    assert table.settings.work.segment_s["hyper"] == (5.0, 6.0)
    assert table.settings.work.segment_s["normal"] == (14.0, 28.0)
    assert table.settings.work.break_s == (2.5, 4.5)
    assert table.settings.work_config().segment_s[ActivityMode.HYPER] == (5.0, 6.0)


def test_invalid_json_falls_back_to_defaults_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    table = load_behavior_table(_write(tmp_path / "behaviors.json", "{ not json"))
    assert table.behaviors == LEGACY_TABLE.behaviors
    err = capsys.readouterr().err
    assert "[Clawdlet] behaviors.json: invalid JSON" in err
    assert "using defaults" in err


def test_wrong_version_falls_back_to_defaults(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    table = _user(tmp_path, version=2, extends="none", behaviors=[])
    assert table.behaviors == LEGACY_TABLE.behaviors
    assert "unsupported version 2" in capsys.readouterr().err


def test_unknown_state_drops_only_that_row(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[_row(id="bad", state="DANCE"), _row(id="good")])
    ids = [r.id for r in table.behaviors]
    assert "bad" not in ids and "good" in ids
    assert any("behaviors[bad] dropped" in w and "DANCE" in w for w in table.warnings)


def test_engine_states_are_not_selectable(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[_row(id="drag", state="DRAGGED")])
    assert table.row("drag") is None
    assert any("behaviors[drag] dropped" in w for w in table.warnings)


def test_unknown_when_key_warns_but_keeps_the_row(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[_row(id="odd", when={"surface": ["floor"], "moon_phase": "full"})])
    assert table.row("odd") is not None
    assert any("moon_phase" in w for w in table.warnings)


def test_unknown_top_level_key_warns(tmp_path: Path) -> None:
    table = _user(tmp_path, colour="orange")
    assert any("colour" in w for w in table.warnings)
    assert table.behaviors == LEGACY_TABLE.behaviors


def test_negative_weight_drops_row_and_keeps_others(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[_row(id="neg", weight=-1), _row(id="pos")])
    assert table.row("neg") is None and table.row("pos") is not None
    assert any("behaviors[neg] dropped" in w and "weight" in w for w in table.warnings)


def test_unknown_next_id_is_dropped_with_a_warning(tmp_path: Path) -> None:
    table = _user(tmp_path, behaviors=[_row(id="chain", next=[{"id": "floor_idle"}, {"id": "ghost"}])])
    assert [ref.id for ref in table.row("chain").next] == ["floor_idle"]
    assert any("ghost" in w for w in table.warnings)


def test_other_row_validation_errors(tmp_path: Path) -> None:
    table = _user(
        tmp_path,
        extends="none",
        behaviors=[
            _row(id="Bad-Id"),
            _row(id="dur", duration_s=[0.1, 2]),
            _row(id="chance", chance=1.5),
            _row(id="surface", state="IDLE", when={"surface": ["perch"]}),  # IDLE is floor-only
            _row(id="dup"),
            _row(id="dup"),
            _row(id="nodur", duration_s=None),
        ],
    )
    assert [r.id for r in table.behaviors] == ["dup"]
    assert len(table.warnings) == 6


# --- Selector -----------------------------------------------------------------------------------


def test_default_weights_reproduce_the_legacy_floor_thresholds() -> None:
    selector = BehaviorSelector(load_bundled_behavior_table())
    for mode, wander_p in (("quiet", 0.22), ("normal", 0.62), ("hyper", 0.88)):
        ctx = IdleContext(surface="floor", mode=mode, prev_state="IDLE")
        for roll in (wander_p, 0.70, 0.8499, 0.8501, 0.95, 0.9999):
            if roll < wander_p:
                continue
            u = (roll - wander_p) / (1.0 - wander_p)
            expected = "BLOB" if roll < 0.85 else "IDLE"
            assert selector.pick("rest", ctx, u).state == expected, (mode, roll)
        blob_ctx = IdleContext(surface="floor", mode=mode, prev_state="BLOB")
        assert selector.pick("rest", blob_ctx, 0.0).state == "IDLE"


def test_default_weights_reproduce_the_legacy_perch_thresholds() -> None:
    selector = BehaviorSelector(load_bundled_behavior_table())
    for mode, wander_p in (("quiet", 0.22), ("normal", 0.62), ("hyper", 0.88)):
        cut = min(0.75, wander_p * 0.85)
        ctx = IdleContext(surface="perch", mode=mode, prev_state="PERCH_CLAUDE")
        for roll in (cut, 0.7199, 0.7201, 0.9):
            if roll < cut:
                continue
            expected = "BLOB" if roll < 0.72 else "PERCH_CLAUDE"
            assert selector.pick("rest", ctx, (roll - cut) / (1.0 - cut)).state == expected, (mode, roll)
        after_stroll = IdleContext(surface="perch", mode=mode, prev_state="WANDER")
        assert selector.pick("rest", after_stroll, 0.7199).id == "perch_blob_after_stroll"
        assert selector.pick("rest", after_stroll, 0.7201).id == "perch_look_down_after_stroll"


def test_per_mode_weights() -> None:
    table = _table(
        _row(id="a", weight={"quiet": 1, "normal": 0}),  # hyper missing -> 0
        _row(id="b", weight={"quiet": 0, "normal": 1, "hyper": 1}),
    )
    selector = BehaviorSelector(table)
    for u in (0.0, 0.5, 0.99):
        assert selector.pick("rest", IdleContext(surface="floor", mode="quiet"), u).id == "a"
        assert selector.pick("rest", IdleContext(surface="floor", mode="normal"), u).id == "b"
        assert selector.pick("rest", IdleContext(surface="floor", mode="hyper"), u).id == "b"


def test_conditions_filter_rows() -> None:
    table = _table(
        _row(id="sleepy", state="BLOB", when={"min_idle_level": 2, "user_idle_s": [40, None]}),
        _row(id="edge", state="BLOB", when={"spot": ["screen_edge"]}),
        _row(id="cursor", state="PERCH_CLAUDE", when={"cursor_in_window": True, "claude_foreground": True}),
    )
    selector = BehaviorSelector(table)
    assert selector.pick("rest", IdleContext(surface="floor", idle_level=1, user_idle_s=50.0), 0.0) is None
    sleepy = IdleContext(surface="floor", idle_level=2, user_idle_s=50.0)
    assert selector.pick("rest", sleepy, 0.0).id == "sleepy"
    edge = IdleContext(surface="floor", spots=frozenset({"screen_edge"}))
    assert selector.pick("rest", edge, 0.0).id == "edge"
    perch = IdleContext(surface="perch", cursor_in_window=True, claude_foreground=True)
    assert selector.pick("rest", perch, 0.0).id == "cursor"
    assert selector.pick("wall_bump", perch, 0.0) is None  # Rows default to on: rest


def test_cooldown_uses_the_context_clock() -> None:
    table = _table(_row(id="rare", weight=100, cooldown_s=5), _row(id="common", weight=1))
    selector = BehaviorSelector(table)
    at = lambda now: IdleContext(surface="floor", now=now)  # noqa: E731
    assert selector.pick("rest", at(0.0), 0.0).id == "rare"
    assert selector.pick("rest", at(1.0), 0.0).id == "common"
    assert selector.pick("rest", at(4.9), 0.0).id == "common"
    assert selector.pick("rest", at(5.0), 0.0).id == "rare"


def test_chance_rows_roll_only_the_selector_rng() -> None:
    table = _table(_row(id="lucky", state="BLOB", chance=1.0), _row(id="plain"))
    ctrl = _floor_ctrl(table)
    before = ctrl._rng.getstate()
    behavior_before = ctrl._behavior_rng.getstate()
    row = ctrl._selector.pick("rest", ctrl._idle_context(None, perched=False), 0.9)
    assert row.id == "lucky"
    assert ctrl._rng.getstate() == before
    assert ctrl._behavior_rng.getstate() != behavior_before


def test_no_chance_rows_draw_nothing() -> None:
    ctrl = _floor_ctrl()
    before = ctrl._behavior_rng.getstate()
    ctrl._selector.pick("rest", ctrl._idle_context(None, perched=False), 0.5)
    assert ctrl._behavior_rng.getstate() == before


def test_chance_row_rest_decision_keeps_fsm_rng_call_count() -> None:
    """A chance row changes the pick, not how many times ``_rng`` is drawn per decision."""
    plain = _floor_ctrl(seed=5)
    lucky = _floor_ctrl(_table(_row(id="lucky", state="BLOB", chance=1.0), _row(id="plain")), seed=5)
    for ctrl in (plain, lucky):
        ctrl._transition_from_floor_rest(None)
    assert plain._rng.getstate() == lucky._rng.getstate()


def test_rest_decision_reaches_the_table_through_tick() -> None:
    table = _table(_row(id="lucky", state="BLOB", chance=1.0, duration_s=[7, 7], scale_by_rest=False))
    ctrl = _floor_ctrl(table, seed=11)
    ctrl.set_activity_mode(ActivityMode.QUIET)
    seen = set()
    for _ in range(60 * 60):
        frame = ctrl.tick(1.0 / 60.0)
        seen.add(frame.state)
        if frame.state == PetState.BLOB:
            assert ctrl._decision_duration == 7.0
    assert PetState.BLOB in seen


def test_zero_total_weight_falls_back_to_legacy_rest() -> None:
    table = _table(_row(id="zero", weight=0))
    assert BehaviorSelector(table).pick("rest", FLOOR, 0.5) is None
    ctrl = _floor_ctrl(table)
    ctrl.state = PetState.BLOB
    ctrl._start_idle_row(None, perched=False, roll=0.9, lo=0.62, chain_from=None)
    assert ctrl.state == PetState.IDLE
    assert 2.2 <= ctrl._decision_duration <= 4.0


def test_disabled_work_break_falls_back_to_glance() -> None:
    raw = json.loads(paths.default_behaviors_path().read_text(encoding="utf-8"))
    for row in raw["work_breaks"]:
        row["enabled"] = False
    ctrl = _floor_ctrl(build_behavior_table(raw))
    before = ctrl._rng.getstate()
    assert ctrl._pick_work_break() == PetState.WORK_GLANCE
    assert ctrl._rng.getstate() == before


def test_work_breaks_are_weighted_with_cooldowns_off_the_fsm_rng() -> None:
    ctrl = _floor_ctrl()
    before = ctrl._rng.getstate()
    picked = [ctrl._pick_work_break() for _ in range(40)]  # All at the same clock time
    assert ctrl._rng.getstate() == before
    # stretch (60 s) and sip (90 s) cool down after one go; think / glance repeat
    assert picked.count(PetState.WORK_STRETCH) == 1 and picked.count(PetState.WORK_SIP) == 1
    assert {PetState.WORK_THINK, PetState.WORK_GLANCE} <= set(picked)
    ctrl._clock += 91.0
    later = [ctrl._pick_work_break() for _ in range(40)]
    assert PetState.WORK_STRETCH in later and PetState.WORK_SIP in later


def test_chain_add_adds_next_rows_to_the_pool() -> None:
    table = _table(
        _row(id="a", weight=1, next=[{"id": "b", "weight": 3}]),
        _row(id="b", weight=0),
    )
    selector = BehaviorSelector(table)
    a = table.row("a")
    assert selector.pick("rest", FLOOR, 0.9).id == "a"  # b weighs 0 without the chain
    assert selector.pick("rest", FLOOR, 0.9, chain_from=a).id == "b"  # a:1 + b:3
    assert selector.pick("rest", FLOOR, 0.1, chain_from=a).id == "a"


def test_chain_replace_restricts_the_pool() -> None:
    table = _table(
        _row(id="a", weight=100, next=[{"id": "b"}], next_mode="replace"),
        _row(id="b", weight=0),
        _row(id="c", weight=100, next=[{"id": "gone_zero", "weight": 0}], next_mode="replace"),
        _row(id="gone_zero", weight=0),
    )
    selector = BehaviorSelector(table)
    for u in (0.0, 0.5, 0.99):
        assert selector.pick("rest", FLOOR, u, chain_from=table.row("a")).id == "b"
    # A replace chain with nothing to offer falls back to the plain pool
    assert selector.pick("rest", FLOOR, 0.0, chain_from=table.row("c")).id == "a"


def test_chain_depth_is_capped() -> None:
    table = _table(
        _row(id="loop", weight=0, next=[{"id": "loop"}], next_mode="replace"),
        _row(id="base", weight=1),
    )
    selector = BehaviorSelector(table)
    row = selector.pick("rest", FLOOR, 0.5)
    assert row.id == "base"
    loop = table.row("loop")
    picks = [selector.pick("rest", FLOOR, 0.5, chain_from=loop).id for _ in range(MAX_CHAIN_DEPTH + 1)]
    assert picks == ["loop"] * MAX_CHAIN_DEPTH + ["base"]


def test_controller_chains_from_the_row_it_started() -> None:
    table = _table(
        _row(id="first", state="BLOB", weight=1, next=[{"id": "second"}], next_mode="replace"),
        _row(id="second", state="IDLE", weight=0),
    )
    ctrl = _floor_ctrl(table)
    ctrl._start_idle_row(None, perched=False, roll=0.5, lo=0.0, chain_from=None)
    assert ctrl.state == PetState.BLOB
    assert ctrl._take_active_row().id == "first"
    ctrl._active_row, ctrl._active_row_state = table.row("first"), PetState.BLOB
    ctrl._start_idle_row(None, perched=False, roll=0.5, lo=0.0, chain_from=ctrl._take_active_row())
    assert ctrl.state == PetState.IDLE
    # Leaving the row's state (e.g. a poke) forgets the chain
    ctrl.tick(1.0 / 60.0)
    ctrl._active_row, ctrl._active_row_state = table.row("first"), PetState.BLOB
    ctrl.state = PetState.POKED
    ctrl.tick(1.0 / 60.0)
    assert ctrl._active_row is None


# --- Idle context -------------------------------------------------------------------------------


def test_idle_context_spots_and_levels() -> None:
    ctrl = _floor_ctrl()
    ctrl.body.x = 2.0
    assert ctrl._idle_context(None, perched=False).spots == {"screen_edge"}
    ctrl.body.x = 600.0
    ctx = ctrl._idle_context(None, perched=False)
    assert ctx.spots == {"open"} and ctx.idle_level == 1 and ctx.user_idle_s is None
    tall = WindowSnapshot(hwnd=1, rect=Rect(300.0, 100.0, 1200.0, 816.0), foreground=True)
    ctrl.body.x = 300.0 - 128.0 - 3.0
    ctx = ctrl._idle_context(tall, perched=False)
    assert "window_side" in ctx.spots and ctx.claude_visible and ctx.claude_foreground

    ctrl._pointer = PointerSnapshot(700.0, 500.0, idle_s=50.0)
    assert ctrl._idle_context(tall, perched=False).idle_level == 2
    assert ctrl._idle_context(tall, perched=False).cursor_in_window
    ctrl._pointer = PointerSnapshot(50.0, 50.0, idle_s=95.0)
    ctx = ctrl._idle_context(tall, perched=False)
    assert ctx.idle_level == 3 and not ctx.cursor_in_window

    ctrl._perch_offset_x = 10.0
    assert ctrl._idle_context(SNAP, perched=True).spots == {"perch_edge"}
    ctrl._perch_offset_x = 400.0
    ctx = ctrl._idle_context(SNAP, perched=True)
    assert ctx.spots == {"perch_middle"} and ctx.surface == "perch" and ctx.prev_state == "IDLE"


# --- Paths, packaging, tray, CLI ----------------------------------------------------------------


def test_paths_resolve_source_tree_and_pyinstaller_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolated_appdata: Path
) -> None:
    assert paths.assets_dir() == REPO / "assets"
    assert paths.default_behaviors_path() == REPO / "assets" / "behaviors" / "behaviors.default.json"
    assert default_sprites_dir() == REPO / "assets" / "sprites"
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.assets_dir() == tmp_path / "assets"
    assert default_sprites_dir() == tmp_path / "assets" / "sprites"
    assert paths.default_behaviors_path() == tmp_path / "assets" / "behaviors" / "behaviors.default.json"
    assert paths.app_data_dir() == _isolated_appdata / "Clawdlet"
    assert default_token_path() == _isolated_appdata / "Clawdlet" / "hook_token"


def test_pyinstaller_command_bundles_the_behavior_table() -> None:
    cmd = pyinstaller_command(REPO)
    data = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--add-data"]
    assert f"{REPO / 'assets' / 'behaviors'};assets/behaviors" in data
    assert f"{REPO / 'assets' / 'sprites'};assets/sprites" in data


def test_tray_open_config_folder_writes_a_template(_isolated_appdata: Path) -> None:
    opened: list[Path] = []
    ctrl = _floor_ctrl()
    dispatch_tray_action(CMD_OPEN_CONFIG_DIR, ctrl, None, TrayMenuState(), None, folder_opener=opened.append)
    folder = _isolated_appdata / "Clawdlet"
    assert opened == [folder]
    template = json.loads((folder / "behaviors.json").read_text(encoding="utf-8"))
    assert template["extends"] == "default" and template["version"] == 1 and template["behaviors"] == []
    assert (folder / "behaviors.schema.json").exists()
    assert load_behavior_table().warnings == ()
    # An existing file is never overwritten
    _write(folder / "behaviors.json", {"version": 1, "behaviors": [{"id": "floor_idle", "weight": 9}]})
    dispatch_tray_action(CMD_OPEN_CONFIG_DIR, ctrl, None, TrayMenuState(), None, folder_opener=opened.append)
    assert json.loads((folder / "behaviors.json").read_text(encoding="utf-8"))["behaviors"][0]["weight"] == 9


def test_tray_reload_swaps_the_controller_table(_isolated_appdata: Path) -> None:
    ctrl = _floor_ctrl()
    folder = _isolated_appdata / "Clawdlet"
    folder.mkdir(parents=True)
    _write(folder / "behaviors.json", {"version": 1, "behaviors": [{"id": "floor_blob", "enabled": False}]})
    assert ctrl.behavior_table.row("floor_blob").enabled
    dispatch_tray_action(CMD_RELOAD_BEHAVIORS, ctrl, None, TrayMenuState(), None)
    assert not ctrl.behavior_table.row("floor_blob").enabled
    assert ctrl._selector.table is ctrl.behavior_table


def test_dump_behaviors_prints_json_and_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [{"id": "floor_idle", "weight": 0.4}, _row(id="neg", weight=-1)]
    user = _write(tmp_path / "mine.json", {"version": 1, "behaviors": rows})
    assert main(["--dump-behaviors", "--behaviors", str(user)]) == 0
    out = capsys.readouterr()
    dumped = json.loads(out.out)
    assert dumped["version"] == 1 and dumped["extends"] == "none"
    assert next(r for r in dumped["behaviors"] if r["id"] == "floor_idle")["weight"] == 0.4
    assert "behaviors[neg] dropped" in out.err
    # The dump is itself a valid table that round-trips
    again = build_behavior_table(dumped)
    assert again.warnings == ()
    assert again.behaviors == load_behavior_table(user).behaviors
