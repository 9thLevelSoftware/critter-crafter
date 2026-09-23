"""Build the weighted anatomical reference mannequin used by skeleton review/export."""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Quaternion, Vector

from . import rigkit
from .frame import to_blender, to_gltf

REFERENCE_NAME = "ReferenceMannequin"
_SIDES = 12


def _branch_by_bone(skeleton: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: branch for branch in skeleton["branches"] for name in branch["bone_names"]}


def _radius(branch: dict[str, Any] | None, bone_length: float) -> float:
    """Return a conservative anatomical radius from the authored branch girth."""
    girth = float((branch or {}).get("girth_m", 0.0))
    if girth <= 0.0:
        girth = max(.045, min(.28, bone_length * .24))
    return max(.018, min(bone_length * .32, girth * .5))


def _basis(axis: Vector, up_hint: Vector) -> tuple[Vector, Vector]:
    z = axis.normalized()
    up = up_hint - z * up_hint.dot(z)
    if up.length < 1e-6:
        up = Vector((0, 0, 1)) if abs(z.z) < .9 else Vector((0, 1, 0))
        up -= z * up.dot(z)
    up.normalize()
    return up, z.cross(up).normalized()


def _blend(*influences: tuple[str, float]) -> dict[str, float]:
    """Normalize a compact vertex influence set before assigning vertex groups."""
    total = sum(value for _, value in influences)
    return {name: value / total for name, value in influences if value > 0.0}


def _append_segment(verts: list[tuple[float, float, float]], faces: list[tuple[int, ...]],
                    weights: list[dict[str, float]], head: Vector, tail: Vector,
                    head_radius: float, tail_radius: float, head_weights: dict[str, float],
                    tail_weights: dict[str, float], up_hint: Vector, terminal: bool,
                    foot_terminal: bool = False) -> None:
    """Append a rounded tapered segment without extending beyond its endpoints."""
    axis = tail - head
    if axis.length < 1e-6:
        return
    up, side = _basis(axis, up_hint)
    # Terminal tips finish at their authored bone tail, avoiding added geometry
    # below the declared foot contact plane.
    if terminal and foot_terminal:
        # Keep the authored contact at the exact tail while carrying the final
        # limb volume closer to it.  This reads as a rounded toe/sole pad
        # rather than a long stilt-like cone and adds no geometry below it.
        rings = ((0.0, head_radius * .72), (.13, head_radius), (.78, tail_radius),
                 (.93, tail_radius * .92), (.975, tail_radius * .42), (1.0, 0.0))
    else:
        rings = ((0.0, head_radius * .72), (.13, head_radius),
                 (.84, tail_radius), (1.0, 0.0 if terminal else tail_radius * .72))
    start = len(verts)
    for fraction, radius in rings:
        centre = head.lerp(tail, fraction)
        influence = head_weights if fraction < .5 else tail_weights
        for j in range(_SIDES):
            angle = math.tau * j / _SIDES
            point = centre + radius * (math.cos(angle) * up + math.sin(angle) * side)
            verts.append(tuple(point)); weights.append(influence)
    for ring in range(len(rings) - 1):
        for j in range(_SIDES):
            a = start + ring * _SIDES + j
            b = start + ring * _SIDES + (j + 1) % _SIDES
            faces.append((a, b, b + _SIDES, a + _SIDES))


def _append_joint(verts: list[tuple[float, float, float]], faces: list[tuple[int, ...]],
                  weights: list[dict[str, float]], centre: Vector, radius: float,
                  influence: dict[str, float], axis: Vector, up_hint: Vector,
                  axial_scale: float = 1.0) -> None:
    """Append a UV ellipsoid for hips, shoulders, elbows, and cranial mass."""
    if axis.length < 1e-6:
        return
    up, side = _basis(axis, up_hint)
    start = len(verts); latitudes = 6; axis = axis.normalized()
    for lat in range(latitudes + 1):
        phi = math.pi * lat / latitudes
        ring_radius = math.sin(phi) * radius
        axial = math.cos(phi) * radius * axial_scale
        for j in range(_SIDES):
            angle = math.tau * j / _SIDES
            point = centre + axial * axis + ring_radius * (math.cos(angle) * up + math.sin(angle) * side)
            verts.append(tuple(point)); weights.append(influence)
    for lat in range(latitudes):
        for j in range(_SIDES):
            a = start + lat * _SIDES + j
            b = start + lat * _SIDES + (j + 1) % _SIDES
            faces.append((a, b, b + _SIDES, a + _SIDES))


