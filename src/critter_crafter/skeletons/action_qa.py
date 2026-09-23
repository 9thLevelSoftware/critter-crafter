"""Verify semantic actions against authored targets and evaluated bone endpoints."""
from __future__ import annotations

import math
from .. import mathutil
from ..library.export_validation import bone_matrix, matrix_inverse, matrix_multiply
from .actions import AttackPlanError, attack_target_at, resolve_attack


def neutral_effector_anchor(skeleton: dict, plan: dict) -> list[float]:
    """Forward kinematics from source bind frames, independent of baked samples."""
    rest = {bone["name"]: bone_matrix(bone) for bone in skeleton["bones"]}
    rotations = {item["bone_name"]: item["rotation_xyzw"] for item in skeleton["neutral_pose"]["rotations"]}
    poses = {}
    for bone in skeleton["bones"]:
        name, parent = bone["name"], bone.get("parent")
        local = matrix_multiply(matrix_inverse(rest[parent]), rest[name]) if parent else rest[name]
        q = rotations[name]
        columns = [mathutil.quat_rotate(q, [float(i == j) for i in range(3)]) for j in range(3)]
        delta = [[columns[j][i] for j in range(3)] + [0.] for i in range(3)] + [[0.,0.,0.,1.]]
        local = matrix_multiply(local, delta)
        poses[name] = matrix_multiply(poses[parent], local) if parent else local
        if not parent:
            for axis, offset in enumerate(skeleton["neutral_pose"]["root_offset_m"]):
                poses[name][axis][3] += offset
    matrix = poses[plan["effector"]["bone_name"]]
    point = plan["effector"]["local_point_m"] + [1.]
    return [sum(matrix[i][j]*point[j] for j in range(4)) for i in range(3)]


def evaluate_actions(skeleton: dict, motion: dict) -> dict:
    diagnostics = []
    metrics = {"max_target_error_m": 0., "impact_displacement_m": 0., "recovery_error_m": 0.}
    def fail(code, detail, clip="", frame=None):
        diagnostics.append({"code": code, "detail": detail, "clip": clip, "frame": frame})
    try:
        plan = resolve_attack(skeleton)
        anchor = neutral_effector_anchor(skeleton, plan)
    except (AttackPlanError, KeyError, ValueError) as exc:
        return {"passed": False, "diagnostics": [{"code": "CC_ACTION_SOURCE", "detail": str(exc)}]}
    if motion.get("attack_plan") != plan:
        fail("CC_ACTION_PROVENANCE", "baked attack plan differs from authored semantic action")
    claimed_anchor = motion.get("attack_anchor_m")
    if (not isinstance(claimed_anchor, list) or len(claimed_anchor) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in claimed_anchor)):
        fail("CC_ACTION_ANCHOR", "baked neutral effector anchor must be a finite three-component vector")
    elif math.dist(anchor, claimed_anchor) > .0001:
        fail("CC_ACTION_ANCHOR", "baked neutral effector anchor differs from source FK")
    clips = {clip["name"]: clip for clip in motion.get("clips", [])}
    effector = plan["effector"]["bone_name"]
    tolerance = plan["qa"]["target_tolerance_m"]
    attack_start, impact_samples = None, []
    checked = 0
    for name in ("telegraph", "attack"):
        clip = clips.get(name, {})
        samples, frames = clip.get("samples", []), clip.get("frames", 0)
        if not samples or frames <= 0:
            fail("CC_ACTION_SAMPLES", "semantic action requires complete evaluated samples", name)
            continue
        for sample in samples:
            frame = sample["frame"]
            phase = frame / frames
            windup = plan["timing"]["windup_end"]
            global_phase = phase * windup if name == "telegraph" else windup + phase * (1-windup)
            declared = attack_target_at(plan, global_phase)
            bones = sample.get("bones", [])
            bones = bones if isinstance(bones, dict) else {b["name"]: b for b in bones}
            position = bones.get(effector, {}).get("tail_m")
            if not position or len(position) != 3 or not all(math.isfinite(v) for v in position):
                fail("CC_ACTION_EFFECTOR", "missing evaluated effector endpoint", name, frame)
                continue
            checked += 1
            target = [a+b for a,b in zip(anchor, declared["target_offset_m"])]
            error = math.dist(position, target)
            metrics["max_target_error_m"] = max(metrics["max_target_error_m"], error)
            if error > tolerance + 1e-6:
                fail("CC_ACTION_TARGET", f"{effector}: {error:.6f} m > {tolerance:.6f} m", name, frame)
            contacts = {c.get("contact_id", c["branch_id"]+":0"): c for c in sample.get("contacts", [])}
            for cid in plan["support_release"]["preserved_contact_ids"]:
                if not contacts.get(cid, {}).get("planted"):
                    fail("CC_ACTION_SUPPORT", f"preserved support {cid} was released", name, frame)
            for cid in plan["support_release"]["contact_ids"]:
                expected_planted = cid not in declared["release_contact_ids"]
                if contacts.get(cid, {}).get("planted") is not expected_planted:
                    fail("CC_ACTION_SUPPORT", f"effector contact {cid} violates release/replant schedule", name, frame)
            if name == "attack":
                if attack_start is None:
                    attack_start = position
                if plan["timing"]["impact"] <= global_phase <= plan["timing"]["recovery_start"]:
                    impact_samples.append(position)
                if frame == frames:
                    metrics["recovery_error_m"] = math.dist(position, anchor)
    if attack_start is None or not impact_samples:
        fail("CC_ACTION_IMPACT", "no evaluated impact window")
    else:
        metrics["impact_displacement_m"] = max(math.dist(attack_start, p) for p in impact_samples)
        if metrics["impact_displacement_m"] < plan["qa"]["minimum_displacement_m"]:
            fail("CC_ACTION_DISPLACEMENT", "attack is stationary or fails its authored displacement")
    if metrics["recovery_error_m"] > tolerance + 1e-6:
        fail("CC_ACTION_RECOVERY", "effector did not return to neutral")
    return {"passed": not diagnostics, "attack_id": plan["attack_id"], "samples_checked": checked,
            "metrics": metrics, "diagnostics": diagnostics}
