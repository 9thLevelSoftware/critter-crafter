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
        assert all(leg["solver"] == "hinge4" for leg in block["legs"])
        assert all(leg["clearance_m"] >= .18 * leg["reach_m"] - 1e-6 for leg in block["legs"])
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


def test_every_skeleton_publishes_a_runtime_mode(catalog):
    modes = {}
    for skeleton in catalog["skeletons"]:
        modes.setdefault(skeleton["family"], set()).add(skeleton["locomotion"]["mode"])
    for family in ("biped", "quadruped", "crawler", "hexapod", "dragger"):
        assert modes[family] == {"legs"}, family
    assert modes["serpentine"] == {"legs", "slide"}
    assert modes["radial"] == {"legs"}


def test_legged_bands_are_consistent_and_most_reach_game_speed(catalog):
    slow = set()
    for skeleton in catalog["skeletons"]:
        block = skeleton["locomotion"]
        if block["mode"] != "legs":
            continue
        assert 0 < block["v_walk_mps"] < block["v_run_mps"] <= block["v_max_mps"], skeleton["skeleton_id"]
        if stepper.gait_params(block, GAME_SPEED)["overspeed"]:
            slow.add(skeleton["anatomy"]["archetype_id"])
    # Belly haulers and three-legged tripods (long duty to keep two feet down) are slower, and the compact
    # radial's stance is confined to the coxa yaw cone; the game reads v_max from the catalog rather than
    # forcing a uniform speed.
    # A compact seeded variant of the paired-leg serpentine falls just short of it too.
    assert slow <= {"dragger_belly_hauler", "crawler_alien_tripod", "radial_raised_articulated_walker",
                    "serpentine_segmented_paired_legs"}


def test_quadrupeds_walk_in_lateral_sequence_and_trot_when_running(catalog):
    for skeleton in catalog["skeletons"]:
        if skeleton["family"] != "quadruped":
            continue
        legs = {leg["branch_id"]: leg for leg in skeleton["locomotion"]["legs"]}
        walk = {k: v["walk_phase"] for k, v in legs.items()}
        run = {k: v["run_phase"] for k, v in legs.items()}
        assert walk == {"leg_L1": 0.0, "leg_L0": .25, "leg_R1": .5, "leg_R0": .75}
        assert run["leg_L0"] == run["leg_R1"] and run["leg_R0"] == run["leg_L1"]
        assert run["leg_L0"] != run["leg_R0"]


def test_slide_mode_phase_rate(catalog):
    block = next(s["locomotion"] for s in catalog["skeletons"] if s["locomotion"]["mode"] == "slide")
    params = stepper.slide_params(block, block["v_walk_mps"])
    assert params["cadence_hz"] == pytest.approx(1.0)
    assert stepper.slide_params(block, 100.0)["overspeed"]


def test_every_skeleton_passes_static_locomotion_qa(catalog):
    from critter_crafter.locomotion.qa import evaluate_locomotion
    problems = {s["skeleton_id"]: evaluate_locomotion(s) for s in catalog["skeletons"]}
    assert {k: v for k, v in problems.items() if v} == {}


def test_locomotion_golden_matches_current_planner(catalog):
    import json
    from critter_crafter.locomotion.qa import golden_rows
    golden = json.loads((paths().root / "tests" / "golden_v3" / "locomotion.json").read_text(encoding="utf-8"))
    assert golden["rows"] == json.loads(json.dumps(golden_rows(catalog))), "run `critter recipe golden`"


def _draggers(catalog, archetype):
    return [s for s in catalog["skeletons"] if s["anatomy"]["archetype_id"] == archetype]


def test_arm_leg_dragger_has_one_pulling_arm_and_one_pushing_leg(catalog):
    skeletons = _draggers(catalog, "dragger_arm_leg_crawler")
    assert len(skeletons) >= 3          # three presets plus the seeded variants
    for skeleton in skeletons:
        block = skeleton["locomotion"]
        assert block["mode"] == "legs" and block["gait"] == "drag" and block["body_on_ground"]
        legs = {leg["branch_id"]: leg for leg in block["legs"]}
        assert set(legs) == {"arm_L", "leg_R"}, skeleton["skeleton_id"]
        arm, leg = legs["arm_L"], legs["leg_R"]
        assert arm["drag_drive"] == "pull" and leg["drag_drive"] == "push"
        # The hand reaches out ahead of its shoulder; the foot sits behind its hip.
        assert arm["home_m"][2] > arm["hip_m"][2] and leg["home_m"][2] < leg["hip_m"][2]
        assert arm["stroke_m"] > 0.2 and leg["stroke_m"] > 0.2
        assert block["usable_stroke_m"] == min(arm["stroke_m"], leg["stroke_m"])
        # Half a cycle apart, so the hand grips while the foot drives and the reverse.
        assert abs(arm["walk_phase"] - leg["walk_phase"]) == pytest.approx(.5, abs=1e-6)
        assert leg["clearance_m"] < arm["clearance_m"], "a pushing leg does not lift as high as a reaching arm"
        assert 0 < block["v_walk_mps"] < block["v_run_mps"] < block["v_max_mps"]
        assert block["attack_branch_id"] == "arm_L"
        assert skeleton["anatomy"]["symmetry"]["kind"] == "asymmetric"


