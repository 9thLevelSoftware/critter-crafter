"""Contact-driven skeleton motion, evaluated bake, mannequin, and export."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Quaternion, Vector

from ..skeletons import motion as planner
from . import ops_reference, rigkit
from .frame import to_blender, to_gltf

ARMATURE_NAME = "Skeleton"
BIND_PROXY = "BindProxy"
CLIPS = [(name, name in planner.LOOP_CLIPS) for name in planner.CLIP_ORDER]


def _q(xyzw: list[float] | tuple[float, ...]) -> Quaternion:
    return Quaternion((xyzw[3], xyzw[0], xyzw[1], xyzw[2]))


def _r6v(v: Any) -> list[float]:
    return [round(float(x), 6) for x in v]


def _root_local(arm: bpy.types.Object, catalog_offset: list[float] | tuple[float, ...]) -> Vector:
    world_delta = Vector(to_blender(catalog_offset))
    basis = arm.pose.bones["root"].bone.matrix_local.to_3x3()
    return basis.inverted() @ world_delta


def _apply_sample(arm: bpy.types.Object, sample: dict[str, Any], root_lift: float = 0.0) -> None:
    for pb in arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    for name, xyzw in sample["rotations_xyzw"].items():
        pb = arm.pose.bones.get(name)
        if pb is not None:
            pb.rotation_quaternion = _q(xyzw)
            pb.scale = (1.0, 1.0, 1.0)
    p = list(sample["root_position_m"]); p[1] += root_lift
    arm.pose.bones["root"].location = _root_local(arm, p)


def _contact_records(branch: dict[str, Any], skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    raw = branch.get("contacts") or []
    if raw:
        records = []
        for contact_index, contact in enumerate(raw):
            index = int(contact["bone_index"])
            if not 0 <= index < len(branch["bone_names"]):
                raise ValueError(f"{branch['branch_id']}: contact bone_index {index} out of range")
            records.append({"kind": contact["kind"], "bone_name": branch["bone_names"][index],
                            "bone_index": index, "contact_index": contact_index,
                            "local_point_m": list(contact["local_point_m"])})
        return records
    index = len(branch["bone_names"]) - 1
    return [{"kind": planner._contact_kind(branch, skeleton), "bone_name": branch["bone_names"][index],
             "bone_index": index, "contact_index": 0, "local_point_m": None}]


def _contact_record(branch: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    """Primary endpoint used by single-contact limb IK and legacy callers."""
    return _contact_records(branch, skeleton)[0]


def _contact_position(pb: bpy.types.PoseBone, contact: dict[str, Any]) -> Vector:
    local = contact.get("local_point_m")
    return pb.tail.copy() if local is None else pb.matrix @ Vector(local)


def _apply_ik_limits(pb: bpy.types.PoseBone, limits: dict[str, Any] | None,
                     inset_deg: float | dict[str, float] = 0.0) -> None:
    limits = limits or {"swing_x": [-100, 100], "swing_y": [-100, 100], "twist": [-55, 55]}
    neutral = pb.rotation_quaternion.to_euler("XYZ")
    canonical_x = limits.get("swing_x", [-100, 100])
    local_x = [-float(canonical_x[1]), -float(canonical_x[0])]
    for index, (axis, pair) in enumerate((("x", local_x),
                       ("y", limits.get("twist", [-55, 55])),
                       ("z", limits.get("swing_y", [-100, 100])))):
        inset = float(inset_deg.get(axis, 0.0) if isinstance(inset_deg, dict) else inset_deg)
        pair = [float(pair[0]) + inset, float(pair[1]) - inset]
        angle = math.degrees(neutral[index])
        if angle < float(pair[0]) - 1e-4 or angle > float(pair[1]) + 1e-4:
            raise ValueError(f"{pb.name}: neutral {axis}={angle:.3f} outside joint limit {pair}")
        setattr(pb, f"use_ik_limit_{axis}", True)
        setattr(pb, f"ik_min_{axis}", math.radians(float(pair[0])))
        setattr(pb, f"ik_max_{axis}", math.radians(float(pair[1])))


def _make_controls(arm: bpy.types.Object, skeleton: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Create temporary contact targets, bend poles, limits, and nonstretch IK."""
    support_ids = set(skeleton.get("anatomy", {}).get("support_branches", []))
    pindex = {(p["binding_profile_id"], p["binding_profile_version"]): p for p in profiles}
    controls: dict[str, dict[str, Any]] = {}
    for branch in skeleton["branches"]:
        if planner.branch_role(branch) != "locomotor" or (support_ids and branch["branch_id"] not in support_ids):
            continue
        if "anatomy" in skeleton and not branch.get("contacts"):
            continue
        contacts = _contact_records(branch, skeleton)
        # Distributed ventral body samples are sliding constraints.  They are
        # kept coplanar by the authored axial pose and travel wave rather than
        # competing IK constraints on overlapping prefixes of one chain.
        if len(contacts) != 1 or contacts[0]["kind"] in ("body", "sliding"):
            continue
        contact = contacts[0]; pb = arm.pose.bones[contact["bone_name"]]
        target = bpy.data.objects.new(f"CC_TMP_contact_{branch['branch_id']}", None)
        pole = bpy.data.objects.new(f"CC_TMP_pole_{branch['branch_id']}", None)
        target.empty_display_type = "SPHERE"; target.empty_display_size = .045
        pole.empty_display_type = "ARROWS"; pole.empty_display_size = .09
        bpy.context.scene.collection.objects.link(target); bpy.context.scene.collection.objects.link(pole)
        base = _contact_position(pb, contact)
        target.matrix_world.translation = arm.matrix_world @ base
        gait = branch.get("gait", {})
        if gait.get("bend_pole_m"):
            pole.matrix_world.translation = arm.matrix_world @ Vector(to_blender(gait["bend_pole_m"]))
        else:
            first = arm.pose.bones[branch["bone_names"][0]]
            bone = next(b for b in skeleton["bones"] if b["name"] == first.name)
            up = Vector(to_blender(bone.get("up_m", (0, 1, 0)))).normalized()
            pole.matrix_world.translation = arm.matrix_world @ (first.head + up * branch["length_m"] * .5)
        ik = pb.constraints.new("IK"); ik.name = f"CC_TMP_nonstretch_{branch['branch_id']}"
        ik.target = target
        # Blender's multi-joint IK pole is stable for the conventional paired
        # limb plans. Radial/tripod/segmental chains use the same authored pole
        # as a visible verification control, while the solver preserves their
        # neutral bend plane instead of forcing an incompatible two-bone roll.
        pole_driven = skeleton.get("family") in ("biped", "quadruped", "hexapod", "dragger")
        ik.pole_target = pole if pole_driven else None
        ik.chain_count = contact["bone_index"] + 1
        ik.pole_angle = -math.pi * .5
        ik.use_tail = True; ik.use_stretch = False; ik.iterations = 1000; ik.influence = 0.0
        profile = pindex.get((branch.get("binding_profile_id", ""), branch.get("binding_profile_version", "")), {})
        joints = profile.get("joints", [])
        for i, name in enumerate(branch["bone_names"][:contact["bone_index"] + 1]):
            _apply_ik_limits(arm.pose.bones[name], joints[i].get("limits_deg") if i < len(joints) else None)
        controls[branch["branch_id"]] = {"branch": branch, "contact": contact, "target": target,
                                           "pole": pole, "constraint": ik, "base": base.copy()}
        root = arm.pose.bones[branch["bone_names"][0]].head
        reach = sum(arm.pose.bones[name].length for name in branch["bone_names"][:contact["bone_index"] + 1])
        offset = base - root
        disc = max(0.0, reach*reach - offset.x*offset.x - offset.z*offset.z)
        extent = math.sqrt(disc)
        lo, hi = -offset.y - extent, -offset.y + extent
        controls[branch["branch_id"]]["forward_limit"] = max(0.005, min(hi, -lo) * .72)
    # Blender measures pole_angle in each chain's rolled rest basis.  Calibrate
    # that basis once at the neutral contact instead of assuming every radial
    # limb shares the biped's -90 degree roll.
    original = {pb.name: pb.matrix.to_quaternion() for pb in arm.pose.bones}
    for control in controls.values():
        constraint = control["constraint"]
        if constraint.pole_target is None:
            continue
        names = control["branch"]["bone_names"][:control["contact"]["bone_index"] + 1]
        constraint.influence = 1.0
        best_angle, best_score = constraint.pole_angle, math.inf
        for step in range(24):
            angle = -math.pi + step * math.tau / 24.0
            constraint.pole_angle = angle; bpy.context.view_layer.update()
            evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
            score = sum(original[name].rotation_difference(
                evaluated.pose.bones[name].matrix.to_quaternion()).angle for name in names)
            if score < best_score:
                best_angle, best_score = angle, score
        constraint.pole_angle = best_angle
        constraint.influence = 0.0
        control["pole_angle"] = best_angle
    bpy.context.view_layer.update()
    arm["cc_motion_solver"] = "contact_ik_nonstretch_v1"
    arm["cc_temporary_controls_baked"] = len(controls)
    return controls


