"""Optional sprouts (tails, dorsal spines, second arms, antennae, crowns) on the curated archetypes."""

from __future__ import annotations

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.skeletons.archetypes import generate_all
from critter_crafter.skeletons.kinematics import neutral_pose_world

SPROUTS = ("tail", "dorsal_", "arm2_", "antenna_", "crown")


def _sprouts(skeleton):
    return [b for b in skeleton["branches"] if b["branch_id"].startswith(SPROUTS)]


@pytest.fixture(scope="module")
def sources():
    return [s for s in generate_all() if s["family"] != "amalgam"]


@pytest.fixture(scope="module")
def compiled():
    catalog = compile_catalog(load_sources(paths().data))
    return {s["skeleton_id"]: s for s in catalog["skeletons"]}


def test_every_family_that_should_sprout_does(sources):
    by_plan: dict[str, set[str]] = {}
    for skeleton in sources:
        by_plan.setdefault(skeleton["anatomy"]["body_plan"], set()).update(b["branch_id"] for b in _sprouts(skeleton))
    assert {"tail", "dorsal_0"} <= by_plan["quadruped"]
    assert {"arm2_L", "arm2_R"} <= by_plan["biped"]
    assert {"antenna_L", "antenna_R", "dorsal_0"} <= by_plan["hexapod"]
    assert "crown" in by_plan["radial"]
    assert "crown" in by_plan["crawler"] and "dorsal_0" in by_plan["crawler"]
    assert "dragger" not in by_plan or not by_plan["dragger"]


def test_sprouts_are_optional_and_never_carry_weight_or_touch_the_ground(sources):
    for skeleton in sources:
        for branch in _sprouts(skeleton):
            assert branch["required"] is False, (skeleton["skeleton_id"], branch["branch_id"])
            assert 25 <= branch["optional_fill_pct"] <= 80
            assert branch["gait"]["role"] in ("sway", "manipulator")
            assert "contacts" not in branch
            assert branch["branch_id"] not in skeleton["anatomy"]["support_branches"]
            assert branch["parent_branch"] == "core"


def test_sprouts_stay_within_the_branch_and_bone_budgets(sources):
    for skeleton in sources:
        assert len(skeleton["branches"]) <= 14
        assert skeleton["anatomy"]["budgets"]["bones"] <= 100


def test_second_arms_and_antennae_are_mirrored_pairs(sources):
    for skeleton in sources:
        by_id = {b["branch_id"]: b for b in skeleton["branches"]}
        for left, right in (("arm2_L", "arm2_R"), ("antenna_L", "antenna_R")):
            if left in by_id:
                assert by_id[right]["mirror_of"] == left
                assert by_id[right]["optional_fill_pct"] == by_id[left]["optional_fill_pct"]


def test_no_sprout_starts_below_the_ground_or_hangs_into_it(compiled):
    for skeleton in compiled.values():
        if skeleton["family"] == "amalgam":
            continue
        pose = neutral_pose_world(skeleton)
        for branch in skeleton["branches"]:
            if not branch["branch_id"].startswith(SPROUTS):
                continue
            lowest = min(min(pose[n]["head"][1], pose[n]["tail"][1]) for n in branch["bone_names"]) - branch["girth_m"] * .5
            assert lowest > .02, (skeleton["skeleton_id"], branch["branch_id"], lowest)
