"""Assemble a recipe in Blender with the same binding math as the Unity package, then render and/or
export a baked single-mesh, single-skeleton creature (FBX/GLB with the full clip set).

Binding: exact profile chains only, with uniform scale and complete socket orientation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import ops_skeleton, rigkit
from .frame import MATRIX, to_blender
from ..recipes.generator import connector_accepted, part_accepted

# glTF -> Blender basis change (docs/frame.md), as a rotation matrix.
C = Matrix(MATRIX)


def snap_matrix_blender(snap: dict[str, Any], s: float) -> Matrix:
    x, y, z, w = snap["rotation_xyzw"]
    r_gltf = Quaternion((w, x, y, z)).to_matrix()
    r_bl = C @ r_gltf @ C.inverted()
    t = Vector(to_blender(snap["position_m"]))
    return Matrix.Translation(t) @ r_bl.to_4x4() @ Matrix.Scale(s, 4)


def _import_part_mesh(path: str) -> bpy.types.Object:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if len(meshes) != 1:
        raise ValueError(f"CC_PART_RENDERERS: {path}: expected one skinned mesh, got {len(meshes)}")
    mesh = meshes[0]
    if not any(m.type == "ARMATURE" for m in mesh.modifiers):
        raise ValueError(f"CC_PART_SKIN: {path}: missing armature modifier")
    if not 1 <= len(mesh.data.materials) <= 2:
        raise ValueError(f"CC_PART_MATERIALS: {path}: expected one or two materials")
    for vertex in mesh.data.vertices:
        weights = [g.weight for g in vertex.groups if g.weight > 0]
        if not weights or len(weights) > 4 or abs(sum(weights) - 1.0) > 1e-4:
            raise ValueError(f"CC_PART_WEIGHTS: {path}: vertex {vertex.index}")
    world = mesh.matrix_world.copy()
    mesh.parent = None
    mesh.modifiers.clear()
    mesh.data.transform(world)
    mesh.matrix_world = Matrix.Identity(4)
    for o in new:
        if o is not mesh:
            bpy.data.objects.remove(o, do_unlink=True)
    return mesh


def _rename_groups(obj: bpy.types.Object, mapping: dict[str, str]) -> None:
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("CC_BIND_TOPOLOGY: non-bijective bone mapping")
    for g in list(obj.vertex_groups):
        target = mapping.get(g.name)
        if target is None:
            raise ValueError(f"CC_BIND_TOPOLOGY: unexpected part bone {g.name}")
        g.name = target


def assemble(skeleton: dict[str, Any], parts: dict[str, dict[str, Any]], library_dir: str,
             recipe: dict[str, Any], gait: dict[str, Any]) -> tuple[bpy.types.Object, bpy.types.Object]:
    built_blend = skeleton.get("asset", {}).get("blend")
    if built_blend:
        path = Path(library_dir) / built_blend
        if not path.is_file():
            raise ValueError(f"CC_BUILT_CLIPS_MISSING: {path}")
        bpy.ops.wm.open_mainfile(filepath=str(path))
        arm = bpy.data.objects.get(ops_skeleton.ARMATURE_NAME)
        if arm is None:
            raise ValueError("CC_BUILT_ARMATURE_MISSING")
        for obj in list(bpy.context.scene.objects):
            if obj is not arm:
                bpy.data.objects.remove(obj, do_unlink=True)
        ops_skeleton.clear_pose(arm)
    else:
        if any(branch.get("binding_profile_id") for branch in skeleton["branches"]):
            raise ValueError("CC_BUILT_CLIPS_MISSING: schema-v3 assembly requires its built deformation clips")
        rigkit.reset_scene()
        arm = rigkit.build_armature(ops_skeleton.ARMATURE_NAME, skeleton["bones"])
        ops_skeleton._bake_clips(arm, skeleton, gait)
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    pieces = []
    for fill in recipe["fills"]:
        br = branches[fill["branch_id"]]
        part = parts[fill["part_id"]]
        if not part_accepted(part, br):
            raise ValueError(f"CC_PART_REJECTED: {part['part_id']} -> {br['branch_id']}")
        s = br["length_m"] / part["length_m"]
        mesh = _import_part_mesh(f"{library_dir}/{part['asset']['fbx']}")
        names = br["bone_names"]
        expected = {f"b{i}" for i in range(len(names))}
        if {g.name for g in mesh.vertex_groups} != expected:
            raise ValueError(f"CC_BIND_TOPOLOGY: {part['part_id']}: expected {sorted(expected)}")
        _rename_groups(mesh, {f"b{i}": name for i, name in enumerate(names)})
        mesh.data.transform(snap_matrix_blender(br["snap"], s))
        pieces.append(mesh)
        if fill["connector_part_id"]:
            conn = parts[fill["connector_part_id"]]
            if not connector_accepted(conn, br):
                raise ValueError(f"CC_CONNECTOR_MISMATCH: {conn['part_id']} -> {br['branch_id']}")
            cm = _import_part_mesh(f"{library_dir}/{conn['asset']['fbx']}")
            if {g.name for g in cm.vertex_groups} != {"b0", "b1"}:
                raise ValueError(f"CC_BIND_TOPOLOGY: {conn['part_id']}: expected ['b0', 'b1']")
            _rename_groups(cm, {"b0": br["attach_bone"], "b1": names[0]})
            cm.data.transform(snap_matrix_blender(br["snap"], 1.0))
            pieces.append(cm)
    rigkit.select_only(pieces)
    bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = recipe["recipe_id"]
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return arm, body


def _setup_render(width: int, height: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.055, 0.07, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    sun = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    scene.collection.objects.link(sun)
    fill = bpy.data.objects.new("Fill", bpy.data.lights.new("Fill", "SUN"))
    fill.data.energy = 1.0
    fill.rotation_euler = (math.radians(70), 0, math.radians(-140))
    scene.collection.objects.link(fill)
    ground = bpy.data.meshes.new("Ground")
    ground.from_pydata([(-12, -12, 0), (12, -12, 0), (12, 12, 0), (-12, 12, 0)], [], [(0, 1, 2, 3)])
    g = bpy.data.objects.new("Ground", ground)
    g.data.materials.append(rigkit.flesh_material("M_Ground", "#2a2d33"))
    scene.collection.objects.link(g)


def _camera(view: str, height: float) -> None:
    """view: 'iso' matches the game's ortho iso camera (offset 16,18,16 in Unity = glTF (-16,18,16))."""
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(2.2, height * 1.35)
    target = Vector((0, 0, height * 0.45))
    offsets = {
        "iso": Vector(to_blender((-16, 18, 16))),
        "front": Vector(to_blender((0, 2, 20))),
        "side": Vector(to_blender((20, 2, 0))),
        "three_quarter": Vector(to_blender((14, 6, 14))),
    }
    direction = offsets[view].normalized()
    cam.location = target + direction * 20
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam


