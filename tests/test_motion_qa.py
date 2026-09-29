import copy
import math

from critter_crafter.skeletons.qa import evaluate_motion as _evaluate_motion

PROFILE = {"binding_profile_id": "test_leg", "binding_profile_version": "1.0.0",
           "joints": [{"limits_deg": {"swing_x": [-45, 45], "swing_y": [-45, 45], "twist": [-45, 45]}}]}


def evaluate_motion(skeleton, motion):
    return _evaluate_motion(skeleton, motion, profiles=[PROFILE])


def _rotation(clip, frame, count):
    """Loops swing 3 degrees on a sine (so the seam is smooth); telegraph ends where attack starts; the rest move 5."""
    if clip in ("idle", "walk", "run", "stun"):
        degrees = 3.0 * math.sin(math.tau * frame / count)
    else:
        degrees = {"telegraph": 5.0, "attack": 5.0}.get(clip, 5.0 if frame else 0.0)
    half = math.radians(degrees) / 2
    return [math.sin(half), 0, 0, math.cos(half)]


def make_motion():
    clips = []
    for name in ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"):
        samples = []
        count = 12 if name in ("idle", "walk", "run", "stun") else 3
        for frame in range(count + 1):
            z = -frame * .01 if name in ("walk", "run") else 0.0
            samples.append({"frame": frame, "root_position_m": [0, 0, 0],
                            "simulated_forward_m": -z,
                            "bones": [{"name": "leg_b0", "head_m": [0, 1, 0], "tail_m": [0, 0, 0],
                                       "rotation_xyzw": _rotation(name, frame, count)}],
                            "minimum_support": 0 if name == "death" and frame >= 1 else 1,
                            "airborne": False, "mesh_bounds_min_m": [-.1, 0, -.1], "mesh_bounds_max_m": [.1, 1, .1],
                            "contacts": [{"branch_id": "leg", "kind": "foot", "planted": True,
                                          "position_m": [0, 0, z], "target_m": [0, 0, z]}]})
        clips.append({"name": name, "frames": count, "fps": 30,
                      "loop": name in ("idle", "walk", "run", "stun"), "samples": samples})
    return {"skeleton_id": "test", "bones": [{"name": "leg_b0"}],
            "anatomy": {"support_branches": ["leg"]},
            "branches": [{"branch_id": "leg", "length_m": 1.0, "bone_names": ["leg_b0"],
                          "binding_profile_id": "test_leg", "binding_profile_version": "1.0.0",
                          "contacts": [{"kind": "foot"}]}]}, {"skeleton_id": "test", "clips": clips, "sample_source": "evaluated_blender"}


def test_qa_uses_actual_contact_positions_with_simulated_travel():
    skeleton, motion = make_motion()
    assert evaluate_motion(skeleton, motion)["passed"]
    motion["clips"][1]["samples"][2]["contacts"][0]["position_m"][2] += .03
    result = evaluate_motion(skeleton, motion)
    assert not result["passed"]
    assert any(d["code"] == "CC_CONTACT_DRIFT" for d in result["diagnostics"])


def test_qa_rejects_penetration_missing_samples_and_root_motion():
    skeleton, motion = make_motion()
    motion["clips"][0]["samples"][1]["contacts"][0]["position_m"][1] = -.006
    motion["clips"][2]["samples"].pop(1)
    motion["clips"][3]["samples"][1]["root_position_m"][0] = .01
    codes = {d["code"] for d in evaluate_motion(skeleton, motion)["diagnostics"]}
    assert {"CC_GROUND_PENETRATION", "CC_MOTION_SAMPLES", "CC_ROOT_MOTION"} <= codes


def test_qa_does_not_certify_ideal_targets_as_baked_motion():
    skeleton, motion = make_motion()
    motion["sample_source"] = "planner"
    assert not evaluate_motion(skeleton, motion)["passed"]


def test_qa_rejects_discontinuous_telegraph_attack():
    skeleton, motion = make_motion()
    motion["clips"][5]["samples"][0]["bones"][0]["head_m"][0] = .1
    assert any(d["code"] == "CC_ATTACK_TRANSITION" for d in evaluate_motion(skeleton, motion)["diagnostics"])


def test_empty_clips_cannot_self_certify():
    skeleton, motion = make_motion()
    for clip in motion["clips"]:
        clip.update(frames=-1, samples=[])
    report = evaluate_motion(skeleton, motion)
    assert not report["passed"]
    assert report["samples_checked"] == 0


def test_support_and_limits_cannot_be_removed_to_pass_qa():
    skeleton, motion = make_motion()
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            sample.update(contacts=[], minimum_support=0, airborne=True)
    report = _evaluate_motion(skeleton, motion)
    assert not report["passed"]
    assert {"CC_MOTION_PROFILE", "CC_CONTACT_SET", "CC_SUPPORT_SCHEDULE"} <= {d["code"] for d in report["diagnostics"]}


def test_non_finite_non_loop_motion_and_wrong_identity_are_rejected():
    skeleton, motion = make_motion()
    motion["skeleton_id"] = "other"
    motion["clips"][-1]["samples"][2]["bones"][0]["head_m"][0] = float("nan")
    codes = {d["code"] for d in evaluate_motion(skeleton, motion)["diagnostics"]}
    assert {"CC_MOTION_ID", "CC_MOTION_FINITE"} <= codes


def test_self_labelled_planted_contacts_cannot_float():
    skeleton, motion = make_motion()
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            sample["contacts"][0]["position_m"][1] = 1
            sample["contacts"][0]["target_m"][1] = 1
    assert "CC_CONTACT_GROUND" in {d["code"] for d in evaluate_motion(skeleton, motion)["diagnostics"]}


def test_axial_twist_seam_is_visible_even_when_bone_endpoints_match():
    skeleton, motion = make_motion()
    motion["clips"][0]["samples"][-1]["bones"][0]["rotation_xyzw"] = [0, math.sin(math.radians(20)), 0, math.cos(math.radians(20))]
    assert "CC_LOOP_ROTATION" in {d["code"] for d in evaluate_motion(skeleton, motion)["diagnostics"]}


def test_multiple_contacts_on_one_leg_do_not_substitute_other_supporting_limbs():
    skeleton, motion = make_motion()
    skeleton["family"] = "quadruped"
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            sample["contacts"].append(copy.deepcopy(sample["contacts"][0]))
    assert "CC_SUPPORT_SCHEDULE" in {d["code"] for d in evaluate_motion(skeleton, motion)["diagnostics"]}


def test_qa_flags_a_clip_in_which_nothing_moves():
    skeleton, motion = make_motion()
    for sample in motion["clips"][0]["samples"]:                       # idle
        sample["bones"][0]["rotation_xyzw"] = [0, 0, 0, 1]
    result = evaluate_motion(skeleton, motion)
    still = [d for d in result["diagnostics"] if d["code"] == "CC_MOTION_STILL"]
    assert [d["clip"] for d in still] == ["idle"]


def test_qa_wants_a_legged_body_to_sink_when_it_dies():
    skeleton, motion = make_motion()
    skeleton["locomotion"] = {"mode": "legs", "hip_height_m": 1.0}
    assert any(d["code"] == "CC_DEATH_DROP" for d in evaluate_motion(skeleton, motion)["diagnostics"])
    for sample in motion["clips"][7]["samples"]:                       # death
        sample["root_position_m"] = [0, -.2 * sample["frame"] / 3, 0]
    assert not any(d["code"] == "CC_DEATH_DROP" for d in evaluate_motion(skeleton, motion)["diagnostics"])