def _is_head(branch: dict[str, Any] | None) -> bool:
    return bool(branch and (branch.get("template", "").startswith("head") or branch.get("gait_role") == "head"))


def add_reference_mesh(arm: bpy.types.Object, skeleton: dict[str, Any],
                       name: str = REFERENCE_NAME) -> bpy.types.Object:
    """Add one smooth, weighted reference mesh built from compiled bind anatomy.

    Limbs follow exact straight bind bones. Every non-root branch is bridged
    from its parent attachment endpoint to its branch origin with shared
    parent/child weights, so visual and deformation continuity agree at sockets.
    """
    by_bone = _branch_by_bone(skeleton)
    bones = {bone["name"]: bone for bone in skeleton["bones"]}
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    weights: list[dict[str, float]] = []
    foot_pad_count = 0
    for bone in skeleton["bones"]:
        name_bone = bone["name"]
        if name_bone == "root":
            continue
        head = Vector(to_blender(bone["head_m"])); tail = Vector(to_blender(bone["tail_m"]))
        axis = tail - head
        if axis.length < 1e-6:
            continue
        branch = by_bone.get(name_bone); radius = _radius(branch, axis.length)
        terminal = bool(branch and branch["bone_names"][-1] == name_bone)
        foot_terminal = bool(terminal and branch and any(contact.get("kind") == "foot"
                                                          for contact in branch.get("contacts", [])))
        parent_name = bone.get("parent") or ""
        head_weight = (_blend((parent_name, .35), (name_bone, .65))
                       if parent_name and parent_name != "root" else {name_bone: 1.0})
        up = Vector(to_blender(bone.get("up_m", (0, 1, 0))))
        _append_segment(verts, faces, weights, head, tail, radius, radius * (.52 if terminal else .88),
                        head_weight, {name_bone: 1.0}, up, terminal, foot_terminal)
        if foot_terminal:
            foot_pad_count += 1
            pad_radius = radius * .52
            # The pad's distal axial pole lands exactly at the declared tail;
            # its compressed volume stays behind the contact rather than
            # inventing a separate floating foot mesh.
            pad_centre = tail - axis.normalized() * (pad_radius * .28)
            _append_joint(verts, faces, weights, pad_centre, pad_radius, {name_bone: 1.0}, axis, up, .28)
        if _is_head(branch):
            _append_joint(verts, faces, weights, head.lerp(tail, .55), max(radius * 1.8, axis.length * .38),
                          {name_bone: 1.0}, axis, up, 1.18)
        elif not terminal:
            # Contact coordinates use this exact segment radius for their
            # ventral surface.  A larger joint sphere would cross that plane.
            _append_joint(verts, faces, weights, head, radius, head_weight, axis, up)
    # Explicit socket bridges cover authored shoulder/hip offsets from the
    # parent joint to the child branch origin.
    for branch in skeleton["branches"]:
        if not branch.get("parent_branch"):
            continue
        child_name = branch["bone_names"][0]; parent_name = branch.get("attach_bone", "")
        parent = bones.get(parent_name); child = bones.get(child_name)
        if not parent or not child:
            continue
        socket = Vector(to_blender(parent["tail_m"])); origin = Vector(to_blender(child["head_m"]))
        offset = origin - socket
        if offset.length < 1e-5:
            continue
        # A socket can span a long diagonal from a torso attachment to a body
        # branch.  Its envelope must be sized from the adjacent anatomy, not
        # that span, or it becomes a ground-penetrating balloon at the child.
        child_axis = Vector(to_blender(child["tail_m"])) - Vector(to_blender(child["head_m"]))
        parent_axis = Vector(to_blender(parent["tail_m"])) - Vector(to_blender(parent["head_m"]))
        child_radius = _radius(branch, child_axis.length)
        parent_radius = _radius(by_bone.get(parent_name), parent_axis.length)
        # A foot-bearing socket is close to the support plane.  Equal LBS
        # blending lets the moving leg pull its full bridge envelope below
        # that plane during stance.  Keep both attachment influences, while
        # anchoring the socket volume primarily to the body-side bone.
        shared = (_blend((parent_name, .75), (child_name, .25))
                  if any(contact.get("kind") == "foot" for contact in branch.get("contacts", []))
                  else _blend((parent_name, .5), (child_name, .5)))
        up = Vector(to_blender(child.get("up_m", (0, 1, 0))))
        _append_segment(verts, faces, weights, socket, origin, min(parent_radius, child_radius) * .92,
                        child_radius * .94, shared, shared, up, False)
        _append_joint(verts, faces, weights, origin, child_radius, shared, offset, up)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces); mesh.validate(); mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(rigkit.flesh_material("M_ReferenceAnatomy", "#9b6874"))
    groups = {bone["name"]: obj.vertex_groups.new(name=bone["name"]) for bone in skeleton["bones"]}
    for index, influences in enumerate(weights):
        for bone_name, value in influences.items():
            if bone_name in groups:
                groups[bone_name].add([index], value, "REPLACE")
    obj.parent = arm
    obj.modifiers.new("Armature", "ARMATURE").object = arm
    obj["cc_reference_technique"] = "anatomical_socket_volumes_v2"
    obj["cc_reference_joint_radius_policy"] = "adjacent_bone_exact_v1"
    obj["cc_reference_foot_terminal_pads"] = foot_pad_count
    obj["cc_straight_bind"] = True
    return obj


