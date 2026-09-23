"""Locomotion catalog block and reference step planner."""

from __future__ import annotations

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.locomotion import stepper
from critter_crafter.locomotion.block import cadence_max_hz, support_holds

GAME_SPEED = 2.5


@pytest.fixture(scope="module")
def catalog():
    return compile_catalog(load_sources(paths().data))


def _hexapods(catalog):
    return [s for s in catalog["skeletons"] if s["family"] == "hexapod"]


def test_hexapods_have_runtime_legs_with_stroke(catalog):
    for skeleton in _hexapods(catalog):
        block = skeleton["locomotion"]
        assert block["mode"] == "legs"
        assert len(block["legs"]) == 6
        assert all(leg["solver"] == "chain" for leg in block["legs"])
        assert block["usable_stroke_m"] > .5, skeleton["skeleton_id"]
        assert 0 < block["v_walk_mps"] < block["v_run_mps"] < block["v_max_mps"]
        assert block["attack_branch_id"] == "leg2_L"


def test_hexapods_reach_game_speed_within_step_rate(catalog):
    for skeleton in _hexapods(catalog):
        block = skeleton["locomotion"]
        params = stepper.gait_params(block, GAME_SPEED)
        assert not params["overspeed"], skeleton["skeleton_id"]
        assert params["cadence_hz"] <= block["cadence_max_hz"]
        # The stance stroke fits inside the usable stroke.
        assert params["stride_m"] * params["duty"] <= .9 * block["usable_stroke_m"] + 1e-9


def test_duty_factors_keep_minimum_support(catalog):
    for skeleton in catalog["skeletons"]:
        block = skeleton["locomotion"]
        if block["mode"] != "legs":
            continue
        supports = [leg for leg in block["legs"] if leg["support"]] or block["legs"]
        for key, duty in (("walk_phase", block["duty_walk"]), ("run_phase", block["duty_run"])):
            assert support_holds([leg[key] for leg in supports], duty, block["min_support"]), skeleton["skeleton_id"]


def test_gait_params_monotonic_and_walk_run_blend(catalog):
    block = _hexapods(catalog)[0]["locomotion"]
    slow = stepper.gait_params(block, block["v_walk_mps"])
    fast = stepper.gait_params(block, block["v_run_mps"])
    assert slow["weight"] == 0.0 and not slow["run"]
    assert fast["weight"] == 1.0 and fast["run"]
    assert fast["duty"] == pytest.approx(block["duty_run"])
    assert fast["stride_m"] > slow["stride_m"] and fast["cadence_hz"] > slow["cadence_hz"]
    idle = stepper.gait_params(block, 0.0)
    assert idle["cadence_hz"] == 0.0 and idle["stride_m"] == 0.0


def test_landing_lead_centres_stance_on_home():
    speed, cadence, duty = 2.0, 2.5, .5
    lead = stepper.landing_lead(speed, cadence, duty)
    stance_travel = speed * duty / cadence
    assert lead == pytest.approx(stance_travel / 2)
    leg = {"home_m": [.3, .0075, .1]}
    assert stepper.landing_target_local(leg, speed, cadence, duty) == pytest.approx([.3, .0075, .1 + lead])


def test_swing_point_endpoints_and_apex():
    a, b = [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]
    assert stepper.swing_point(a, b, 0.0, .2) == pytest.approx(a)
    assert stepper.swing_point(a, b, 1.0, .2) == pytest.approx(b)
    assert stepper.swing_point(a, b, .5, .2) == pytest.approx([0.0, .2, .5])


def test_cadence_cap_scales_with_leg_size():
    assert cadence_max_hz(.25) == 6.0
    assert cadence_max_hz(1.0) == pytest.approx(3.0)
    assert cadence_max_hz(4.0) == 2.0
