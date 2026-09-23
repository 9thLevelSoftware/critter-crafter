import math

from critter_crafter.skeletons.motion import (
    ARCHETYPE_PROFILES, CLIP_ORDER, _gesture, build_motion, contact_trajectory, neutral_quaternions,
)


def _skeleton():
    bones = [
        {"name": "root", "parent": "", "head_m": [0, 0, 0], "tail_m": [0, .1, 0], "up_m": [0, 0, 1]},
        {"name": "leg_b0", "parent": "root", "head_m": [0, .8, 0], "tail_m": [0, .4, 0], "up_m": [0, 0, 1]},
        {"name": "leg_b1", "parent": "leg_b0", "head_m": [0, .4, 0], "tail_m": [0, 0, 0], "up_m": [0, 0, 1]},
    ]
    return {"skeleton_id": "biped_plantigrade_humanoid_balanced_v3", "family": "biped",
            "locomotion_hint": "biped", "motion_profile_id": "biped_plantigrade_humanoid",
            "bones": bones, "anatomy": {"support_branches": ["leg"]},
            "neutral_pose": {"root_offset_m": [0, 0, 0], "rotations": [
                {"bone_name": b["name"], "rotation_xyzw": [0, 0, 0, 1]} for b in bones]},
            "branches": [{"branch_id": "leg", "template": "limb2", "parent_branch": "",
                "bone_names": ["leg_b0", "leg_b1"], "length_m": .8, "gait_role": "locomotor",
                "gait_phase_rad": 0.0, "gait": {"role": "locomotor", "phase_rad": 0,
                    "support_phase": .62, "stride_m": .3, "clearance_m": .08, "cadence_hz": 1.6},
                "contacts": [{"kind": "foot", "bone_index": 1, "local_point_m": [0, .4, 0]}]}]}


def test_motion_archetypes_cover_every_family():
    assert len(ARCHETYPE_PROFILES) == 13
    families = [p["family"] for p in ARCHETYPE_PROFILES.values()]
    assert set(families) == {"biped", "quadruped", "hexapod", "crawler", "radial", "serpentine", "dragger"}
    # The tentacle radial was retired; the radial family keeps its articulated walker.
    assert all(families.count(family) == (1 if family == "radial" else 2) for family in set(families))


def test_contact_cycle_has_position_and_velocity_continuity():
    stride, clearance, support = .55, .12, .63
    eps = 1e-7
    for seam in (0.0, support):
        a = contact_trajectory(seam - eps, stride, clearance, support)
        b = contact_trajectory(seam + eps, stride, clearance, support)
        assert abs(a["forward_m"] - b["forward_m"]) < 1e-5
        assert abs(a["height_m"] - b["height_m"]) < 1e-5
        assert abs(a["forward_dphase"] - b["forward_dphase"]) < 1e-4
        assert abs(a["height_dphase"] - b["height_dphase"]) < 1e-4


def test_motion_plan_has_fixed_horizontal_root_contacts_and_metadata():
    skel = _skeleton()
    gait = {"hint": "biped", "frequency_hz": 1.8, "amplitude_deg": 24, "bob_m": .03}
    motion = build_motion(skel, gait)
    assert [c["name"] for c in motion["clips"]] == list(CLIP_ORDER)
    for clip in motion["clips"]:
        assert clip["fps"] == 30
        assert clip["duration_s"] == clip["frames"] / 30
        roots = [s["root_position_m"] for s in clip["samples"]]
        assert len({round(p[0], 9) for p in roots}) == 1
        assert len({round(p[2], 9) for p in roots}) == 1
        if clip["loop"]:
            assert clip["samples"][0]["rotations_xyzw"] == clip["samples"][-1]["rotations_xyzw"]
    for clip_name in ("walk", "run"):
        clip = next(c for c in motion["clips"] if c["name"] == clip_name)
        assert clip["speed_mps"] > 0
        assert all(s["contacts"] for s in clip["samples"])
        assert {c["kind"] for s in clip["samples"] for c in s["contacts"]} <= {"foot", "hand", "sliding", "body"}
        schedule = clip["contact_schedule"]
        assert schedule == [{"contact_id": "leg:0", "branch_id": "leg", "contact_index": 0,
                             "kind": "foot", "phase_offset": .25,
                             "stance_fraction": .92,
                             "support": True}]
        first = clip["samples"][0]["contacts"][0]
        assert first["phase"] == schedule[0]["phase_offset"]
        assert first["support_fraction"] == schedule[0]["stance_fraction"]


def test_neutral_quaternions_are_unit_local_deltas():
    skel = _skeleton()
    q = neutral_quaternions(skel)
    assert set(q) == {b["name"] for b in skel["bones"]}
    assert all(abs(math.sqrt(sum(x*x for x in v)) - 1.0) < 1e-8 for v in q.values())


def test_sliding_walk_wave_is_horizontal_and_readable():
    samples = [_gesture("walk", "sliding", 3, frame / 48, 0.0,
                        math.radians(24.0), 8) for frame in range(49)]
    assert all(abs(swing_x) < 1e-12 for swing_x, _ in samples)
    swing_z = [value for _, value in samples]
    assert math.degrees(max(swing_z) - min(swing_z)) >= 3.0
