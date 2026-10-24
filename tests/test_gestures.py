"""Milestone 5 step B: click/drag/combo recognition and stroke (petting) detection."""

from __future__ import annotations

from clawdlet.gestures import ClickGestureRecognizer, Gesture, PetStrokeDetector, system_double_click_s

DCLICK = 0.4


def _click(rec: ClickGestureRecognizer, t: float, x: float = 10.0, y: float = 10.0) -> None:
    rec.press(t, x, y)
    rec.release(t + 0.05)


def _drain(rec: ClickGestureRecognizer, start: float, end: float, step: float = 0.01) -> list[Gesture]:
    out: list[Gesture] = []
    t = start
    while t <= end:
        g = rec.poll(t)
        if g is not None:
            out.append(g)
        t += step
    return out


def test_single_click_settles_to_poke_after_interval() -> None:
    rec = ClickGestureRecognizer(double_click_s=DCLICK)
    _click(rec, 0.0)
    assert rec.poll(0.2) is None  # still waiting for a possible second click
    assert _drain(rec, 0.2, 1.0) == [Gesture.POKE]


def test_double_click_yields_double_without_poke() -> None:
    rec = ClickGestureRecognizer(double_click_s=DCLICK)
    _click(rec, 0.0)
    assert rec.poll(0.15) is None
    _click(rec, 0.25)
    assert _drain(rec, 0.3, 1.5) == [Gesture.DOUBLE]


def test_triple_click_fires_combo_immediately_and_swallows_the_rest() -> None:
    rec = ClickGestureRecognizer(double_click_s=DCLICK)
    _click(rec, 0.0)
    _click(rec, 0.2)
    _click(rec, 0.4)
    assert rec.poll(0.46) == Gesture.COMBO
    _click(rec, 0.6)
    _click(rec, 0.8)
    assert _drain(rec, 0.5, 2.0) == []


def test_press_and_move_becomes_drag_not_click() -> None:
    rec = ClickGestureRecognizer(double_click_s=DCLICK, drag_threshold_px=4.0)
    rec.press(0.0, 100.0, 100.0)
    assert rec.motion(0.02, 102.0, 101.0) is None  # within the 4px slop
    assert rec.motion(0.04, 104.0, 102.0) == Gesture.DRAG_START
    assert rec.dragging
    assert rec.press_point == (100.0, 100.0)
    assert rec.motion(0.06, 140.0, 120.0) is None  # DRAG_START fires once
    rec.release(0.3)
    assert not rec.dragging
    assert _drain(rec, 0.3, 1.5) == []


def test_drag_cancels_pending_click_burst() -> None:
    rec = ClickGestureRecognizer(double_click_s=DCLICK)
    _click(rec, 0.0)
    rec.press(0.2, 10.0, 10.0)
    rec.motion(0.25, 30.0, 10.0)
    rec.release(0.5)
    assert _drain(rec, 0.5, 1.5) == []


def test_system_double_click_is_clamped() -> None:
    assert 0.2 <= system_double_click_s() <= 0.5


def test_stroking_back_and_forth_fires_pet() -> None:
    det = PetStrokeDetector(min_travel_px=77.0)
    xs = [100, 130, 100, 130, 100, 130]  # 5 reversals, 150px travel
    fired = [det.motion(i * 0.15, float(x)) for i, x in enumerate(xs)]
    assert fired.count(True) == 1
    assert fired[-1] is True or fired[-2] is True


def test_one_way_glide_or_slow_stroke_is_not_petting() -> None:
    det = PetStrokeDetector(min_travel_px=77.0)
    assert not any(det.motion(i * 0.05, 100.0 + i * 10.0) for i in range(20))
    det.reset()
    # Same zig-zag but spread over 3 seconds: reversals fall out of the 1.2s window
    xs = [100, 130, 100, 130, 100, 130]
    assert not any(det.motion(i * 0.6, float(x)) for i, x in enumerate(xs))


def test_tiny_jitter_is_ignored() -> None:
    det = PetStrokeDetector(min_travel_px=10.0)
    xs = [100, 101, 100, 101, 100, 101, 100, 101]
    assert not any(det.motion(i * 0.05, float(x)) for i, x in enumerate(xs))
