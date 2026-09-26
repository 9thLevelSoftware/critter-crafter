"""Curated v3 anatomy candidates remain deterministic and anatomically usable."""

from __future__ import annotations

import math
import json
from pathlib import Path

import pytest

from critter_crafter.skeletons.archetypes import (
    ARCHETYPES,
    PRESETS,
    _FRACTIONS,
    _PROFILE_FRACTIONS,
    _contact_height,
    _contact_point_world,
    build_candidate,
    generate_all,
    generate_extras,
    write_profiles,
)
from critter_crafter import mathutil as mu


def _branch(doc: dict, branch_id: str) -> dict:
    return next(branch for branch in doc["branches"] if branch["branch_id"] == branch_id)


def test_biped_candidate_has_grounded_symmetric_support_and_motion_metadata() -> None:
    doc = build_candidate("biped_plantigrade_humanoid", "balanced", seed=1)

    assert doc["schema_version"] == "3.0.0"
    assert doc["skeleton_id"] == "biped_plantigrade_humanoid_balanced_v3"
    assert doc["anatomy"]["support_branches"] == ["leg_L", "leg_R"]
    # Legs are sized so the neutral feet meet the ground with the hip at its designed height.
    assert abs(doc["neutral_pose"]["root_offset_m"][1]) < .01
    left, right = _branch(doc, "leg_L"), _branch(doc, "leg_R")
    assert right["mirror_of"] == "leg_L"
    assert left["gait"]["support_phase"] == right["gait"]["support_phase"]
    assert left["contacts"][0]["kind"] == right["contacts"][0]["kind"] == "foot"
    assert left["contacts"][0]["local_point_m"][1] > 0
    assert math.isclose(left["origin_m"][0], -right["origin_m"][0])


def test_all_curated_candidates_are_reproducible_distinct_and_within_budget() -> None:
    candidates = generate_all(seed=9)

    assert len(ARCHETYPES) == 13
    assert PRESETS == ("compact", "balanced", "elongated")
    assert len(candidates) == 39
    assert candidates == generate_all(seed=9)
    assert len({candidate["skeleton_id"] for candidate in candidates}) == 39
    silhouettes = {
        (round(candidate["anatomy"]["silhouette"]["width_m"], 3),
         round(candidate["anatomy"]["silhouette"]["height_m"], 3),
         round(candidate["anatomy"]["silhouette"]["length_m"], 3))
        for candidate in candidates
    }
    assert len(silhouettes) > 20
    for candidate in candidates:
        budgets = candidate["anatomy"]["budgets"]
        assert budgets["bones"] <= 120
        assert budgets["parts"] <= 16
        assert budgets["triangles"] <= 30_000
        assert candidate["anatomy"]["support_branches"]
        assert len(candidate["neutral_pose"]["rotations"]) == candidate["anatomy"]["budgets"]["bones"]
        for branch in candidate["branches"]:
            assert branch["binding_profile_id"]
            assert branch["binding_profile_version"] == "1.0.0"
            assert len(branch["socket"]["rotation_xyzw"]) == 4
            for contact in branch.get("contacts", []):
                assert contact["kind"] in {"foot", "hand", "sliding", "body"}
                assert contact["bone_index"] >= 0


def test_horror_style_is_an_optional_modifier_not_the_anatomical_default() -> None:
    anatomical = build_candidate("crawler_bilateral_eight_legged", "balanced", seed=3)
    horror = build_candidate("crawler_bilateral_eight_legged", "balanced", seed=3, style="horror")

    assert anatomical["anatomy"]["style"] == "anatomical"
    assert horror["anatomy"]["style"] == "horror"
    assert anatomical["skeleton_id"] != horror["skeleton_id"]
    assert [branch["branch_id"] for branch in anatomical["branches"]] == [branch["branch_id"] for branch in horror["branches"]]
    assert anatomical["branches"] != horror["branches"]


def test_write_profiles_emits_the_exact_profiles_referenced_by_candidates(tmp_path) -> None:
    written = write_profiles(tmp_path)

    referenced = {branch["binding_profile_id"] for candidate in generate_all() for branch in candidate["branches"]}
    assert {path.stem.removesuffix(".binding") for path in written} == referenced


def _is_extras_id(skeleton_id: str) -> bool:
    return skeleton_id.endswith(("_extras_v3", "_armed_v3", "_finned_v3"))