def reference_mesh_metrics(obj: bpy.types.Object) -> dict[str, Any]:
    """Return compact, host-observed limits used by the Blender integration test."""
    influences = [sum(group.weight for group in vertex.groups) for vertex in obj.data.vertices]
    counts = [len(vertex.groups) for vertex in obj.data.vertices]
    return {
        "triangles": rigkit.triangle_count(obj),
        "materials": len(obj.data.materials),
        "vertices": len(obj.data.vertices),
        "max_influences": max(counts, default=0),
        "min_weight_sum": min(influences, default=0.0),
        "max_weight_sum": max(influences, default=0.0),
        "technique": str(obj.get("cc_reference_technique", "")),
        "joint_radius_policy": str(obj.get("cc_reference_joint_radius_policy", "")),
        "foot_terminal_pads": int(obj.get("cc_reference_foot_terminal_pads", 0)),
    }


def run(args: dict[str, Any]) -> dict[str, Any]:
    """Small headless entry point for reference-mesh validation."""
    rigkit.reset_scene()
    arm = rigkit.build_armature("ReferenceValidationArmature", args["skeleton"]["bones"])
    obj = add_reference_mesh(arm, args["skeleton"])
    metrics = reference_mesh_metrics(obj)
    if args.get("neutral"):
        neutral = args["skeleton"]["neutral_pose"]
        for item in neutral["rotations"]:
            x, y, z, w = item["rotation_xyzw"]
            arm.pose.bones[item["bone_name"]].rotation_quaternion = Quaternion((w, x, y, z))
        world_offset = Vector(to_blender(neutral["root_offset_m"]))
        root = arm.pose.bones["root"]
        root.location = root.bone.matrix_local.to_3x3().inverted() @ world_offset
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        metrics["neutral_min_y_m"] = min(to_gltf(evaluated.matrix_world @ vertex.co)[1] for vertex in mesh.vertices)
        evaluated.to_mesh_clear()
    return metrics
