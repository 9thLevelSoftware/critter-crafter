"""Shared bpy helpers: scene reset, armature building, materials, export. Imports only bpy + stdlib."""

from __future__ import annotations

import math
import os
from typing import Any, Iterable, Sequence

import bpy
from mathutils import Matrix, Vector

from .frame import to_blender

FPS = 30


LIVE = False  # set by `critter blender snippet` code: never wipe a user's open Blender session


def reset_scene() -> None:
    """Headless: factory-reset to an empty file. Live (Blender MCP): build in a fresh scene instead."""
    if LIVE:
        scene = bpy.data.scenes.new("critter_preview")
        if bpy.context.window is not None:
            bpy.context.window.scene = scene
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
    scene.render.fps = FPS
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0


def build_armature(name: str, bones: Sequence[dict[str, Any]]) -> bpy.types.Object:
    """Create an armature from catalog bones ({name, parent, head_m, tail_m, up_m} in glTF frame).

    Bone roll is set so that each bone's local Z axis aligns with its `up_m` vector,
    which makes rotations about local X a swing toward/away from the branch's up side.
    """
    arm_data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm_data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    ebones = arm_data.edit_bones
    for b in bones:
        eb = ebones.new(b["name"])
        eb.head = Vector(to_blender(b["head_m"]))
        eb.tail = Vector(to_blender(b["tail_m"]))
        eb.align_roll(Vector(to_blender(b.get("up_m", (0.0, 1.0, 0.0)))))
        eb.use_deform = True
    for b in bones:
        if b.get("parent"):
            ebones[b["name"]].parent = ebones[b["parent"]]
            ebones[b["name"]].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return obj


def hex_to_linear(hex_color: str) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (lin[0], lin[1], lin[2], 1.0)


def flesh_material(name: str, albedo: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = hex_to_linear(albedo)
    bsdf.inputs["Roughness"].default_value = 0.75
    mat.diffuse_color = hex_to_linear(albedo)
    return mat


def select_only(objs: Iterable[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    objs = list(objs)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def export_fbx(path: str, objs: Sequence[bpy.types.Object], animated: bool) -> None:
    """FBX flags pinned here (and only here); verified by the Unity FrameProbeTests."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    select_only(objs)
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        axis_forward="-Z",
        axis_up="Y",
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        add_leaf_bones=False,
        primary_bone_axis="Y",
        # Blender's FBX bone basis is intentionally left at the exporter
        # default.  Unity documents and compensates its constant local +Y
        # half-turn when comparing this FBX basis with the catalog basis.
        secondary_bone_axis="X",
        # Temporary IK controls are removed before export; retaining only
        # deformation bones prevents controls/helpers leaking into engine rigs.
        use_armature_deform_only=True,
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
        bake_anim=animated,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_actions=animated,
        bake_anim_force_startend_keying=True,
        bake_anim_simplify_factor=0.0,
        path_mode="STRIP",
    )


def export_glb(path: str, objs: Sequence[bpy.types.Object], animated: bool) -> None:
    """GLB in the catalog frame. Blender authoring faces +Y (see frame.py) while the glTF exporter
    maps Blender -Y to glTF +Z, so root objects are turned 180 deg about Z for the export only."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    select_only(objs)
    roots = [o for o in objs if o.parent is None or o.parent not in objs]
    saved = [o.matrix_world.copy() for o in roots]
    turn = Matrix.Rotation(math.pi, 4, "Z")
    for o in roots:
        o.matrix_world = turn @ o.matrix_world
    bpy.context.view_layer.update()
    try:
        _export_gltf(path, animated)
    finally:
        for o, m in zip(roots, saved):
            o.matrix_world = m
        bpy.context.view_layer.update()


def _export_gltf(path: str, animated: bool) -> None:
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_skins=True,
        export_def_bones=True,
        export_animations=animated,
        export_apply=False,
        # Write bind nodes from the straight edit-bone rest pose even though the
        # armature also carries baked actions and a separate neutral pose.
        export_rest_position_armature=True,
        export_reset_pose_bones=True,
    )


def triangle_count(obj: bpy.types.Object) -> int:
    """True triangle count: sum(len(poly.vertices) - 2). (Fixes the prototype's polygon-count bug.)"""
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def save_blend(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False, compress=True)