def test_symmetric_draggers_only_pull(catalog):
    for archetype in ("dragger_forelimb_puller", "dragger_belly_hauler"):
        for skeleton in _draggers(catalog, archetype):
            drives = {leg["drag_drive"] for leg in skeleton["locomotion"]["legs"]}
            assert drives == {"pull"}, skeleton["skeleton_id"]


def test_arm_leg_dragger_reaches_game_speed_within_step_rate(catalog):
    for skeleton in _draggers(catalog, "dragger_arm_leg_crawler"):
        block = skeleton["locomotion"]
        params = stepper.gait_params(block, min(GAME_SPEED, .9 * block["v_max_mps"]))
        assert not params["overspeed"], skeleton["skeleton_id"]
        assert params["cadence_hz"] <= block["cadence_max_hz"]
        assert params["stride_m"] * params["duty"] <= .9 * block["usable_stroke_m"] + 1e-9


def test_radial_stances_stay_inside_the_coxa_yaw_cone(catalog):
    import math
    from critter_crafter.locomotion.block import MAX_COXA_YAW_DEG
    radials = [s for s in catalog["skeletons"] if s["family"] == "radial"]
    assert len(radials) >= 3            # three presets plus the seeded variants
    for skeleton in radials:
        for leg in skeleton["locomotion"]["legs"]:
            hip, home = leg["hip_m"], leg["home_m"]
            neutral = math.atan2(home[0] - hip[0], home[2] - hip[2])
            # No leg lies along the travel axis: its stance would run through its own hip.
            assert abs(math.sin(neutral)) > .3, (skeleton["skeleton_id"], leg["branch_id"])
            for dz in (leg["stance_shift_m"] - leg["stroke_m"] / 2, leg["stance_shift_m"] + leg["stroke_m"] / 2):
                bearing = math.atan2(home[0] - hip[0], home[2] + dz - hip[2])
                yaw = math.degrees((bearing - neutral + math.pi) % (2 * math.pi) - math.pi)
                assert abs(yaw) <= MAX_COXA_YAW_DEG + .5, (skeleton["skeleton_id"], leg["branch_id"], yaw)


def test_radial_walks_in_alternating_tripods(catalog):
    for skeleton in (s for s in catalog["skeletons"] if s["family"] == "radial"):
        legs = skeleton["locomotion"]["legs"]
        assert {round(l["walk_phase"], 3) for l in legs} == {0.0, .5}
        assert [l["walk_phase"] for l in legs] == [l["run_phase"] for l in legs]


@pytest.mark.parametrize("name,index,contact", [
    ("neutral", 0, 3), ("grounded_overrun", 2, 3), ("instant_turn", 2, 3),
    ("ramp", 2, 3), ("hinge_limit", 0, 3), ("asymmetric_group_tie", 1, 1),
    ("support_release_blocked", -1, -1), ("early_swing_cap_blocked", -1, -1),
    ("no_ground", -1, -1), ("zero_stride_no_zero_hit", -1, -1),
    ("hinge_yaw_blocked", -1, -1), ("degenerate_reach", -1, -1),
    ("physical_cost", 0, 3),
])
def test_landing_physical_cases(name, index, contact):
    from critter_crafter.locomotion.qa import landing_golden_rows
    row = next(row for row in landing_golden_rows() if row["case_id"] == name)
    chosen = row["chosen_slot"]
    assert (-1 if chosen < 0 else row["candidates"][chosen]["index"]) == index
    assert (-1 if chosen < 0 else row["candidates"][chosen]["contact_index"]) == contact


def test_landing_rejects_invalid_numeric_and_reach_boundaries():
    candidates = [{"index": 0, "contact_index": 0, "ground_valid": True,
                   "hinge": False, "reach_fraction": float("nan"), "coxa_yaw_deg": 0.0}]
    for invalid in (float("nan"), float("inf"), -1.0, 1.000001):
        candidates[0]["reach_fraction"] = invalid
        assert stepper.choose_landing(candidates, 1.0, True) == -1
    for hinge, reach_limit in ((False, .95), (True, .97)):
        candidates[0]["hinge"] = hinge
        candidates[0]["reach_fraction"] = stepper.landing_reach_fraction(0, 0, reach_limit, 1)
        assert stepper.choose_landing(candidates, 0.0, True) == 0
        candidates[0]["reach_fraction"] = stepper.landing_reach_fraction(0, 0, reach_limit + 1e-6, 1)
        assert stepper.choose_landing(candidates, 0.0, True) == -1


def test_landing_offsets_use_only_five_travel_space_proposals():
    assert [stepper.candidate_offset(i, 2.0) for i in range(5)] == [
        (0.0, 0.0), (0.0, .2), (0.0, -.2), (.2, 0.0), (-.2, 0.0)]
    assert all(stepper.candidate_offset(i, 0.0) == (0.0, 0.0) for i in range(5))


def test_grounded_lifts_keep_authored_support_rule_but_not_early_swing_cap():
    assert not stepper.can_lift(True, 0, 1, True, True, 2, 2)
    assert stepper.can_lift(True, 1, 1, True, True, 2, 2)
    assert not stepper.can_lift(True, 1, 1, True, False, 2, 2)
    assert stepper.can_lift(True, 1, 1, False, False, 2, 2)