def _set_targets(arm: bpy.types.Object, controls: dict[str, dict[str, Any]], sample: dict[str, Any], enabled: bool) -> None:
    paths = {c["branch_id"]: c for c in sample["contacts"]}
    for branch_id, control in controls.items():
        path = paths.get(branch_id)
        driven = path and bool(path.get("drive_ik", False))
        control["constraint"].influence = 1.0 if enabled and driven else 0.0
        if path:
            delta = Vector(to_blender((0.0, float(path["height_m"]), float(path["forward_m"]))))
            control["target"].matrix_world.translation = arm.matrix_world @ (control["base"] + delta)


def _make_action_control(arm: bpy.types.Object, skeleton: dict[str, Any], plan: dict[str, Any],
                         profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    attack = plan.get("attack_plan")
    if not attack:
        return None
    effector = attack["effector"]
    branch = next(b for b in skeleton["branches"] if b["branch_id"] == effector["branch_id"])
    pb = arm.pose.bones[effector["bone_name"]]
    contact = {"local_point_m":effector["local_point_m"]}
    evaluated_base = _contact_position(pb, contact)
    declared_base = attack.get("trajectory", {}).get("neutral_effector_m")
    base = (Vector(to_blender(declared_base)) if declared_base is not None
            else evaluated_base)
    target = bpy.data.objects.new(f"CC_TMP_action_{attack['attack_id']}", None)
    target.empty_display_type = "SPHERE"; target.empty_display_size = .055
    bpy.context.scene.collection.objects.link(target)
    target.matrix_world.translation = arm.matrix_world @ base
    constraint = pb.constraints.new("IK"); constraint.name = f"CC_TMP_action_{attack['attack_id']}"
    solver_names = attack.get("solver_chain", {}).get("bone_names", [])
    constraint.target = target
    constraint.chain_count = len(solver_names) or (int(effector["bone_index"]) + 1)
    constraint.use_tail = True; constraint.use_stretch = False; constraint.iterations = 1000; constraint.influence = 0.0
    profile = next((p for p in profiles if p["binding_profile_id"] == branch.get("binding_profile_id")
                    and p["binding_profile_version"] == branch.get("binding_profile_version")), None)
    limit_names = solver_names or branch["bone_names"][:constraint.chain_count]
    dorsal_seed = (attack.get("solver") == "analytic_chain"
                   or any(c.get("kind") in ("body", "sliding") for c in branch.get("contacts", [])))
    seed_sign = 1.0 if attack.get("solver") == "analytic_chain" else -1.0
    original_stiffness = {}
    for name in limit_names:
        i = branch["bone_names"].index(name)
        joints = (profile or {}).get("joints", [])
        inset = ({"x": 0.5, "y": 2.0, "z": 0.5}
                 if dorsal_seed and attack.get("solver") == "analytic_chain"
                 else (2.0 if dorsal_seed else 0.0))
        _apply_ik_limits(arm.pose.bones[name], joints[i].get("limits_deg") if i < len(joints) else None,
                         inset_deg=inset)
        if dorsal_seed:
            original_stiffness[name] = arm.pose.bones[name].ik_stiffness_x
            arm.pose.bones[name].ik_stiffness_x = .94 if attack.get("solver") == "analytic_chain" else .90
    support = set(skeleton.get("anatomy", {}).get("support_branches", []))
    return {"plan":attack, "branch":branch, "contact":contact, "bone_name":pb.name,
            "base":base.copy(), "target":target, "constraint":constraint,
            "solver_names":limit_names, "dorsal_seed":dorsal_seed,
            "seed_sign":seed_sign,
            "original_stiffness":original_stiffness,
            "support_effector":branch["branch_id"] in support}


def _seed_action_bend(arm: bpy.types.Object, control: dict[str, Any] | None,
                      sample: dict[str, Any]) -> None:
    """Select the lifted solution for otherwise ambiguous ground chains."""
    if not control or not control.get("dorsal_seed") or not sample.get("action_target"):
        return
    offset = Vector(sample["action_target"]["target_offset_m"])
    reach = max(1e-6, float(control["plan"].get("trajectory", {}).get("max_reach_m", offset.length)))
    strength = math.sqrt(min(1.0, offset.length / reach))
    names = control["solver_names"]
    angle = math.radians(22.0 if len(names) <= 3 else 12.0) * strength
    for index, name in enumerate(names):
        xyzw = sample.get("rotations_xyzw", {}).get(name)
        if xyzw is None:
            continue
        shape = (1.0 if control["plan"].get("solver") == "analytic_chain" and index == 0
                 else math.sin(math.pi * (index + 1) / (len(names) + 1)))
        seed = Quaternion((1.0, 0.0, 0.0), control.get("seed_sign", 1.0) * angle * shape)
        if control["plan"].get("solver") == "analytic_chain" and index == 0:
            lateral_strength = min(1.0, abs(offset.x) / max(1e-6, reach * .25))
            lateral = math.copysign(math.radians(12.0) * lateral_strength, -offset.x) if offset.x else 0.0
            seed = seed @ Quaternion((0.0, 0.0, 1.0), lateral)
        arm.pose.bones[name].rotation_quaternion = _q(xyzw) @ seed


def _set_action_target(arm: bpy.types.Object, control: dict[str, Any] | None,
                       sample: dict[str, Any], clip_name: str) -> None:
    if not control:
        return
    action_target = sample.get("action_target")
    active = False
    offset = (0.0, 0.0, 0.0)
    if clip_name in ("telegraph", "attack") and action_target:
        active = True
        offset = action_target["target_offset_m"]
    control["constraint"].influence = 1.0 if active else 0.0
    control["target"].matrix_world.translation = arm.matrix_world @ (
        control["base"] + Vector(to_blender(offset)))


def _fit_motion_to_reach(plan: dict[str, Any], controls: dict[str, dict[str, Any]]) -> None:
    """Resolve authored stride against strict neutral reach without stretching.

    All support contacts use one effective travel speed, so adding the simulated
    forward distance makes every planted contact stationary in world space.
    """
    if not controls:
        return
    for clip in plan["clips"]:
        if clip["name"] not in ("walk", "run"):
            continue
        requested = [float(c["stride_m"]) for s in clip["samples"] for c in s["contacts"] if c["branch_id"] in controls]
        if not requested:
            continue
        supports = [float(c["support_fraction"]) for s in clip["samples"] for c in s["contacts"] if c["branch_id"] in controls]
        stride = min(min(requested),
                     min(2.0 * c["forward_limit"] for c in controls.values()) / max(supports),
                     min(float(c["branch"]["length_m"]) * .12 for c in controls.values()))
        duration = clip["frames"] / planner.FPS
        clip["stride_m"] = stride; clip["speed_mps"] = stride / duration
        for sample in clip["samples"]:
            sample["simulated_forward_m"] = clip["speed_mps"] * sample["frame"] / planner.FPS
            for contact in sample["contacts"]:
                if contact["branch_id"] not in controls:
                    continue
                stroke = stride * contact["support_fraction"]
                path = planner.contact_trajectory(contact["phase"], stroke, contact["clearance_m"], contact["support_fraction"])
                contact.update(path); contact["stride_m"] = stride; contact["contact_stroke_m"] = stroke


def _legacy_ground_lift(arm: bpy.types.Object, skeleton: dict[str, Any], neutral: dict[str, Any]) -> float:
    if "neutral_pose" in skeleton:
        return 0.0
    _apply_sample(arm, neutral); bpy.context.view_layer.update()
    support = [b for b in skeleton["branches"] if planner.branch_role(b) == "locomotor"]
    if not support:
        return 0.0
    lowest = min(_contact_position(arm.pose.bones[c["bone_name"]], c).z
                 for b in support for c in _contact_records(b, skeleton))
    return -lowest


def _key_evaluated_action(arm: bpy.types.Object, clip: dict[str, Any], controls: dict[str, dict[str, Any]],
                          action_control: dict[str, Any] | None, root_lift: float) -> bpy.types.Action:
    names = [b.name for b in arm.data.bones if b.use_deform]
    matrices: list[dict[str, Matrix]] = []
    arm.animation_data.action = None
    for sample in clip["samples"]:
        bpy.context.scene.frame_set(sample["frame"])
        _apply_sample(arm, sample, root_lift)
        _seed_action_bend(arm, action_control, sample)
        _set_targets(arm, controls, sample, True)
        _set_action_target(arm, action_control, sample, clip["name"])
        bpy.context.view_layer.update()
        evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
        matrices.append({name: evaluated.pose.bones[name].matrix.copy() for name in names})
    for control in controls.values(): control["constraint"].influence = 0.0
    if action_control: action_control["constraint"].influence = 0.0
    action = bpy.data.actions.new(clip["name"]); action.use_fake_user = True
    arm.animation_data.action = action
    # Blender 5 layered actions no longer expose Action.fcurves.  Set the
    # insertion preference before writing dense per-frame keys instead.
    previous_interpolation = bpy.context.preferences.edit.keyframe_new_interpolation_type
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    for sample, pose in zip(clip["samples"], matrices):
        bpy.context.scene.frame_set(sample["frame"])
        for name in names:
            pb = arm.pose.bones[name]
            if pb.parent is None:
                basis = pb.bone.matrix_local.inverted() @ pose[name]
            else:
                rest_from_parent = pb.parent.bone.matrix_local.inverted() @ pb.bone.matrix_local
                basis = (pose[pb.parent.name] @ rest_from_parent).inverted() @ pose[name]
            pb.matrix_basis = basis
            pb.keyframe_insert("location", frame=sample["frame"], group=name)
            pb.keyframe_insert("rotation_quaternion", frame=sample["frame"], group=name)
            pb.keyframe_insert("scale", frame=sample["frame"], group=name)
    bpy.context.preferences.edit.keyframe_new_interpolation_type = previous_interpolation
    action.use_frame_range = True; action.frame_start = 0; action.frame_end = clip["frames"]
    action.use_cyclic = bool(clip["loop"])
    for key, value in (("cc_fps", planner.FPS), ("cc_duration_s", clip["duration_s"]),
                       ("cc_speed_mps", clip["speed_mps"]), ("cc_cadence_hz", clip["cadence_hz"]),
                       ("cc_root_motion", "fixed_horizontal")): action[key] = value
    return action


def _force_discrete_action_seam(arm: bpy.types.Object, action: bpy.types.Action,
                                end: int, names: list[str]) -> None:
    """Mirror the first two local increments at the cyclic endpoint.

    Blender IK can choose an equivalent pole solution on opposite sides of the
    action boundary.  Pinning the sampled local increments removes that branch
    ambiguity and meets the runtime's second-order velocity continuity check.
    """
    arm.animation_data.action = action
    start: dict[int, dict[str, tuple[Vector, Quaternion, Vector]]] = {}
    for frame in (0, 1, 2):
        bpy.context.scene.frame_set(frame); bpy.context.view_layer.update()
        start[frame] = {name: (arm.pose.bones[name].location.copy(),
                               arm.pose.bones[name].rotation_quaternion.copy(),
                               arm.pose.bones[name].scale.copy()) for name in names}
    for frame, source in ((end - 2, 2), (end - 1, 1), (end, 0)):
        bpy.context.scene.frame_set(frame)
        for name in names:
            pb = arm.pose.bones[name]; p0, q0, s0 = start[0][name]; pk, qk, sk = start[source][name]
            if source:
                pb.location = 2.0 * p0 - pk
                delta = q0.conjugated() @ qk
                pb.rotation_quaternion = q0 @ delta.conjugated()
                pb.scale = 2.0 * s0 - sk
            else:
                pb.location = p0; pb.rotation_quaternion = q0; pb.scale = s0
            pb.keyframe_insert("location", frame=frame, group=name)
            pb.keyframe_insert("rotation_quaternion", frame=frame, group=name)
            pb.keyframe_insert("scale", frame=frame, group=name)


def _remove_controls(controls: dict[str, dict[str, Any]]) -> None:
    for control in controls.values():
        pb = control["constraint"].id_data.pose.bones.get(control["contact"]["bone_name"])
        if pb: pb.constraints.remove(control["constraint"])
        bpy.data.objects.remove(control["target"], do_unlink=True); bpy.data.objects.remove(control["pole"], do_unlink=True)


def _clip_result(clip: dict[str, Any], lift: float) -> dict[str, Any]:
    keys = ("name", "loop", "frames", "fps", "duration_s", "cadence_hz", "speed_mps", "stride_m",
            "playback", "support_policy", "contact_schedule")
    return {k: clip[k] for k in keys} | {"nominal_speed_mps": clip["speed_mps"], "ground_offset_m": round(lift, 6)}


def _bake_clips(arm: bpy.types.Object, skeleton: dict[str, Any], gait: dict[str, Any],
                binding_profiles: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    arm.animation_data_create(); plan = planner.build_motion(skeleton, gait)
    lift = _legacy_ground_lift(arm, skeleton, plan["clips"][0]["samples"][0])
    _apply_sample(arm, plan["clips"][0]["samples"][0], lift); bpy.context.view_layer.update()
    controls = _make_controls(arm, skeleton, binding_profiles or [])
    action_control = _make_action_control(arm, skeleton, plan, binding_profiles or [])
    _fit_motion_to_reach(plan, controls)
    planner.enforce_discrete_loop_seams(plan)
    names = [bone.name for bone in arm.data.bones if bone.use_deform]
    # Pin cyclic solver branches while leaving the terminal contact bone on
    # its evaluated path.  Extrapolating that last bone can rotate its
    # weighted sole below the floor even though its contact point is valid.
    contact_terminals = {record["bone_name"] for branch in skeleton["branches"]
                         if planner.branch_role(branch) == "locomotor"
                         for record in _contact_records(branch, skeleton)}
    if skeleton.get("family") == "radial":
        contact_terminals.update(name for branch in skeleton["branches"]
                                 if planner.branch_role(branch) == "locomotor"
                                 for name in branch["bone_names"])
    seam_names = [name for name in names if name not in contact_terminals]
    for clip in plan["clips"]:
        action = _key_evaluated_action(arm, clip, controls, action_control, lift)
        if clip["loop"] and seam_names:
            _force_discrete_action_seam(arm, action, clip["frames"], seam_names)
    _remove_controls(controls)
    if action_control:
        arm.pose.bones[action_control["bone_name"]].constraints.remove(action_control["constraint"])
        bpy.data.objects.remove(action_control["target"], do_unlink=True)
        for name, stiffness in action_control.get("original_stiffness", {}).items():
            arm.pose.bones[name].ik_stiffness_x = stiffness
    arm["cc_motion_profile_id"] = plan["motion_profile_id"]; arm["cc_neutral_basis"] = plan["neutral_basis"]
    arm["cc_root_lift_m"] = lift; arm["_cc_plan"] = json.dumps(plan, separators=(",", ":"))
    clear_pose(arm)
    return [_clip_result(c, lift) for c in plan["clips"]]


def _load_polished_actions(path: str, skeleton: dict[str, Any], gait: dict[str, Any],
                           profiles: list[dict[str, Any]], source_fingerprint: str) -> tuple[bpy.types.Object, bpy.types.Object,
                                                                      list[dict[str, Any]], dict[str, Any]]:
    """Open an authored master and use its actions without regenerating them."""
    bpy.ops.wm.open_mainfile(filepath=str(Path(path).resolve()))
    arm = bpy.data.objects.get(ARMATURE_NAME)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"polish blend lacks armature {ARMATURE_NAME!r}")
    expected = {bone["name"] for bone in skeleton["bones"]}
    actual = {bone.name for bone in arm.data.bones if bone.use_deform}
    if actual != expected:
        raise ValueError(f"polish deformation bones differ: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    if str(arm.get("cc_source_fingerprint", "")) != source_fingerprint:
        raise ValueError("polish source fingerprint differs from the requested skeleton build")
    expected_bones = {bone["name"]: bone for bone in skeleton["bones"]}
    for bone in arm.data.bones:
        if not bone.use_deform:
            continue
        source = expected_bones[bone.name]
        head = Vector(to_gltf(bone.head_local)); tail = Vector(to_gltf(bone.tail_local))
        if ((head - Vector(source["head_m"])).length > 1e-5
                or (tail - Vector(source["tail_m"])).length > 1e-5):
            raise ValueError(f"polish rest geometry differs at bone {bone.name}")
        axis = (tail - head).normalized()
        expected_up = Vector(source.get("up_m", (0.0, 1.0, 0.0)))
        expected_up = (expected_up - axis * expected_up.dot(axis)).normalized()
        actual_up = Vector(to_gltf(bone.matrix_local.to_3x3() @ Vector((0.0, 0.0, 1.0))))
        actual_up = (actual_up - axis * actual_up.dot(axis)).normalized()
        if actual_up.dot(expected_up) < 1.0 - 1e-6:
            raise ValueError(f"polish rest geometry differs at bone {bone.name}")
    mannequin = next((obj for obj in bpy.data.objects if obj.type == "MESH"
                      and obj.get("cc_reference_technique") and obj.parent == arm), None)
    if mannequin is None:
        raise ValueError("polish blend lacks the weighted reference mannequin")
    plan = planner.build_motion(skeleton, gait)
    clear_pose(arm); _apply_sample(arm, plan["clips"][0]["samples"][0]); bpy.context.view_layer.update()
    controls = _make_controls(arm, skeleton, profiles)
    _fit_motion_to_reach(plan, controls); planner.enforce_discrete_loop_seams(plan); _remove_controls(controls)
    clips = []
    for clip in plan["clips"]:
        action = bpy.data.actions.get(clip["name"])
        if action is None:
            raise ValueError(f"polish blend lacks required action {clip['name']!r}")
        start, end = map(round, action.frame_range)
        if start > 0 or end < clip["frames"]:
            raise ValueError(f"polish action {clip['name']!r} does not cover frames 0..{clip['frames']}")
        clips.append(_clip_result(clip, 0.0))
    arm["cc_motion_profile_id"] = plan["motion_profile_id"]
    arm["cc_neutral_basis"] = plan["neutral_basis"]
    arm["cc_root_lift_m"] = 0.0
    clear_pose(arm)
    return arm, mannequin, clips, plan


def clear_pose(obj: bpy.types.Object) -> None:
    obj.animation_data_create(); obj.animation_data.action = None
    for pb in obj.pose.bones:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()


def add_bind_proxy(arm: bpy.types.Object) -> bpy.types.Object:
    verts, faces = [], []
    for b in arm.data.bones:
        h = arm.matrix_world @ b.head_local; i = len(verts)
        verts += [tuple(h), (h.x+.001,h.y,h.z), (h.x,h.y+.001,h.z)]; faces.append((i,i+1,i+2))
    mesh = bpy.data.meshes.new(BIND_PROXY); mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(BIND_PROXY, mesh); bpy.context.scene.collection.objects.link(obj)
    for i, b in enumerate(arm.data.bones): obj.vertex_groups.new(name=b.name).add([3*i,3*i+1,3*i+2],1.0,"REPLACE")
    obj.parent = arm; obj.modifiers.new("Armature", "ARMATURE").object = arm; obj.hide_render = True
    obj["cc_bind_proxy"] = "fbx_rest_matrices_v1"
    return obj


def _neutral_contact_bases(arm: bpy.types.Object, skeleton: dict[str, Any], plan: dict[str, Any], lift: float) -> dict[tuple[str, int], Vector]:
    clear_pose(arm); _apply_sample(arm, plan["clips"][0]["samples"][0], lift); bpy.context.view_layer.update()
    return {(br["branch_id"], c["contact_index"]): _contact_position(arm.pose.bones[c["bone_name"]], c)
            for br in skeleton["branches"] if planner.branch_role(br) == "locomotor"
            for c in _contact_records(br, skeleton)}


def _joint_limit_violations(arm: bpy.types.Object, skeleton: dict[str, Any],
                            profiles: list[dict[str, Any]]) -> list[str]:
    pindex = {(p["binding_profile_id"], p["binding_profile_version"]): p for p in profiles}
    violations = []
    for branch in skeleton["branches"]:
        profile = pindex.get((branch.get("binding_profile_id", ""), branch.get("binding_profile_version", "")), {})
        for name, joint in zip(branch["bone_names"], profile.get("joints", [])):
            e = arm.pose.bones[name].rotation_quaternion.to_euler("XYZ")
            actual = {"swing_x": -math.degrees(e.x), "swing_y": math.degrees(e.z), "twist": math.degrees(e.y)}
            for axis, pair in joint.get("limits_deg", {}).items():
                if actual[axis] < float(pair[0]) - .1 or actual[axis] > float(pair[1]) + .1:
                    violations.append(f"{name}.{axis}={actual[axis]:.3f} outside [{pair[0]},{pair[1]}]")
    return violations


def _minimum_support(skeleton: dict[str, Any], clip_name: str, desired_sample: dict[str, Any]) -> tuple[int, bool]:
    if clip_name == "death" and float(desired_sample.get("phase", 0.0)) >= .25:
        return 0, False
    body_plan = skeleton.get("anatomy", {}).get("body_plan", skeleton.get("family", ""))
    desired_min = 2 if body_plan in ("quadruped", "hexapod", "crawler", "radial") else 1
    return desired_min, False


def _sample_artifact(arm: bpy.types.Object, mannequin: bpy.types.Object, skeleton: dict[str, Any],
                     clips: list[dict[str, Any]], plan: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    bases = _neutral_contact_bases(arm, skeleton, plan, float(arm.get("cc_root_lift_m", 0.0)))
    attack_plan = plan.get("attack_plan")
    action_base = None
    if attack_plan:
        effector = attack_plan["effector"]
        declared_base = attack_plan.get("trajectory", {}).get("neutral_effector_m")
        action_base = (Vector(to_blender(declared_base)) if declared_base is not None else
                       _contact_position(arm.pose.bones[effector["bone_name"]],
                                         {"local_point_m":effector["local_point_m"]}))
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    out_clips = []; max_error = 0.0; min_y = math.inf
    for meta, desired in zip(clips, plan["clips"]):
        arm.animation_data.action = bpy.data.actions[meta["name"]]; samples = []
        for sample in desired["samples"]:
            bpy.context.scene.frame_set(sample["frame"]); bpy.context.view_layer.update()
            bones, points = [], []
            for pb in arm.pose.bones:
                if not pb.bone.use_deform: continue
                head, tail = Vector(to_gltf(pb.head)), Vector(to_gltf(pb.tail)); points += [head, tail]
                q = pb.rotation_quaternion
                bones.append({"name": pb.name, "head_m": _r6v(head), "tail_m": _r6v(tail),
                              "rotation_xyzw": [round(q.x,7),round(q.y,7),round(q.z,7),round(q.w,7)]})
            contacts = []
            for path in sample["contacts"]:
                br = branches[path["branch_id"]]
                for contact in _contact_records(br, skeleton):
                    pos = Vector(to_gltf(_contact_position(arm.pose.bones[contact["bone_name"]], contact)))
                    if contact["kind"] in ("body", "sliding"):
                        # A sliding target follows the evaluated horizontal
                        # trajectory but retains the authored support plane.
                        # Its error therefore measures loss of ground contact.
                        base = Vector(to_gltf(bases[(path["branch_id"], contact["contact_index"])]))
                        target = Vector((pos.x, base.y + float(path["height_m"]), pos.z))
                    elif (path["support"] or planner.branch_role(br) == "locomotor") and not (
                            meta["name"] == "death" and not path["support"]):
                        target_bl = bases[(path["branch_id"], contact["contact_index"])] + Vector(
                            to_blender((0,path["height_m"],path["forward_m"])))
                        target = Vector(to_gltf(target_bl))
                    else:
                        target = pos.copy()
                    error = (pos-target).length
                    if path["support"]:
                        max_error = max(max_error,error)
                    min_y = min(min_y,pos.y)
                    contact_id = f"{path['branch_id']}:{contact['contact_index']}"
                    released = set(sample.get("action_target", {}).get("release_contact_ids", []))
                    planted = bool(path["support"]) and contact_id not in released
                    contacts.append({"contact_id":contact_id,"branch_id":path["branch_id"],
                                     "contact_index":contact["contact_index"],
                                     "kind":contact["kind"],"planted":planted,
                                     "phase":round(path["phase"],7),"position_m":_r6v(pos),
                                     "target_m":_r6v(target),"error_m":round(error,7)})
            root = Vector(to_gltf(arm.pose.bones["root"].head))
            depsgraph = bpy.context.evaluated_depsgraph_get(); evaluated_mesh = mannequin.evaluated_get(depsgraph)
            sampled_mesh = evaluated_mesh.to_mesh()
            mesh_points = [Vector(to_gltf(evaluated_mesh.matrix_world @ vertex.co)) for vertex in sampled_mesh.vertices]
            evaluated_mesh.to_mesh_clear()
            lo=[min(p[i] for p in mesh_points) for i in range(3)]; hi=[max(p[i] for p in mesh_points) for i in range(3)]
            minimum_support, airborne = _minimum_support(skeleton, meta["name"], sample)
            support_state = ("collapse_release" if meta["name"] == "death" and sample["phase"] >= .25
                             else "authored_schedule")
            artifact_sample = {"frame":sample["frame"],"phase":sample["phase"],"simulated_forward_m":round(sample["simulated_forward_m"],6),
                            "root_position_m":_r6v(root),"bones":bones,"contacts":contacts,
                            "minimum_support":minimum_support,"airborne":airborne,
                            "support_state":support_state,
                            "joint_limit_violations":_joint_limit_violations(arm, skeleton, profiles),
                            "mesh_bounds_min_m":_r6v(lo),"mesh_bounds_max_m":_r6v(hi)}
            if sample.get("action_target") and attack_plan and action_base is not None:
                effector = attack_plan["effector"]
                actual = Vector(to_gltf(_contact_position(arm.pose.bones[effector["bone_name"]],
                                                         {"local_point_m":effector["local_point_m"]})))
                declared = Vector(to_gltf(action_base + Vector(to_blender(
                    sample["action_target"]["target_offset_m"]))))
                artifact_sample["action_target"] = dict(sample["action_target"]) | {
                    "effector_position_m":_r6v(actual), "declared_position_m":_r6v(declared),
                    "error_m":round((actual-declared).length, 7)}
            samples.append(artifact_sample)
        out_clips.append(meta | {"samples":samples})
    clear_pose(arm)
    profile_provenance = [{k: profile[k] for k in ("binding_profile_id", "binding_profile_version",
                                                    "binding_profile_hash") if k in profile}
                          for profile in profiles]
    snapshot_keys = ("schema_version", "skeleton_id", "family", "locomotion_hint", "bones", "branches",
                     "neutral_pose", "anatomy")
    return {"schema_version":"motion-samples-1","sample_source":"evaluated_blender","skeleton_id":skeleton["skeleton_id"],
            "source_fingerprint":str(arm.get("cc_source_fingerprint", "")),
            "binding_profiles":profile_provenance,
            "skeleton_snapshot":{key:skeleton[key] for key in snapshot_keys if key in skeleton},
            "fps":planner.FPS,"bone_names":[b["name"] for b in skeleton["bones"]],"root_motion":"fixed_horizontal",
            "neutral_basis":plan["neutral_basis"],"motion_profile_id":plan["motion_profile_id"],"clips":out_clips,
            **({"attack_plan":attack_plan,
                "attack_anchor_m":_r6v(Vector(to_gltf(action_base)))}
               if attack_plan and action_base is not None else {}),
            "metrics":{"max_contact_error_m":round(max_error,7),"min_contact_height_m":round(min_y if min_y<math.inf else 0,7)}}


def run(args: dict[str, Any]) -> dict[str, Any]:
    skeleton, gait = args["skeleton"], args["gait_profile"]
    profiles = args.get("binding_profiles", [])
    polish = args.get("polish_blend", "")
    if polish:
        arm, mannequin, clips, plan = _load_polished_actions(
            polish, skeleton, gait, profiles, str(args.get("source_fingerprint", "")))
        proxy = next((obj for obj in bpy.data.objects if obj.get("cc_bind_proxy") and obj.parent == arm), None)
        if proxy is None:
            proxy = add_bind_proxy(arm)
    else:
        rigkit.reset_scene(); arm = rigkit.build_armature(ARMATURE_NAME, skeleton["bones"])
        clips = _bake_clips(arm, skeleton, gait, profiles)
        plan = json.loads(arm["_cc_plan"]); del arm["_cc_plan"]
        mannequin = ops_reference.add_reference_mesh(arm, skeleton); proxy = add_bind_proxy(arm)
    if args.get("source_fingerprint"):
        arm["cc_source_fingerprint"] = args["source_fingerprint"]
    artifact = _sample_artifact(arm, mannequin, skeleton, clips, plan, profiles)
    out_motion = args.get("out_motion") or str(Path(args["out_fbx"]).with_name("motion.json"))
    os.makedirs(os.path.dirname(out_motion), exist_ok=True)
    with open(out_motion,"w",encoding="utf-8",newline="\n") as f: json.dump(artifact,f,indent=2); f.write("\n")
    bpy.context.scene.frame_start=0; bpy.context.scene.frame_end=max(c["frames"] for c in clips)
    clear_pose(arm); rigkit.export_fbx(args["out_fbx"],[arm,mannequin,proxy],animated=True)
    if args.get("out_glb"): clear_pose(arm); rigkit.export_glb(args["out_glb"],[arm,mannequin],animated=True)
    if args.get("out_blend"):
        if polish and Path(args["out_blend"]).resolve() == Path(polish).resolve():
            raise ValueError("out_blend must not overwrite the authored polish source")
        clear_pose(arm); rigkit.save_blend(args["out_blend"])
    return {"skeleton_id":skeleton["skeleton_id"],"bones":len(arm.data.bones),"clips":clips,"motion":out_motion,
            "motion_metrics":artifact["metrics"],"reference_triangles":rigkit.triangle_count(mannequin)}
