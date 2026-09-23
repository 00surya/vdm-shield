import numpy as np

from vmd.eco import EcoGate


def frame(value=0):
    return np.full((180, 320, 3), value, np.uint8)


def test_eco_gate_starts_active_then_enters_quiet_and_wakes_on_motion():
    gate = EcoGate()
    assert gate.observe(frame(), 0)
    assert gate.observe(frame(), 9.9)
    assert not gate.observe(frame(), 10.1)
    moving = frame()
    moving[30:120, 50:180] = 255
    assert gate.observe(moving, 10.2)
    assert gate.snapshot()["state"] == "active"
    assert gate.snapshot()["motion_score"] > 0


def test_quiet_mode_keeps_periodic_weapon_and_pose_safety_scans():
    gate = EcoGate()
    gate.observe(frame(), 0)
    gate.observe(frame(), 11)
    assert gate.snapshot()["state"] == "quiet"
    assert gate.should_run("threat", 11)
    assert not gate.should_run("threat", 11.5)
    assert gate.should_run("threat", 12)
    assert gate.should_run("pose", 11)
    assert not gate.should_run("pose", 11.5)
    assert gate.should_run("depth", 11)
    assert not gate.should_run("depth", 19)
    assert gate.should_run("depth", 21)


def test_slow_scene_change_is_compared_with_background():
    gate = EcoGate(motion_ratio=.02, pixel_delta=8)
    gate.observe(frame(), 0)
    gate.observe(frame(), 11)
    assert gate.snapshot()["state"] == "quiet"
    # Small changes accumulate against the slow background reference.
    for second, value in enumerate(range(2, 18, 2), start=12):
        active = gate.observe(frame(value), second)
        if active:
            break
    assert active


def test_pending_interaction_keeps_full_sampling_through_confirmation():
    gate = EcoGate()
    gate.observe(frame(), 0)
    gate.observe(frame(), 11)
    assert gate.snapshot()['state'] == 'quiet'
    gate.keep_active(11)
    assert gate.should_run('pose', 11)
    assert gate.should_run('pose', 11.1)
    assert gate.observe(frame(), 16)
    assert gate.snapshot()['state'] == 'active'
    assert not gate.observe(frame(), 21.1)