def test_committed_v3_sources_are_the_seed_one_candidate_set() -> None:
    source_dir = Path(__file__).parents[1] / "data" / "skeletons"
    committed = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(source_dir.glob("*_v3.skeleton.json"))
        if not _is_extras_id(path.name.replace(".skeleton.json", ""))
    ]

    assert committed == generate_all(seed=1)


def test_sockets_are_unit_relative_rotations_and_radial_plans_declare_ring_counts() -> None:
    for candidate in generate_all():
        symmetry = candidate["anatomy"]["symmetry"]
        if candidate["family"] == "radial":
            assert symmetry["kind"] == "radial"
            assert symmetry["ring_count"] in {6, 8}
        for branch in candidate["branches"]:
            quaternion = branch["socket"]["rotation_xyzw"]
            assert math.isclose(sum(value * value for value in quaternion), 1.0, abs_tol=1e-6)


def test_each_candidate_has_a_profile_valid_nonstraight_neutral_pose() -> None:
    for candidate in generate_all():
        rotations = {item["bone_name"]: item["rotation_xyzw"] for item in candidate["neutral_pose"]["rotations"]}
        branches = {branch["branch_id"]: branch for branch in candidate["branches"]}
        for branch_id in candidate["anatomy"]["support_branches"]:
            branch = branches[branch_id]
            bone_names = [f"{branch_id}_b{i}" for i in range(len(branch["stance_deg"]))]
            assert any(rotations[name] != [0.0, 0.0, 0.0, 1.0] for name in bone_names)


