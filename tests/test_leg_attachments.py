"""Legs hang off the spine bone that carries them, not all off one bone."""

from __future__ import annotations

import pytest

from critter_crafter.skeletons.archetypes import generate_all
from critter_crafter.skeletons.kinematics import neutral_pose_world
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.config import paths


@pytest.fixture(scope="module")
def compiled():
    catalog = compile_catalog(load_sources(paths().data))
    return {s["skeleton_id"]: s for s in catalog["skeletons"]}


def _sources(family):
    return [s for s in generate_all() if s["family"] == family]


def test_quadruped_hips_sit_on_the_pelvis_and_shoulders_on_the_chest():
    for skeleton in _sources("quadruped"):
        joints = {b["branch_id"]: b["socket"]["parent_joint"] for b in skeleton["branches"]}
        assert joints["leg_L1"] == joints["leg_R1"] == "pelvis"
        assert joints["leg_L0"] == joints["leg_R0"] == "end"


def test_hexapod_leg_pairs_use_all_three_spine_bones():
    for skeleton in _sources("hexapod"):
        joints = {b["branch_id"]: b["socket"]["parent_joint"] for b in skeleton["branches"]}
        assert [joints[f"leg{i}_L"] for i in range(3)] == ["pelvis", "lower", "end"]
        assert all(joints[f"leg{i}_L"] == joints[f"leg{i}_R"] for i in range(3))


@pytest.mark.parametrize("family", ["quadruped", "hexapod"])
def test_reattached_hips_stay_where_they_were_authored(family, compiled):
    """Moving a leg to a different parent bone must not move it: hip heads equal the authored origins."""
    for source in _sources(family):
        skeleton = compiled[source["skeleton_id"]]
        pose = neutral_pose_world(skeleton)
        offset = skeleton["neutral_pose"]["root_offset_m"]
        for branch in source["branches"]:
            if branch["gait"]["role"] != "locomotor":
                continue
            head = pose[f"{branch['branch_id']}_b0"]["head"]
            expected = [branch["origin_m"][0] + offset[0], branch["origin_m"][1] + offset[1], branch["origin_m"][2] + offset[2]]
            assert all(abs(a - b) < 2e-3 for a, b in zip(head, expected)), (source["skeleton_id"], branch["branch_id"])
