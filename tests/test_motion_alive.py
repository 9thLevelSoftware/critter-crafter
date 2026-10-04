"""Clips move the body while pinned feet hold their ground (no Blender: the planner only)."""

from __future__ import annotations

from copy import deepcopy
import math

import pytest

from critter_crafter.config import paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.skeletons import motion as m
from critter_crafter.skeletons.kinematics import contact_world, neutral_pose_world, pose_world
from critter_crafter import mathutil as mu

GAIT = {"hint": "crawl", "frequency_hz": 1.2, "amplitude_deg": 16, "bob_m": .005}


@pytest.fixture(scope="module")
def skeletons():
    return {s["skeleton_id"]: s for s in compile_catalog(load_sources(paths().data))["skeletons"]}


@pytest.fixture(scope="module")
def plans(skeletons):
    ids = ("quadruped_stocky_plantigrade_balanced_v3", "hexapod_elongated_insect_balanced_v3",
           "biped_plantigrade_humanoid_balanced_v3", "dragger_belly_hauler_balanced_v3",
           "serpentine_limbless_articulated_balanced_v3")
    return {sid: m.build_motion(skeletons[sid], GAIT) for sid in ids}


def _clip(plan, name):
    return next(c for c in plan["clips"] if c["name"] == name)


def test_torso_moves_in_idle_while_feet_are_pinned(skeletons, plans):
    sid = "quadruped_stocky_plantigrade_balanced_v3"
    idle = _clip(plans[sid], "idle")
    neutral = m.neutral_quaternions(skeletons[sid])
    moved = {bone for sample in idle["samples"] for bone, q in sample["rotations_xyzw"].items()
             if bone.startswith("core_b") and any(abs(a - b) > 1e-6 for a, b in zip(q, neutral[bone]))}
    assert moved == {"core_b0", "core_b1", "core_b2"}       # the whole spine, not a frozen one
    pinned = {c["branch_id"] for c in idle["samples"][5]["contacts"] if c["ik"] == "pin"}
    assert pinned == {"leg_L0", "leg_R0", "leg_L1", "leg_R1"}


def test_hips_never_stray_past_their_reach_budget(skeletons, plans):
    for sid, plan in plans.items():
        skeleton = skeletons[sid]
        neutral = neutral_pose_world(skeleton)
        legs = [b for b in skeleton["branches"] if m.is_pinned_locomotor(b, skeleton)]
        for clip in plan["clips"]:
            if clip["name"] in ("telegraph", "attack"):
                continue                                           # the attack effector is released
            if clip["name"] == "death":
                continue                # the collapse is meant to fold the legs; QA checks the folded pose in Blender
            for sample in clip["samples"]:
                rotations = {k: tuple(v) for k, v in sample["rotations_xyzw"].items()}
                pose = pose_world(skeleton, rotations, sample["root_position_m"])
                for leg in legs:
                    contact = leg["contacts"][0]
                    target = contact_world(neutral, leg, contact)
                    reach = sum(neutral[n]["length"] for n in leg["bone_names"][:contact["bone_index"] + 1])
                    d0 = mu.length(mu.sub(neutral[leg["bone_names"][0]]["head"], target))
                    stretch = abs(mu.length(mu.sub(pose[leg["bone_names"][0]]["head"], target)) - d0)
                    allowed = m.REACH_BUDGET * max(0.0, .95 * reach - d0)
                    assert stretch <= allowed + 2e-4, (sid, clip["name"], leg["branch_id"], stretch, allowed)


def test_legged_bodies_sink_when_they_die_and_dragged_ones_stay(skeletons, plans):
    for sid, plan in plans.items():
        heights = [s["root_position_m"][1] for s in _clip(plan, "death")["samples"]]
        drop = heights[0] - min(heights)
        if sid.startswith(("quadruped", "hexapod", "biped")):
            assert drop == pytest.approx(m.DEATH_DROP * skeletons[sid]["locomotion"]["hip_height_m"], rel=.02)
        else:
            assert drop == 0.0
        assert heights[-1] == pytest.approx(min(heights), abs=1e-9)   # it ends settled, not mid-fall


def test_pins_hold_through_death_but_the_support_is_released(plans):
    contacts = [c for s in _clip(plans["biped_plantigrade_humanoid_balanced_v3"], "death")["samples"]
                for c in s["contacts"] if c["branch_id"] == "leg_L"]
    assert {c["ik"] for c in contacts} == {"pin"}
    assert contacts[0]["support"] and not contacts[-1]["support"]


def test_only_the_bones_under_a_sliding_contact_are_frozen(skeletons):
    limbless = skeletons["serpentine_limbless_articulated_balanced_v3"]
    assert m._frozen_bones(limbless) == {"core_b0"}                   # the head the body hangs from
    biped = skeletons["biped_plantigrade_humanoid_balanced_v3"]
    assert m._frozen_bones(biped) == set()                            # planted feet are pinned, not frozen


def test_effector_ancestors_hold_still_while_the_attack_solver_works(skeletons, plans):
    sid = "quadruped_stocky_plantigrade_balanced_v3"
    effector = m.resolve_attack(skeletons[sid])["effector"]["branch_id"]
    ancestors = m._ancestor_bones(skeletons[sid], next(b for b in skeletons[sid]["branches"] if b["branch_id"] == effector))
    neutral = m.neutral_quaternions(skeletons[sid])
    for name in ("telegraph", "attack"):
        for sample in _clip(plans[sid], name)["samples"]:
            for bone in ancestors:
                assert sample["rotations_xyzw"][bone] == pytest.approx(list(neutral[bone]), abs=1e-7)


