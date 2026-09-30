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