def _qmul(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def test_socket_rotations_reconstruct_each_branch_bind_forward_frame() -> None:
    for candidate in generate_all():
        world_rotations: dict[str, list[float]] = {}
        for branch in candidate["branches"]:
            local = branch["socket"]["rotation_xyzw"]
            parent = branch["parent_branch"]
            world = local if parent is None else _qmul(world_rotations[parent], local)
            world_rotations[branch["branch_id"]] = world
            expected = mu.frame_from_dir_up(branch["direction"], branch["up"])[2]
            actual = mu.quat_rotate(world, [0.0, 0.0, 1.0])
            assert all(math.isclose(actual[i], expected[i], abs_tol=2e-6) for i in range(3))


def test_neutral_x_swings_stay_within_each_profile_limit() -> None:
    profile_dir = Path(__file__).parents[1] / "data" / "binding_profiles"
    profiles = {profile["binding_profile_id"]: profile for path in profile_dir.glob("*.binding.json")
                for profile in [json.loads(path.read_text(encoding="utf-8"))]}
    for candidate in generate_all():
        rotations = {item["bone_name"]: item["rotation_xyzw"] for item in candidate["neutral_pose"]["rotations"]}
        for branch in candidate["branches"]:
            for index, joint in enumerate(profiles[branch["binding_profile_id"]]["joints"]):
                quat = rotations[f"{branch['branch_id']}_b{index}"]
                # Profile +X uses canonical +Z length; Blender local X is its inverse.
                swing_x = -math.degrees(2 * math.atan2(quat[0], quat[3]))
                lo, hi = joint["limits_deg"]["swing_x"]
                assert lo <= swing_x <= hi


def test_declared_support_contacts_are_grounded_by_the_neutral_root_offset() -> None:
    for candidate in generate_all():
        rotations = {item["bone_name"]: item["rotation_xyzw"] for item in candidate["neutral_pose"]["rotations"]}
        branches = {branch["branch_id"]: branch for branch in candidate["branches"]}
        root_y = candidate["neutral_pose"]["root_offset_m"][1]
        clearances: list[tuple[float, float]] = []
        for branch_id in candidate["anatomy"]["support_branches"]:
            branch = branches[branch_id]
            bone_count = {"core1": 1, "spine3": 3, "limb3": 3, "insect_leg4": 4, "tentacle8": 8, "head1": 1, "appendage1": 1}[branch["template"]]
            angles = [math.degrees(2 * math.atan2(rotations[f"{branch_id}_b{i}"][0], rotations[f"{branch_id}_b{i}"][3]))
                      for i in range(bone_count)]
            z_angles = branch["stance_z_deg"]
            for contact in branch["contacts"]:
                target = 0.0 if contact["kind"] in {"sliding", "body"} else 7.5e-3
                clearances.append((root_y + _contact_height(branch, angles, contact, z_angles), target))
        assert all(height >= target - 2e-4 for height, target in clearances)
        assert any(math.isclose(height, target, abs_tol=2e-4) for height, target in clearances)


def test_support_chain_joint_endpoints_do_not_penetrate_the_ground_plane() -> None:
    profile_dir = Path(__file__).parents[1] / "data" / "binding_profiles"
    profiles = {profile["binding_profile_id"]: profile for path in profile_dir.glob("*.binding.json")
                for profile in [json.loads(path.read_text(encoding="utf-8"))]}
    for candidate in generate_all():
        branches = {branch["branch_id"]: branch for branch in candidate["branches"]}
        root_y = candidate["neutral_pose"]["root_offset_m"][1]
        for branch_id in candidate["anatomy"]["support_branches"]:
            branch = branches[branch_id]
            fractions = profiles[branch["binding_profile_id"]]["bone_fractions"]
            for index, fraction in enumerate(fractions):
                endpoint = {"kind": "body", "bone_index": index,
                            "local_point_m": [0.0, round(branch["length_m"] * fraction, 4), 0.0]}
                height = _contact_height(branch, branch["stance_deg"], endpoint, branch["stance_z_deg"])
                assert root_y + height >= -5e-3


def test_sliding_and_body_support_is_distributed_across_each_chain() -> None:
    for candidate in generate_all():
        for branch in candidate["branches"]:
            contacts = branch.get("contacts", [])
            kinds = {contact["kind"] for contact in contacts}
            if not kinds.intersection({"sliding", "body"}):
                continue
            indices = [contact["bone_index"] for contact in contacts]
            fractions = {
                "core1_body": (1.0,), "spine3_axial": (.34, .33, .33),
                "limb3_plantigrade": (.45, .45, .10), "limb3_digitigrade": (.35, .45, .20),
                "insect_leg4_articulated": (.20, .35, .35, .10), "tentacle8_flexible": (.125,) * 8,
                "head1_neck": (1.0,), "appendage1_terminal": (1.0,),
            }[branch["binding_profile_id"]]
            assert len(indices) >= 3
            assert len(indices) == len(set(indices))
            assert min(indices) < len(fractions) - 1
            for contact in contacts:
                index = contact["bone_index"]
                segment_length = branch["length_m"] * fractions[index]
                radius = max(.018, min(segment_length * .32, branch["girth_m"] * .5))
                assert contact["local_point_m"] == [0.0, round(segment_length, 4), -round(radius, 4)]


def test_ventral_body_contacts_keep_the_reference_volume_above_ground() -> None:
    for candidate in generate_all():
        rotations = {item["bone_name"]: item["rotation_xyzw"] for item in candidate["neutral_pose"]["rotations"]}
        branches = {branch["branch_id"]: branch for branch in candidate["branches"]}
        underside_heights: list[float] = []
        for branch_id in candidate["anatomy"]["support_branches"]:
            branch = branches[branch_id]
            if not branch.get("contacts") or branch["contacts"][0]["kind"] not in {"sliding", "body"}:
                continue
            angles = [math.degrees(2 * math.atan2(rotations[f"{branch_id}_b{i}"][0], rotations[f"{branch_id}_b{i}"][3]))
                      for i in range(len(branch["stance_deg"]))]
            underside_heights.extend(candidate["neutral_pose"]["root_offset_m"][1]
                                     + _contact_height(branch, angles, contact, branch["stance_z_deg"])
                                     for contact in branch["contacts"])
        if underside_heights:
            assert min(underside_heights) >= -2e-4
            if all(branches[branch_id]["contacts"][0]["kind"] in {"sliding", "body"}
                   for branch_id in candidate["anatomy"]["support_branches"]):
                assert math.isclose(min(underside_heights), 0.0, abs_tol=2e-4)


def test_dragger_mixed_supports_keep_belly_on_ground_and_hands_above_it() -> None:
    for archetype in ("dragger_forelimb_puller", "dragger_belly_hauler"):
        for preset in PRESETS:
            candidate = build_candidate(archetype, preset, seed=1)
            rotations = {item["bone_name"]: item["rotation_xyzw"] for item in candidate["neutral_pose"]["rotations"]}
            root_y = candidate["neutral_pose"]["root_offset_m"][1]
            for branch in candidate["branches"]:
                if branch["branch_id"] not in candidate["anatomy"]["support_branches"]:
                    continue
                angles = [math.degrees(2 * math.atan2(rotations[f"{branch['branch_id']}_b{i}"][0], rotations[f"{branch['branch_id']}_b{i}"][3]))
                          for i in range(len(branch["stance_deg"]))]
                for contact in branch["contacts"]:
                    height = root_y + _contact_height(branch, angles, contact, branch["stance_z_deg"])
                    expected = {"body": 0.0, "hand": 1.75e-2}.get(contact["kind"], 7.5e-3)
                    assert math.isclose(height, expected, abs_tol=1e-4)


def test_draggers_rest_their_torso_on_the_ground_and_reach_forward() -> None:
    for preset in PRESETS:
        hauler = build_candidate("dragger_belly_hauler", preset, seed=1)
        puller = build_candidate("dragger_forelimb_puller", preset, seed=1)
        for doc in (hauler, puller):
            core, arm, belly = _branch(doc, "core"), _branch(doc, "arm_L"), _branch(doc, "belly")
            root_y = doc["neutral_pose"]["root_offset_m"][1]
            # Torso underside on the ground (within a centimetre), shoulders low on its flanks.
            underside = core["origin_m"][1] + root_y - core["girth_m"] * .5
            assert abs(underside) < .01, (doc["skeleton_id"], underside)
            # Shoulders at chest height: level with the torso, not propped above it.
            assert arm["origin_m"][1] + root_y < core["girth_m"] * .7
            # The hand plants ahead of the shoulder.
            hand = _contact_point_world(arm, arm["stance_deg"], arm["contacts"][0], arm["stance_z_deg"])
            assert hand[2] > arm["origin_m"][2] + .3 * arm["length_m"]
            assert doc["anatomy"]["support_branches"] == ["arm_L", "arm_R", "belly"]
            assert [contact["bone_index"] for contact in belly["contacts"]] == [1, 4, 7]
        assert _branch(puller, "arm_L")["length_m"] / puller["anatomy"]["silhouette"]["length_m"] > \
            _branch(hauler, "arm_L")["length_m"] / hauler["anatomy"]["silhouette"]["length_m"]


def test_branch_semantics_and_girth_match_the_exact_binding_profile() -> None:
    profile_dir = Path(__file__).parents[1] / "data" / "binding_profiles"
    profiles = {profile["binding_profile_id"]: profile for path in profile_dir.glob("*.binding.json")
                for profile in [json.loads(path.read_text(encoding="utf-8"))]}
    for candidate in generate_all():
        for branch in candidate["branches"]:
            profile = profiles[branch["binding_profile_id"]]
            role = branch["gait"]["role"]
            expected_category = (
                "core" if role == "core" else
                "head" if role == "head" else
                "tail" if branch["branch_id"] == "body" else
                "appendage" if branch["template"] == "tentacle8" else
                "limb"
            )
            assert branch["accepts"] == {"categories": [expected_category], "templates": [branch["template"]]}
            assert math.isclose(branch["girth_m"], branch["length_m"] * profile["girth_ratio"], abs_tol=1e-4)


def test_presets_change_correlated_proportions_and_neutral_stance() -> None:
    candidates = {
        preset: build_candidate("quadruped_stocky_plantigrade", preset, seed=5)
        for preset in PRESETS
    }
    aspect = {preset: doc["anatomy"]["silhouette"]["width_m"] / doc["anatomy"]["silhouette"]["length_m"]
              for preset, doc in candidates.items()}
    relative_leg = {preset: _branch(doc, "leg_L0")["length_m"] / doc["anatomy"]["silhouette"]["height_m"]
                    for preset, doc in candidates.items()}
    assert aspect["compact"] > aspect["balanced"] > aspect["elongated"]
    assert relative_leg["compact"] < relative_leg["balanced"] < relative_leg["elongated"]
    assert len({json.dumps(doc["neutral_pose"]["rotations"], sort_keys=True) for doc in candidates.values()}) == 3


def test_radial_footprints_remain_circular_in_every_preset() -> None:
    for archetype in ("radial_raised_articulated_walker",):
        for preset in PRESETS:
            candidate = build_candidate(archetype, preset, seed=7)
            silhouette = candidate["anatomy"]["silhouette"]
            assert math.isclose(silhouette["width_m"], silhouette["length_m"], abs_tol=1e-4)
            radii = [math.hypot(branch["origin_m"][0], branch["origin_m"][2])
                     for branch in candidate["branches"] if branch["branch_id"].startswith("arm_")]
            assert max(radii) - min(radii) <= 2e-4


def test_locomotion_hints_describe_the_actual_body_plan() -> None:
    for candidate in generate_all():
        if candidate["family"] == "quadruped":
            assert candidate["locomotion_hint"] == "quadruped"


def test_biped_head_and_shoulders_form_a_connected_torso_envelope() -> None:
    for archetype in ("biped_plantigrade_humanoid", "biped_digitigrade_creature"):
        for preset in PRESETS:
            candidate = build_candidate(archetype, preset, seed=1)
            core = _branch(candidate, "core")
            head = _branch(candidate, "head")
            torso_tail = [core["origin_m"][i] + mu.normalize(core["direction"])[i] * core["length_m"]
                          for i in range(3)]
            assert math.dist(head["origin_m"], torso_tail) <= 5e-3
            for arm_id in ("arm_L", "arm_R"):
                arm = _branch(candidate, arm_id)
                shoulder_gap = abs(arm["origin_m"][0] - core["origin_m"][0])
                touching_radius = (core["girth_m"] + arm["girth_m"]) / 2
                assert shoulder_gap <= touching_radius + 5e-3


def test_generate_extras_is_forty_one_unique_anatomical_drafts() -> None:
    extras = generate_extras(seed=1)

    assert len(extras) == 41
    assert extras == generate_extras(seed=1)
    ids = [candidate["skeleton_id"] for candidate in extras]
    assert len(set(ids)) == 41
    assert all(candidate["status"] == "draft" for candidate in extras)
    assert all(candidate["anatomy"]["style"] == "anatomical" for candidate in extras)
    assert sum(sid.endswith("_extras_v3") for sid in ids) == 39
    assert "biped_plantigrade_humanoid_balanced_armed_v3" in ids
    assert "serpentine_limbless_articulated_balanced_finned_v3" in ids
    assert generate_all(seed=1) == generate_all(seed=1)
    assert len(generate_all(seed=1)) == 39
    assert not ({candidate["skeleton_id"] for candidate in generate_all(seed=1)} & set(ids))


def test_committed_extras_match_generate_extras() -> None:
    source_dir = Path(__file__).parents[1] / "data" / "skeletons"
    extras = generate_extras(seed=1)
    committed = [
        json.loads((source_dir / f"{candidate['skeleton_id']}.skeleton.json").read_text(encoding="utf-8"))
        for candidate in extras
    ]
    assert committed == extras


def test_extras_are_optional_non_locomotor_and_fill_at_100() -> None:
    for candidate in generate_extras():
        extras = [branch for branch in candidate["branches"] if not branch["required"]]
        assert extras
        support = set(candidate["anatomy"]["support_branches"])
        contacts = set(candidate["anatomy"]["contact_branches"])
        for branch in extras:
            assert branch["optional_fill_pct"] == 100
            assert branch["gait"]["role"] != "locomotor"
            assert not branch.get("contacts")
            assert branch["branch_id"] not in support
            assert branch["branch_id"] not in contacts
        if candidate["family"] == "serpentine":
            assert all(branch["parent_branch"] == "body" for branch in extras)
        if candidate["skeleton_id"].endswith("_armed_v3"):
            assert {branch["branch_id"] for branch in extras} == {"arm2_L", "arm2_R"}
        if candidate["skeleton_id"].endswith("_finned_v3"):
            assert {branch["branch_id"] for branch in extras} == {"fin_L", "fin_R"}


def test_extra_arms_share_primary_arm_origin_height() -> None:
    for candidate in generate_extras():
        ids = {branch["branch_id"] for branch in candidate["branches"]}
        if "arm2_L" not in ids:
            continue
        arm = _branch(candidate, "arm_L")
        extra = _branch(candidate, "arm2_L")
        assert extra["origin_m"][1] == arm["origin_m"][1]
        assert extra["length_m"] == arm["length_m"]
        assert extra["attach_bone_index"] == 2


def test_extras_last_bone_tips_stay_above_ground() -> None:
    for candidate in generate_extras():
        root_y = candidate["neutral_pose"]["root_offset_m"][1]
        for branch in candidate["branches"]:
            if branch["required"]:
                continue
            n = _FRACTIONS[branch["template"]]
            last_fraction = _PROFILE_FRACTIONS[branch["binding_profile_id"]][-1]
            tip = {
                "kind": "body",
                "bone_index": n - 1,
                "local_point_m": [0.0, branch["length_m"] * last_fraction, 0.0],
            }
            point = _contact_point_world(branch, branch["stance_deg"], tip, branch["stance_z_deg"])
            assert point[1] + root_y >= -0.005, (
                candidate["skeleton_id"], branch["branch_id"], point[1] + root_y
            )


def test_horror_plus_extras_raises() -> None:
    with pytest.raises(ValueError, match="horror"):
        build_candidate("biped_plantigrade_humanoid", "balanced", seed=1, style="horror", extras=True)
