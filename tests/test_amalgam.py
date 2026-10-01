"""Seeded amalgam skeletons: fused bodies, mixed limbs, a few of them working."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.skeletons import amalgam
from critter_crafter.skeletons.actions import ATTACK_PROFILES, derive_attack_profile, resolve_attack

WORKING = ("locomotor",)


@pytest.fixture(scope="module")
def catalog():
    return compile_catalog(load_sources(paths().data))


@pytest.fixture(scope="module")
def compiled(catalog):
    return {s["skeleton_id"]: s for s in catalog["skeletons"] if s["family"] == "amalgam"}


def test_generation_is_deterministic_and_the_seed_is_in_the_id():
    for seed in amalgam.AMALGAM_SEEDS:
        first = amalgam.build_amalgam(seed)
        assert first == amalgam.build_amalgam(seed)
        assert first["skeleton_id"] == amalgam.amalgam_id(seed)
        assert f"_s{seed:04d}_" in first["skeleton_id"]
    assert len({amalgam.amalgam_id(s) for s in amalgam.AMALGAM_SEEDS}) == len(amalgam.AMALGAM_SEEDS)


def test_the_movement_mode_comes_from_its_own_stream():
    # Pinned: changing how limbs are scattered must never reshuffle which mode a committed seed gets.
    assert [amalgam.mode_for_seed(s) for s in (4, 8, 6)] == ["hauled", "walker", "slither"]


def test_every_mode_has_several_committed_seeds():
    counts = Counter(amalgam.mode_for_seed(s) for s in amalgam.AMALGAM_SEEDS)
    assert set(counts) == set(amalgam.MODES)
    assert all(n >= 4 for n in counts.values())


def test_committed_files_match_the_generator():
    directory = Path(__file__).parents[1] / "data" / "skeletons"
    on_disk = sorted(p.name for p in directory.glob("amalgam_*_v3.skeleton.json"))
    assert on_disk == sorted(f"{amalgam.amalgam_id(s)}.skeleton.json" for s in amalgam.AMALGAM_SEEDS)


@pytest.mark.parametrize("seed", amalgam.AMALGAM_SEEDS)
def test_each_committed_amalgam_checks_out_end_to_end(seed):
    """Compiles, has a sound locomotion block and attack, grounds every working limb, plans all clips."""
    assert amalgam.validate_amalgam(amalgam.build_amalgam(seed)) == []


def test_only_working_limbs_are_locomotors_and_they_hang_off_the_body(compiled):
    for skeleton in compiled.values():
        branches = skeleton["branches"]
        supports = set(skeleton["anatomy"]["support_branches"])
        assert {b["branch_id"] for b in branches if b["gait_role"] == "locomotor"} == supports
        assert all(b["parent_branch"] == "core" for b in branches if b["branch_id"] in supports)
        assert sum(b["gait_role"] == "manipulator" for b in branches) == 1              # exactly one striker
        assert all(not b.get("contacts") for b in branches if b["gait_role"] in ("flail", "none", "manipulator", "head"))
        assert len(branches) <= amalgam.MAX_BRANCHES
        assert skeleton["anatomy"]["symmetry"] == {"kind": "asymmetric"}


def test_vestigial_limbs_are_optional_and_never_carry_weight(compiled):
    for skeleton in compiled.values():
        flails = [b for b in skeleton["branches"] if b["branch_id"].startswith("flail")]
        assert 2 <= len(flails)
        for branch in flails:
            assert branch["required"] is False
            assert 60 <= branch["optional_fill_pct"] <= 80
            assert branch["gait_role"] in ("flail", "none")


def test_the_locomotion_block_follows_the_mode(compiled):
    for skeleton in compiled.values():
        block, mode = skeleton["locomotion"], skeleton["anatomy"]["archetype_id"]
        if mode == "amalgam_slither":
            assert block["mode"] == "slide"
            continue
        assert {leg["branch_id"] for leg in block["legs"]} == {
            b["branch_id"] for b in skeleton["branches"] if b["gait_role"] == "locomotor" and b["branch_id"] != "belly"}
        if mode == "amalgam_hauled":
            assert block["gait"] == "drag" and block["body_on_ground"]
            assert {leg["drag_drive"] for leg in block["legs"]} <= {"pull", "push"}
        else:
            assert block.get("gait", "walk") != "drag"
            assert all(leg["stance_shift_m"] != 0.0 or leg["solver"] == "hinge4" for leg in block["legs"])


def test_walkers_grow_mismatched_legs(compiled):
    for skeleton in compiled.values():
        if skeleton["anatomy"]["archetype_id"] != "amalgam_walker":
            continue
        profiles = {b["binding_profile_id"] for b in skeleton["branches"] if b["gait_role"] == "locomotor"}
        assert len(profiles) >= 2, skeleton["skeleton_id"]


def test_the_attack_is_derived_from_the_striker(compiled):
    for skeleton in compiled.values():
        assert skeleton["anatomy"]["archetype_id"] not in ATTACK_PROFILES
        assert derive_attack_profile(skeleton) is not None
        assert resolve_attack(skeleton)["effector"]["branch_id"] == "striker"
        # A sliding body has no gait to release a leg from, so its block leaves the attack branch empty.
        expected = "" if skeleton["locomotion"]["mode"] == "slide" else "striker"
        assert skeleton["locomotion"]["attack_branch_id"] == expected


def test_traits_say_how_many_feet_must_stay_down(compiled):
    for skeleton in compiled.values():
        legs = [b for b in skeleton["branches"] if b["gait_role"] == "locomotor" and b["branch_id"] != "belly"]
        traits = skeleton["anatomy"]["traits"]
        if skeleton["anatomy"]["archetype_id"] == "amalgam_walker":
            assert traits["min_support"] == (1 if len(legs) <= 3 else 2)
        assert traits["body_on_ground"] == (skeleton["anatomy"]["archetype_id"] == "amalgam_hauled")


def test_amalgams_have_their_own_pool(catalog):
    pool = next(p for p in catalog["pools"] if p["pool_id"] == "amalgam")
    assert pool["families"] == ["amalgam"]
    any_pool = next(p for p in catalog["pools"] if p["pool_id"] == "any")
    assert "amalgam" not in any_pool["families"]


def test_walkers_limp_on_one_leg_and_step_unevenly(compiled):
    for skeleton in compiled.values():
        if skeleton["anatomy"]["archetype_id"] != "amalgam_walker":
            continue
        block = skeleton["locomotion"]
        limpers = [leg for leg in block["legs"] if leg.get("limp")]
        assert len(limpers) == 1, skeleton["skeleton_id"]
        assert block["body_limp_m"] == limpers[0]["limp"] > 0
        phases = sorted(leg["walk_phase"] for leg in block["legs"])
        gaps = [b - a for a, b in zip(phases, phases[1:] + [phases[0] + 6.283185])]
        assert max(gaps) - min(gaps) > .1, "the legs step in a metronome rhythm"


def test_walker_legs_are_long_and_slim_beside_the_body(compiled):
    for skeleton in compiled.values():
        if skeleton["anatomy"]["archetype_id"] != "amalgam_walker":
            continue
        body = next(b for b in skeleton["branches"] if b["branch_id"] == "core")
        legs = [b for b in skeleton["branches"] if b["gait_role"] == "locomotor"]
        assert all(leg["length_m"] > body["girth_m"] for leg in legs), skeleton["skeleton_id"]
        assert all(leg["girth_m"] < .5 * leg["length_m"] * .22 * 2 for leg in legs)


def test_necked_amalgams_hang_their_heads_on_necks_and_optional_necks_have_optional_heads(compiled):
    necked = {sid: s for sid, s in compiled.items() if any(b["gait_role"] == "neck" for b in s["branches"])}
    assert necked, "no committed amalgam has a neck"
    for skeleton in necked.values():
        by_id = {b["branch_id"]: b for b in skeleton["branches"]}
        for neck in (b for b in skeleton["branches"] if b["gait_role"] == "neck"):
            assert neck["template"] == "neck2" and neck["binding_profile_id"] == "neck2_axial"
            assert neck["parent_branch"] == "core" and not neck.get("contacts")
            heads = [b for b in skeleton["branches"] if b["parent_branch"] == neck["branch_id"]]
            assert len(heads) == 1 and heads[0]["gait_role"] == "head"
            assert neck["required"] or not heads[0]["required"]           # a required head can't hang from an optional neck
        assert by_id["head"]["parent_branch"] == "neck"                       # the first head keeps the id the landmark names
        assert skeleton["anatomy"]["landmarks"]["neck"] == "head"


def test_a_limb_tip_head_hangs_from_a_vestigial_limb_and_is_optional(compiled):
    tipped = [s for s in compiled.values() if any(b["parent_branch"].startswith("flail") and b["gait_role"] == "head"
                                                  for b in s["branches"])]
    assert tipped
    for skeleton in tipped:
        head = next(b for b in skeleton["branches"] if b["gait_role"] == "head" and b["parent_branch"].startswith("flail"))
        assert head["required"] is False


def test_head_growth_leaves_the_seeds_it_does_not_name_exactly_as_they_were():
    old = {seed: json.dumps(amalgam.build_amalgam(seed), sort_keys=True)
           for seed in amalgam.AMALGAM_SEEDS if seed not in amalgam.HEADY_SEEDS}
    saved = dict(amalgam.HEADY_SEEDS)
    try:
        amalgam.HEADY_SEEDS.clear()
        for seed in old:
            assert json.dumps(amalgam.build_amalgam(seed), sort_keys=True) == old[seed]
    finally:
        amalgam.HEADY_SEEDS.update(saved)
    assert all(not any(b["gait"]["role"] == "neck" for b in json.loads(doc)["branches"]) for doc in old.values())


def test_a_hauling_arms_hand_has_its_own_profile_and_its_own_placeholder_fits(catalog):
    from critter_crafter.library.catalog import reference_part_id
    from critter_crafter.recipes.generator import part_accepted

    parts = {p["part_id"]: p for p in catalog["parts"]}
    hands = 0
    for skeleton in catalog["skeletons"]:
        for branch in skeleton["branches"]:
            assert part_accepted(parts[reference_part_id(skeleton["skeleton_id"], branch["branch_id"])], branch),                 (skeleton["skeleton_id"], branch["branch_id"])
            if branch["binding_profile_id"] == amalgam.HAND_PROFILE:
                hands += 1
                assert skeleton["skeleton_id"].startswith("amalgam_hauled") and branch["branch_id"].startswith("hand")
                assert branch["gait_role"] == "hand" and branch["template"] == "appendage1" and not branch.get("contacts")
                assert branch["parent_branch"].startswith("arm")
    assert hands


def test_a_hauling_arms_hand_lies_flat_just_above_the_wrist_at_the_neutral_pose(compiled):
    from critter_crafter.skeletons.amalgam import HAND_LIFT_M
    from critter_crafter.skeletons.kinematics import neutral_pose_world

    checked = 0
    for skeleton in compiled.values():
        pose = neutral_pose_world(skeleton)
        for branch in skeleton["branches"]:
            if branch["gait_role"] != "hand":
                continue
            arm = next(b for b in skeleton["branches"] if b["branch_id"] == branch["parent_branch"])
            wrist = pose[arm["bone_names"][-1]]["tail"]
            hand = pose[branch["bone_names"][0]]["head"]
            assert [round(h - w, 3) for h, w in zip(hand, wrist)] == [0.0, round(HAND_LIFT_M, 3), 0.0], skeleton["skeleton_id"]
            from critter_crafter import mathutil as mu
            rotation = pose[branch["bone_names"][0]]["rotation"]
            axis, dorsal = mu.quat_rotate(rotation, (0, 1, 0)), mu.quat_rotate(rotation, (0, 0, 1))
            assert abs(axis[1]) < 1e-4 and dorsal[1] == pytest.approx(1.0, abs=1e-4)      # flat, palm down
            assert arm["binding_profile_id"] != amalgam.HAND_PROFILE        # the arm keeps its ordinary parts
            checked += 1
    assert checked


def test_the_giant_hands_fit_every_hand_branch_and_nothing_else(catalog):
    from critter_crafter.recipes.generator import part_accepted

    hand_parts = [p for p in catalog["parts"] if p["part_id"].startswith("meshy_giant_hand_")]
    assert len(hand_parts) == 4
    branches = [(s["skeleton_id"], b) for s in catalog["skeletons"] for b in s["branches"]]
    hand_branches = [(sid, b) for sid, b in branches if b["gait_role"] == "hand"]
    assert hand_branches
    for part in hand_parts:
        assert all(part_accepted(part, b) for _, b in hand_branches), part["part_id"]
        assert not [sid for sid, b in branches if b["gait_role"] != "hand" and part_accepted(part, b)], part["part_id"]
