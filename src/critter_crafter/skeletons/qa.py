"""Independent QA of evaluated deformation-bone/contact samples, in metres."""
from __future__ import annotations

import math
from typing import Any

CLIPS = ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death")
LOOPS = frozenset(("idle", "walk", "run", "stun"))


def _vector(value, size=3) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == size
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in value))


def verification_version() -> str:
    from hashlib import sha256
    from pathlib import Path
    package = Path(__file__).resolve().parents[1]
    files = ("skeletons/qa.py", "skeletons/action_qa.py", "skeletons/actions.py", "skeletons/commands.py",
             "skeletons/review.py", "library/export_validation.py")
    return sha256(b"".join(name.encode() + (package / name).read_bytes() for name in files)).hexdigest()


def _distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _rotation_delta(a, b) -> list[float]:
    """Shortest quaternion delta as an axis-angle vector in degrees."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    q = [aw*bx-ax*bw-ay*bz+az*by, aw*by+ax*bz-ay*bw-az*bx,
         aw*bz-ax*by+ay*bx-az*bw, aw*bw+ax*bx+ay*by+az*bz]
    if q[3] < 0:
        q = [-v for v in q]
    sine = math.sqrt(sum(v*v for v in q[:3]))
    if sine < 1e-10:
        return [0., 0., 0.]
    angle = math.degrees(2*math.atan2(sine, q[3]))
    return [v * angle / sine for v in q[:3]]


def _bones(sample: dict) -> dict:
    value = sample.get("bones", [])
    return value if isinstance(value, dict) else {b["name"]: b for b in value}


def evaluate_motion(skeleton: dict, motion: dict, penetration_m: float = .005,
                    profiles: list[dict] | None = None) -> dict[str, Any]:
    diagnostics: list[dict] = []
    metrics = {"max_planted_drift_m": 0.0, "max_penetration_m": 0.0,
               "max_loop_position_error_m": 0.0, "max_loop_velocity_error_mps": 0.0,
               "max_contact_target_error_m": 0.0}

    def fail(code: str, detail: str, clip: str = "", frame: int | None = None):
        diagnostics.append({"code": code, "clip": clip, "frame": frame, "detail": detail})

    if motion.get("sample_source") != "evaluated_blender":
        fail("CC_MOTION_NOT_BAKED", "QA requires sampled built deformation bones")
    if motion.get("skeleton_id") != skeleton["skeleton_id"]:
        fail("CC_MOTION_ID", "motion belongs to a different skeleton")
    clips = {c["name"]: c for c in motion.get("clips", [])}
    if set(clips) != set(CLIPS) or len(motion.get("clips", [])) != len(CLIPS):
        fail("CC_CLIP_SET", f"expected {', '.join(CLIPS)}")
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    profile_map = {(p["binding_profile_id"], p["binding_profile_version"]): p for p in profiles or []}
    limits = {}
    for branch in branches.values():
        profile = profile_map.get((branch.get("binding_profile_id"), branch.get("binding_profile_version")))
        if profile:
            for name, joint in zip(branch["bone_names"], profile["joints"]):
                limits[name] = joint["limits_deg"]
        else:
            fail("CC_MOTION_PROFILE", f"{branch['branch_id']}: missing verified joint limits")
    expected_bones = {b["name"] for b in skeleton["bones"]}
    required_support = set(skeleton.get("anatomy", {}).get("support_branches", []))
    anatomical_minimum = 2 if skeleton.get("family") in ("quadruped", "hexapod", "crawler", "radial") else 1
    declared_contacts = {f"{b['branch_id']}:{i}": (b['branch_id'], c['kind'])
                         for b in branches.values() for i, c in enumerate(b.get("contacts", []))}
    if len(required_support) < anatomical_minimum or not declared_contacts:
        fail("CC_SUPPORT_METADATA", "anatomy must declare supporting branches and contacts")
    for name, clip in clips.items():
        samples = clip.get("samples", [])
        fps, frames = clip.get("fps", 0), clip.get("frames", -1)
        if (not isinstance(frames, int) or frames < 3 or fps != 30 or not samples
                or [s.get("frame") for s in samples] != list(range(max(0, frames) + 1))):
            fail("CC_MOTION_SAMPLES", "complete consecutive samples at 30 FPS required", name)
        if clip.get("loop") is not (name in LOOPS):
            fail("CC_CLIP_LOOP", "incorrect loop declaration", name)
        if not samples:
            continue
        first_root = samples[0].get("root_position_m", [0, 0, 0])
        planted: dict[str, list] = {}
        for sample in samples:
            frame = sample["frame"]
            root = sample.get("root_position_m", [0, 0, 0])
            if not _vector(root) or not _vector(first_root):
                fail("CC_MOTION_FINITE", "invalid root position", name, frame)
                continue
            if max(abs(root[axis] - first_root[axis]) for axis in (0, 2)) > .0001:
                fail("CC_ROOT_MOTION", "horizontal root translation", name, frame)
            bones = _bones(sample)
            if set(bones) != expected_bones:
                fail("CC_MOTION_BONES", "sample lacks complete deformation hierarchy", name, frame)
            for bone_name, bone in bones.items():
                if not _vector(bone.get("head_m")) or not _vector(bone.get("tail_m")):
                    fail("CC_MOTION_FINITE", f"{bone_name}: invalid bone positions", name, frame)
            for bone_name, joint_limits in limits.items():
                q = bones.get(bone_name, {}).get("rotation_xyzw")
                if q is None or len(q) != 4 or not all(math.isfinite(v) for v in q):
                    fail("CC_MOTION_ROTATION", bone_name, name, frame)
                    continue
                if abs(sum(v*v for v in q) - 1) > 1e-4:
                    fail("CC_MOTION_ROTATION", f"{bone_name}: non-unit quaternion", name, frame)
                x, y, z, w = q
                # Profiles use +Z along the part and +Y dorsal. Deformation
                # bones use +Y along and +Z dorsal: profile +X = bone -X.
                angles = (-math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)),
                          math.asin(max(-1.0, min(1.0, 2*(w*y-z*x)))),
                          math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z)))
                for axis, angle in zip(("swing_x", "twist", "swing_y"), angles):
                    low, high = joint_limits[axis]
                    degrees = math.degrees(angle)
                    if not low - .1 <= degrees <= high + .1:
                        fail("CC_JOINT_LIMIT", f"{bone_name}.{axis}: {degrees:.3f} outside [{low}, {high}]", name, frame)
            active = set()
            seen_contacts = set()
            for contact in sample.get("contacts", []):
                bid = contact["branch_id"]
                kind = contact.get("kind", "foot")
                cid = contact.get("contact_id", f"{bid}:0")
                if cid in seen_contacts or declared_contacts.get(cid) != (bid, kind):
                    fail("CC_CONTACT_SET", f"unexpected or duplicate contact {cid}", name, frame)
                seen_contacts.add(cid)
                pos = contact.get("position_m")
                if not _vector(pos):
                    fail("CC_CONTACT_SAMPLE", f"{bid}: missing evaluated contact position", name, frame)
                    continue
                depth = max(0.0, -pos[1])
                metrics["max_penetration_m"] = max(metrics["max_penetration_m"], depth)
                if depth > penetration_m + 1e-6:
                    fail("CC_GROUND_PENETRATION", f"{bid}: {depth:.6f} m", name, frame)
                limit = max(.005, .01 * branches.get(bid, {}).get("length_m", 0))
                # Foot/hand records currently mark the deforming tip axis, up
                # to 7.5 mm above its rounded sole. This ground proximity band
                # is independent of the stricter planted-drift threshold.
                ground_band = .01 if kind in ("foot", "hand") else .005
                if contact.get("planted") and abs(pos[1]) > ground_band + 1e-6:
                    fail("CC_CONTACT_GROUND", f"{cid}: planted point {pos[1]:.6f} m from ground", name, frame)
                if "target_m" in contact and _vector(contact["target_m"]):
                    error = _distance(pos, contact["target_m"])
                    metrics["max_contact_target_error_m"] = max(metrics["max_contact_target_error_m"], error)
                    if error > limit + 1e-6 and contact.get("planted", False):
                        fail("CC_CONTACT_REACH", f"{bid}: target error {error:.6f} m", name, frame)
                if contact.get("planted", False) and kind not in ("sliding", "body"):
                    active.add(cid)
                    world = [pos[0], pos[1], pos[2] + sample.get("simulated_forward_m", 0.0)]
                    origin = planted.setdefault(cid, world)
                    drift = _distance(origin, world)
                    metrics["max_planted_drift_m"] = max(metrics["max_planted_drift_m"], drift)
                    if drift > limit + 1e-6:
                        fail("CC_CONTACT_DRIFT", f"{bid}: {drift:.6f} m > {limit:.6f} m", name, frame)
                if contact.get("required_support") and not contact.get("planted"):
                    fail("CC_SUPPORT_SCHEDULE", f"{bid}: declared required contact absent", name, frame)
            planted = {k: v for k, v in planted.items() if k in active}
            if seen_contacts != set(declared_contacts):
                fail("CC_CONTACT_SET", f"missing contacts: {sorted(set(declared_contacts) - seen_contacts)}", name, frame)
            required = anatomical_minimum
            # Death explicitly releases locomotor support during collapse. No
            # other clip can waive support by labelling a bad pose airborne.
            if name == "death" and frame / max(1, frames) >= .25:
                required = 0
            if sample.get("minimum_support") != required or sample.get("airborne") is not False:
                fail("CC_SUPPORT_METADATA", "schedule missing or differs from anatomical support policy", name, frame)
            support_count = len({c["branch_id"] for c in sample.get("contacts", [])
                                 if c.get("planted", False) and c.get("branch_id") in required_support})
            if support_count < required:
                fail("CC_SUPPORT_SCHEDULE", f"{support_count} contacts < {required} required", name, frame)
            for violation in sample.get("joint_limit_violations", []):
                fail("CC_JOINT_LIMIT", str(violation), name, frame)
            mesh_min = sample.get("mesh_bounds_min_m")
            if not _vector(mesh_min) or not _vector(sample.get("mesh_bounds_max_m")):
                fail("CC_MESH_SAMPLE", "missing evaluated mesh bounds", name, frame)
            elif mesh_min[1] < -penetration_m:
                fail("CC_MESH_PENETRATION", f"mesh minimum {mesh_min[1]:.6f} m", name, frame)
        if clip.get("loop") and len(samples) >= 3:
            first, second, previous, last = map(_bones, (samples[0], samples[1], samples[-2], samples[-1]))
            for bone in first.keys() & last.keys():
                qs = [values.get(bone, {}).get("rotation_xyzw") for values in (first, second, previous, last)]
                if all(_vector(q, 4) for q in qs):
                    if _distance(_rotation_delta(qs[0], qs[3]), [0,0,0]) > .1:
                        fail("CC_LOOP_ROTATION", f"{bone}: orientation seam exceeds 0.1 degrees", name)
                    q2 = _bones(samples[2]).get(bone, {}).get("rotation_xyzw")
                    qn2 = _bones(samples[-3]).get(bone, {}).get("rotation_xyzw")
                    if _vector(q2, 4) and _vector(qn2, 4):
                        start_velocity = [(4*a-b)*fps/2 for a,b in zip(_rotation_delta(qs[0],qs[1]), _rotation_delta(qs[0],q2))]
                        end_velocity = [(-4*a+b)*fps/2 for a,b in zip(_rotation_delta(qs[3],qs[2]), _rotation_delta(qs[3],qn2))]
                        if _distance(start_velocity, end_velocity) > 5.0:
                            fail("CC_LOOP_ANGULAR_VELOCITY", f"{bone}: angular seam exceeds 5 degrees/s", name)
                for point in ("head_m", "tail_m"):
                    error = _distance(first[bone][point], last[bone][point])
                    metrics["max_loop_position_error_m"] = max(metrics["max_loop_position_error_m"], error)
                    if error > .001:
                        fail("CC_LOOP_POSITION", f"{bone}.{point}: {error:.6f} m", name)
                    if bone in second and bone in previous:
                        third, antepenultimate = _bones(samples[2]), _bones(samples[-3])
                        v0 = [(-3*a+4*b-c)*fps/2 for a,b,c in zip(first[bone][point], second[bone][point], third[bone][point])]
                        v1 = [(3*c-4*b+a)*fps/2 for a,b,c in zip(antepenultimate[bone][point], previous[bone][point], last[bone][point])]
                        velocity = _distance(v0, v1)
                        metrics["max_loop_velocity_error_mps"] = max(metrics["max_loop_velocity_error_mps"], velocity)
                        # Second-order one-sided endpoint derivative estimates.
                        if velocity > .1:
                            fail("CC_LOOP_VELOCITY", f"{bone}.{point}: {velocity:.6f} m/s", name)
    if clips.get("telegraph", {}).get("samples") and clips.get("attack", {}).get("samples"):
        end = _bones(clips["telegraph"]["samples"][-1])
        start = _bones(clips["attack"]["samples"][0])
        for name in end.keys() & start.keys():
            if any(_distance(end[name][p], start[name][p]) > .001 for p in ("head_m", "tail_m")):
                fail("CC_ATTACK_TRANSITION", f"{name}: telegraph end differs from attack start")
            qa, qb = end[name].get("rotation_xyzw"), start[name].get("rotation_xyzw")
            if _vector(qa, 4) and _vector(qb, 4) and _distance(_rotation_delta(qa,qb), [0,0,0]) > .1:
                fail("CC_ATTACK_TRANSITION", f"{name}: telegraph/attack orientation differs")
    return {"skeleton_id": skeleton["skeleton_id"], "passed": not diagnostics,
            "qa_version": verification_version(), "sample_source": motion.get("sample_source"),
            "clips_checked": len(clips), "samples_checked": sum(len(c.get("samples", [])) for c in clips.values()),
            "contacts_checked": sum(len(s.get("contacts", [])) for c in clips.values() for s in c.get("samples", [])),
            "joint_limits_checked": len(limits),
            "thresholds": {"drift_m": "max(0.005, 0.01 * chain_length_m)", "penetration_m": penetration_m,
                           "foot_tip_ground_proximity_m": .01, "body_ground_proximity_m": .005},
            "metrics": metrics, "diagnostics": diagnostics}