def run(args: dict[str, Any]) -> dict[str, Any]:
    arm, body = assemble(args["skeleton"], args["parts"], args["library_dir"], args["recipe"], args["gait_profile"])
    out: dict[str, Any] = {"recipe_id": args["recipe"]["recipe_id"], "triangles": rigkit.triangle_count(body)}
    if args.get("frames_pattern"):
        # animated review: render several clips/frames into numbered PNGs ({i:03d} in the pattern)
        height = max(v.co.z for v in body.data.vertices) + 0.3
        _setup_render(int(args.get("size", 256)), int(args.get("size", 256)))
        _camera(args.get("view", "three_quarter"), max(height, float(args.get("min_height", 0.0))))
        pngs = []
        for clip_name, frame in args["frames"]:
            arm.animation_data.action = bpy.data.actions[clip_name]
            bpy.context.scene.frame_set(int(frame))
            path = args["frames_pattern"].format(i=len(pngs))
            bpy.context.scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            pngs.append(path)
        out["pngs"] = pngs
    if args.get("out_png"):
        clip = args.get("clip", "idle")
        arm.animation_data.action = bpy.data.actions[clip]
        frame = int(args.get("frame", 0))
        bpy.context.scene.frame_set(frame)
        height = max(v.co.z for v in body.data.vertices) + 0.3
        _setup_render(int(args.get("size", 512)), int(args.get("size", 512)))
        _camera(args.get("view", "iso"), height)
        bpy.context.scene.render.filepath = args["out_png"]
        bpy.ops.render.render(write_still=True)
        out["png"] = args["out_png"]
    if args.get("out_fbx"):
        ops_skeleton.clear_pose(arm)
        rigkit.export_fbx(args["out_fbx"], [arm, body], animated=True)
        out["fbx"] = args["out_fbx"]
    if args.get("out_glb"):
        ops_skeleton.clear_pose(arm)
        rigkit.export_glb(args["out_glb"], [arm, body], animated=True)
        out["glb"] = args["out_glb"]
    return out