def test_hit_recoil_reaches_the_head_after_the_core(skeletons, plans):
    sid = "quadruped_stocky_plantigrade_balanced_v3"
    neutral = m.neutral_quaternions(skeletons[sid])
    samples = _clip(plans[sid], "hit")["samples"]

    def peak_frame(bone):
        return max(range(len(samples)), key=lambda i: sum(abs(a - b) for a, b in zip(samples[i]["rotations_xyzw"][bone], neutral[bone])))

    assert peak_frame("head_b0") > peak_frame("core_b0")


def test_loop_clips_still_close_after_the_new_gestures(plans):
    for plan in plans.values():
        for clip in plan["clips"]:
            if clip["loop"]:
                assert clip["samples"][0]["rotations_xyzw"] == clip["samples"][-1]["rotations_xyzw"]
                assert clip["samples"][0]["root_position_m"] == clip["samples"][-1]["root_position_m"]


@pytest.mark.parametrize("kind", ("body", "sliding"))
def test_dragger_body_contacts_stay_grounded_through_reactions(skeletons, plans, kind):
    sid = "dragger_belly_hauler_balanced_v3"
    skeleton = deepcopy(skeletons[sid])
    belly = next(b for b in skeleton["branches"] if b["branch_id"] == "belly")
    for contact in belly["contacts"]:
        contact["kind"] = kind
    plan = plans[sid] if kind == "body" else m.build_motion(skeleton, GAIT)
    expected_ids = {f"belly:{i}" for i in range(len(belly["contacts"]))}
    assert expected_ids == {"belly:0", "belly:1", "belly:2"}
    for name, stance in (("idle", 1.0), ("hit", 1.0), ("death", .25)):
        schedule = {c["contact_id"]: c for c in _clip(plan, name)["contact_schedule"]
                    if c["branch_id"] == "belly"}
        assert set(schedule) == expected_ids
        for contact in schedule.values():
            assert contact["kind"] == kind
            assert contact["support"] is True
            assert contact["grounding_declared"] is True
            assert contact["grounding_fraction"] == 1.0
            assert contact["stance_fraction"] == stance


def test_serpent_attack_releases_only_terminal_ground_contact(plans):
    plan = plans["serpentine_limbless_articulated_balanced_v3"]
    release = plan["attack_plan"]["support_release"]
    assert release["contact_ids"] == ["body:2"]
    assert set(release["preserved_contact_ids"]) == {"body:0", "body:1"}
    for name in ("idle", "telegraph", "attack"):
        schedule = {c["contact_id"]: c for c in _clip(plan, name)["contact_schedule"]}
        assert set(schedule) == {"body:0", "body:1", "body:2"}
        for contact_id, contact in schedule.items():
            assert contact["kind"] == "sliding"
            assert contact["support"] is True
            assert contact["grounding_declared"] is True
            assert contact["grounding_fraction"] == (
                0.0 if name != "idle" and contact_id == "body:2" else 1.0)
            assert contact["stance_fraction"] == 1.0


def test_foot_striker_releases_grounding_without_releasing_other_feet(plans):
    plan = plans["hexapod_elongated_insect_balanced_v3"]
    release = plan["attack_plan"]["support_release"]
    assert release["contact_ids"] == ["leg2_L:0"]
    preserved = {"leg0_L:0", "leg0_R:0", "leg1_L:0", "leg1_R:0", "leg2_R:0"}
    assert set(release["preserved_contact_ids"]) == preserved
    for name in ("idle", "telegraph", "attack"):
        schedule = {c["contact_id"]: c for c in _clip(plan, name)["contact_schedule"]}
        assert set(schedule) == preserved | {"leg2_L:0"}
        for contact_id, contact in schedule.items():
            assert contact["kind"] == "foot"
            assert contact["support"] is True
            assert contact["grounding_declared"] is True
            assert contact["grounding_fraction"] == (
                0.0 if name != "idle" and contact_id == "leg2_L:0" else 1.0)
            assert contact["stance_fraction"] == 1.0


@pytest.mark.parametrize("sid", (
    "biped_plantigrade_humanoid_balanced_v3",
    "quadruped_stocky_plantigrade_balanced_v3",
    "hexapod_elongated_insect_balanced_v3",
))
def test_death_foot_grounding_follows_authored_support_release(plans, sid):
    death = _clip(plans[sid], "death")
    release_phase = death["support_policy"]["death_release_phase"]
    assert release_phase == .25
    feet = [c for c in death["contact_schedule"] if c["kind"] == "foot"]
    assert {c["branch_id"] for c in feet} == {
        c["branch_id"] for c in death["samples"][0]["contacts"] if c["kind"] == "foot"}
    for contact in feet:
        assert contact["grounding_declared"] is True
        assert contact["grounding_fraction"] == release_phase
        assert contact["stance_fraction"] == release_phase
        for sample in death["samples"]:
            foot = next(c for c in sample["contacts"] if c["branch_id"] == contact["branch_id"])
            assert foot["support"] is (sample["phase"] < release_phase)
            assert foot["ik"] == "pin"
