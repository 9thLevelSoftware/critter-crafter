"""Authored semantic attack plans are complete, reachable, and support-safe."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.skeletons.actions import (
    ACTION_PLAN_VERSION,
    ATTACK_PROFILES,
    AttackPlanError,
    action_global_phase,
    attack_target_at,
    resolve_attack,
)
from critter_crafter.skeletons.archetypes import ARCHETYPES


def _skeletons() -> list[dict]:
    return compile_catalog(load_sources(Path("data")))["skeletons"]


def _contact_ids(skeleton: dict) -> set[str]:
    support = set(skeleton["anatomy"]["support_branches"])
    return {
        f"{branch['branch_id']}:{index}"
        for branch in skeleton["branches"]
        if branch["branch_id"] in support
        for index, _contact in enumerate(branch.get("contacts", []))
    }


def test_profiles_cover_all_fourteen_archetypes_with_declared_semantics() -> None:
    assert set(ATTACK_PROFILES) == set(ARCHETYPES)
    assert len(ATTACK_PROFILES) == 13
    assert len({profile.attack_id for profile in ATTACK_PROFILES.values()}) == 13
    assert all(profile.branch_id and profile.bone_index >= 0 for profile in ATTACK_PROFILES.values())


@pytest.mark.parametrize("skeleton", _skeletons(), ids=lambda item: item["skeleton_id"])
def test_every_candidate_resolves_a_json_safe_reach_bounded_plan(skeleton: dict) -> None:
    plan = resolve_attack(skeleton)
    branch = next(item for item in skeleton["branches"] if item["branch_id"] == plan["effector"]["branch_id"])
    bone = next(item for item in skeleton["bones"] if item["name"] == plan["effector"]["bone_name"])
    bone_length = math.dist(bone["head_m"], bone["tail_m"])

    assert plan["version"] == ACTION_PLAN_VERSION
    assert plan["archetype_id"] == skeleton["anatomy"]["archetype_id"]
    assert plan["effector"]["bone_name"] == branch["bone_names"][plan["effector"]["bone_index"]]
    assert plan["effector"]["space"] == "bone_local"
    assert plan["effector"]["local_point_m"] == pytest.approx([0.0, bone_length, 0.0], abs=1e-6)
    assert plan["trajectory"]["space"] == "catalog"
    assert math.dist([0, 0, 0], plan["trajectory"]["impact_offset_m"]) >= plan["qa"]["minimum_displacement_m"]
    assert max(
        math.dist([0, 0, 0], plan["trajectory"][key])
        for key in ("windup_offset_m", "impact_offset_m", "recovery_offset_m")
    ) <= plan["trajectory"]["max_reach_m"] + 1e-6
    assert 0 < plan["timing"]["windup_end"] < plan["timing"]["impact"]
    assert plan["timing"]["impact"] < plan["timing"]["recovery_start"] < plan["timing"]["recovery_end"] == 1.0
    json.dumps(plan, sort_keys=True)


def test_targets_are_resolved_from_authored_neutral_not_measured_samples() -> None:
    skeleton = next(item for item in _skeletons() if item["skeleton_id"] == "biped_plantigrade_humanoid_balanced_v3")
    changed_pose = copy.deepcopy(skeleton)
    translation = [4.0, 5.0, 6.0]
    changed_pose["neutral_pose"]["root_offset_m"] = [
        value + delta
        for value, delta in zip(changed_pose["neutral_pose"]["root_offset_m"], translation, strict=True)
    ]

    original = resolve_attack(skeleton)
    changed = resolve_attack(changed_pose)
    assert changed["effector"] == original["effector"]
    assert changed["trajectory"]["impact_offset_m"] == original["trajectory"]["impact_offset_m"]
    assert changed["trajectory"]["impact_target_m"] == pytest.approx([
        value + delta
        for value, delta in zip(original["trajectory"]["impact_target_m"], translation, strict=True)
    ])


def test_declared_effectors_match_the_intended_anatomical_attacks() -> None:
    expected = {
        "biped_plantigrade_humanoid": ("arm_R", "straight_punch"),
        "biped_digitigrade_creature": ("arm_R", "raking_claw"),
        "quadruped_stocky_plantigrade": ("head", "driving_bite"),
        "quadruped_lean_digitigrade": ("head", "snap_bite"),
        "crawler_bilateral_eight_legged": ("leg3_L", "foreleg_spear"),
        "crawler_alien_tripod": ("leg_2", "tripod_hook"),
        "hexapod_compact_insect": ("leg2_L", "foreleg_jab"),
        "hexapod_elongated_insect": ("leg2_L", "foreleg_lance"),
        "radial_raised_articulated_walker": ("arm_0", "radial_stab"),
        "serpentine_limbless_articulated": ("body", "tail_whip"),
        "serpentine_segmented_paired_legs": ("body", "segmented_tail_sweep"),
        "dragger_forelimb_puller": ("arm_L", "puller_hammer"),
        "dragger_belly_hauler": ("arm_L", "hauler_shove"),
    }
    assert {
        archetype_id: (profile.branch_id, profile.attack_id)
        for archetype_id, profile in ATTACK_PROFILES.items()
    } == expected


def test_support_release_is_contact_granular_and_preserves_declared_support() -> None:
    for skeleton in _skeletons():
        plan = resolve_attack(skeleton)
        policy = plan["support_release"]
        supports = _contact_ids(skeleton)
        released = set(policy["contact_ids"])
        preserved = set(policy["preserved_contact_ids"])

        assert released <= supports
        assert preserved == supports - released
        assert released.isdisjoint(preserved)
        assert len(preserved) >= policy["minimum_preserved"]
        assert all(item.startswith(plan["effector"]["branch_id"] + ":") for item in released)

    limbless = next(item for item in _skeletons() if item["skeleton_id"] == "serpentine_limbless_articulated_balanced_v3")
    policy = resolve_attack(limbless)["support_release"]
    assert policy["contact_ids"] == ["body:2"]
    assert policy["preserved_contact_ids"] == ["body:0", "body:1"]


def test_phase_sampler_hits_declared_targets_and_release_window() -> None:
    skeleton = next(item for item in _skeletons() if item["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    plan = resolve_attack(skeleton)
    timing = plan["timing"]

    assert attack_target_at(plan, 0.0)["target_offset_m"] == [0.0, 0.0, 0.0]
    assert attack_target_at(plan, timing["windup_end"])["target_offset_m"] == plan["trajectory"]["windup_offset_m"]
    impact = attack_target_at(plan, timing["impact"])
    assert impact["target_offset_m"] == plan["trajectory"]["impact_offset_m"]
    assert impact["release_contact_ids"] == plan["support_release"]["contact_ids"]
    assert attack_target_at(plan, 1.0)["target_offset_m"] == [0.0, 0.0, 0.0]
    assert attack_target_at(plan, 0.0)["release_contact_ids"] == []
    assert attack_target_at(plan, timing["windup_end"] / 2)["release_contact_ids"] == plan["support_release"]["contact_ids"]
    assert attack_target_at(plan, 1.0)["release_contact_ids"] == []


def test_clip_phases_form_one_continuous_global_action() -> None:
    skeleton = next(item for item in _skeletons() if item["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    plan = resolve_attack(skeleton)
    seam = plan["timing"]["windup_end"]

    assert action_global_phase(plan, "telegraph", 0.0) == 0.0
    assert action_global_phase(plan, "telegraph", 1.0) == seam
    assert action_global_phase(plan, "attack", 0.0) == seam
    assert action_global_phase(plan, "attack", 1.0) == 1.0
    assert attack_target_at(plan, action_global_phase(plan, "telegraph", 1.0)) == attack_target_at(
        plan, action_global_phase(plan, "attack", 0.0)
    )


def test_all_authored_targets_are_nonstretch_for_the_declared_solver_chain() -> None:
    for skeleton in _skeletons():
        plan = resolve_attack(skeleton)
        anchor = plan["solver_chain"]["anchor_neutral_m"]
        chain_length = plan["solver_chain"]["length_m"]
        tolerance = plan["qa"]["target_tolerance_m"]
        for key in ("windup_target_m", "impact_target_m", "recovery_target_m"):
            radius = math.dist(anchor, plan["trajectory"][key])
            assert radius <= chain_length + 1e-6
            if len(plan["solver_chain"]["bone_names"]) == 1:
                assert abs(radius - chain_length) <= tolerance + 1e-6


def test_one_bone_bites_follow_the_exact_reach_sphere_for_the_full_cycle() -> None:
    for skeleton in _skeletons():
        if not skeleton["anatomy"]["archetype_id"].startswith("quadruped_"):
            continue
        plan = resolve_attack(skeleton)
        assert plan["trajectory"]["projection"] == "one_bone_sphere"
        anchor = plan["solver_chain"]["anchor_neutral_m"]
        neutral = plan["trajectory"]["neutral_effector_m"]
        radius = plan["solver_chain"]["length_m"]
        for sample_index in range(201):
            offset = attack_target_at(plan, sample_index / 200)["target_offset_m"]
            target = [value + delta for value, delta in zip(neutral, offset, strict=True)]
            assert abs(math.dist(anchor, target) - radius) <= 1e-6


def test_grounded_windups_lift_and_tail_whips_retract() -> None:
    skeletons = {item["anatomy"]["archetype_id"]: item for item in _skeletons() if "balanced" in item["skeleton_id"]}
    for archetype_id in ("serpentine_limbless_articulated", "serpentine_segmented_paired_legs"):
        skeleton = skeletons[archetype_id]
        branch = next(item for item in skeleton["branches"] if item["branch_id"] == "body")
        plan = resolve_attack(skeleton)
        assert plan["trajectory"]["impact_offset_m"][2] >= branch["length_m"] * .05
        assert plan["trajectory"]["windup_offset_m"][1] >= branch["length_m"] * .19 - 1e-6
        assert plan["trajectory"]["impact_offset_m"][1] >= branch["length_m"] * .18 - 1e-6
        assert abs(plan["trajectory"]["impact_offset_m"][0]) <= branch["length_m"] * .061


def test_belly_hauler_shove_fits_its_low_short_arm_and_remains_meaningful() -> None:
    haulers = [
        item for item in _skeletons()
        if item["anatomy"]["archetype_id"] == "dragger_belly_hauler"
    ]
    assert len(haulers) == 3
    for skeleton in haulers:
        plan = resolve_attack(skeleton)
        branch = next(item for item in skeleton["branches"] if item["branch_id"] == "arm_L")
        impact = plan["trajectory"]["impact_offset_m"]
        windup = plan["trajectory"]["windup_offset_m"]
        assert impact == pytest.approx([branch["length_m"] * value for value in (.04, .09, .11)])
        assert math.dist(windup, impact) >= plan["qa"]["minimum_displacement_m"]
        assert math.dist(
            plan["solver_chain"]["anchor_neutral_m"],
            plan["trajectory"]["impact_target_m"],
        ) <= plan["solver_chain"]["length_m"] + 1e-6


def test_unknown_archetype_and_structural_drift_fail_loudly() -> None:
    skeleton = next(item for item in _skeletons() if item["skeleton_id"] == "crawler_alien_tripod_balanced_v3")
    unknown = copy.deepcopy(skeleton)
    unknown["anatomy"]["archetype_id"] = "unknown"
    with pytest.raises(AttackPlanError, match="CC_ACTION_ARCHETYPE"):
        resolve_attack(unknown)

    missing_effector = copy.deepcopy(skeleton)
    missing_effector["branches"] = [branch for branch in missing_effector["branches"] if branch["branch_id"] != "leg_2"]
    with pytest.raises(AttackPlanError, match="CC_ACTION_EFFECTOR"):
        resolve_attack(missing_effector)
