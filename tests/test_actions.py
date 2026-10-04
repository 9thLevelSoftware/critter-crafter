"""Authored semantic attack plans are complete, reachable, and support-safe."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from critter_crafter.library.catalog import CatalogError, compile_catalog, load_sources
from critter_crafter.skeletons.actions import (
    ACTION_PLAN_VERSION,
    ATTACK_PROFILES,
    AttackPlanError,
    action_global_phase,
    attack_target_at,
    resolve_attack,
)
from critter_crafter.skeletons.archetypes import ARCHETYPES, build_candidate
from critter_crafter.skeletons.amalgam import AMALGAM_SEEDS, build_amalgam


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
    assert len({profile.attack_id for profile in ATTACK_PROFILES.values()}) == len(ATTACK_PROFILES)
    assert all(profile.effector_slot and profile.bone_index >= 0 for profile in ATTACK_PROFILES.values())


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
        "dragger_arm_leg_crawler": ("arm_L", "lopsided_hammer"),
    }
    assert {
        skeleton["anatomy"]["archetype_id"]: (
            resolve_attack(skeleton)["effector"]["branch_id"], resolve_attack(skeleton)["attack_id"],
        )
        for skeleton in _skeletons()
        if skeleton["anatomy"]["archetype_id"] in expected
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
    assert len(haulers) >= 3          # three presets plus the seeded variants
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


def _compile_source(source: dict) -> dict:
    sources = load_sources(Path("data"))
    sources["skeletons"] = [source]
    return compile_catalog(sources)["skeletons"][0]


def _rename_branch(value, old: str, new: str):
    if isinstance(value, dict):
        return {
            key: copy.deepcopy(item) if key in {"role", "gait_role", "categories"} else _rename_branch(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rename_branch(item, old, new) for item in value]
    if isinstance(value, str) and (value == old or value.startswith(old + "_b") or value.startswith(old + ":")):
        return new + value[len(old):]
    return value


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
def test_semantic_effectors_survive_branch_and_bone_renaming(archetype: str) -> None:
    source = build_candidate(archetype, "balanced")
    original = resolve_attack(_compile_source(source))
    old = original["effector"]["branch_id"]
    changed = resolve_attack(_compile_source(_rename_branch(source, old, "renamed_effector")))
    assert changed == _rename_branch(original, old, "renamed_effector")
    for phase in (0.0, .1, original["timing"]["windup_end"], original["timing"]["impact"], .9, 1.0):
        assert attack_target_at(changed, phase) == _rename_branch(
            attack_target_at(original, phase), old, "renamed_effector",
        )


@pytest.mark.parametrize("seed", AMALGAM_SEEDS)
def test_amalgam_selection_ignores_branch_order_and_unrelated_manipulators(seed: int) -> None:
    skeleton = _compile_source(build_amalgam(seed))
    original = resolve_attack(skeleton)
    renamed = _rename_branch(skeleton, "striker", "unrelated_name")
    detail = copy.deepcopy(next(branch for branch in renamed["branches"] if branch.get("effector_slot") == "primary_strike"))
    detail.pop("effector_slot")
    detail["branch_id"] = "decoy"
    detail["capabilities"] = ["grasp"]
    renamed["branches"].insert(0, detail)
    changed = resolve_attack(renamed)
    assert changed == _rename_branch(original, "striker", "unrelated_name")


def _break_semantics(skeleton: dict, mutation: str) -> None:
    selected = next(branch for branch in skeleton["branches"] if branch.get("effector_slot") == "designated_tripod_striker")
    if mutation == "missing":
        selected.pop("effector_slot")
    elif mutation == "duplicate":
        skeleton["branches"][0]["effector_slot"] = selected["effector_slot"]
    elif mutation == "wrong":
        selected["capabilities"] = ["support", "bite"]
    elif mutation == "duplicate_capability":
        selected["capabilities"].append("strike")
    elif mutation == "unsupported_contact":
        selected["capabilities"].remove("support")


@pytest.mark.parametrize("mutation,code", [
    ("missing", "CC_ACTION_EFFECTOR"),
    ("duplicate", "CC_ACTION_EFFECTOR"),
    ("wrong", "CC_ACTION_CAPABILITY"),
    ("duplicate_capability", "CC_ACTION_CAPABILITY"),
    ("unsupported_contact", "CC_ACTION_CAPABILITY"),
])
def test_source_compiler_and_resolver_reject_invalid_semantics(mutation: str, code: str) -> None:
    source = build_candidate("crawler_alien_tripod", "balanced")
    compiled = _compile_source(source)
    _break_semantics(source, mutation)
    _break_semantics(compiled, mutation)
    with pytest.raises(CatalogError, match=code):
        _compile_source(source)
    with pytest.raises(AttackPlanError, match=code):
        resolve_attack(compiled)


def _reduced_support_topology() -> dict:
    skeleton = _compile_source(build_candidate("crawler_bilateral_eight_legged", "balanced"))
    removed = {"leg0_L", "leg0_R"}
    skeleton["branches"] = [branch for branch in skeleton["branches"] if branch["branch_id"] not in removed]
    for field in ("support_branches", "contact_branches"):
        skeleton["anatomy"][field] = [value for value in skeleton["anatomy"][field] if value not in removed]
    return skeleton


def test_topology_policy_preserves_every_other_contact_without_lowering_curated_requirement() -> None:
    skeleton = _reduced_support_topology()
    with pytest.raises(AttackPlanError, match="requires 7"):
        resolve_attack(skeleton)
    skeleton["anatomy"]["action_support"] = {
        "released_contact_ids": ["leg3_L:0"], "minimum_preserved": 5,
    }
    plan = resolve_attack(skeleton)
    assert plan["support_release"]["contact_ids"] == ["leg3_L:0"]
    assert set(plan["support_release"]["preserved_contact_ids"]) == _contact_ids(skeleton) - {"leg3_L:0"}
    assert plan["support_release"]["minimum_preserved"] == 5
    curated = resolve_attack(_compile_source(build_candidate("crawler_bilateral_eight_legged", "balanced")))
    assert curated["support_release"]["minimum_preserved"] == 7


@pytest.mark.parametrize("release,minimum", [
    (["leg3_R:0"], 5),
    (["leg3_L:1"], 5),
    (["leg3_L:0", "leg3_L:0"], 5),
    ([], 6),
    (["leg3_L:0"], 2),
    (["leg3_L:0"], 6),
])
def test_topology_policy_rejects_wrong_unknown_duplicate_missing_or_miscounted_contacts(release, minimum) -> None:
    skeleton = _reduced_support_topology()
    skeleton["anatomy"]["action_support"] = {
        "released_contact_ids": release, "minimum_preserved": minimum,
    }
    with pytest.raises(AttackPlanError, match="CC_ACTION_SUPPORT"):
        resolve_attack(skeleton)


def test_topology_policy_cannot_release_below_the_authored_support_floor() -> None:
    skeleton = _compile_source(build_candidate("crawler_alien_tripod", "balanced"))
    skeleton["anatomy"]["traits"]["min_support"] = 3
    skeleton["anatomy"]["action_support"] = {
        "released_contact_ids": ["leg_2:0"], "minimum_preserved": 2,
    }
    with pytest.raises(AttackPlanError, match="support floor"):
        resolve_attack(skeleton)


@pytest.mark.parametrize("mutation,code", [
    ("missing", "CC_ACTION_EFFECTOR"),
    ("duplicate", "CC_ACTION_EFFECTOR"),
    ("wrong", "CC_ACTION_CAPABILITY"),
])
def test_derived_amalgam_cannot_fall_back_to_another_arm(mutation: str, code: str) -> None:
    skeleton = _compile_source(build_amalgam(AMALGAM_SEEDS[0]))
    selected = next(branch for branch in skeleton["branches"] if branch.get("effector_slot") == "primary_strike")
    if mutation == "missing":
        selected.pop("effector_slot")
    elif mutation == "duplicate":
        skeleton["branches"][0]["effector_slot"] = "primary_strike"
    else:
        selected["capabilities"] = ["grasp"]
    with pytest.raises(AttackPlanError, match=code):
        resolve_attack(skeleton)


def test_contactless_amalgam_policy_preserves_all_body_and_limb_supports() -> None:
    for seed in AMALGAM_SEEDS:
        skeleton = _compile_source(build_amalgam(seed))
        original = resolve_attack(skeleton)
        skeleton["anatomy"]["action_support"] = {
            "released_contact_ids": [], "minimum_preserved": len(_contact_ids(skeleton)),
        }
        assert resolve_attack(skeleton) == original
