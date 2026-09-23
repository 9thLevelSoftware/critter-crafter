"""Assemble a recipe in Blender with the same binding math as the Unity package, then render and/or
export a baked single-mesh, single-skeleton creature (FBX/GLB with the full clip set).

Binding (docs/frame.md): part mesh -> snapFrame * Scale(s); vertex groups b<i> renamed to the
branch bones <branch>_b<min(i, n-1)>; connectors: b0 -> attach bone, b1 -> <branch>_b0.
"""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import ops_skeleton, rigkit
from .frame import MATRIX, to_blender

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
    mesh = next(o for o in new if o.type == "MESH")
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
    for g in list(obj.vertex_groups):
        target = mapping.get(g.name)
        if target is None:
            continue
        existing = obj.vertex_groups.get(target)
        if existing is None:
            g.name = target
            continue
        # merge into existing target group (clamped chains map several bones onto one)
        idx = g.index
        for v in obj.data.vertices:
            for ge in v.groups:
                if ge.group == idx:
                    existing.add([v.index], ge.weight, "ADD")
        obj.vertex_groups.remove(g)


def assemble(skeleton: dict[str, Any], parts: dict[str, dict[str, Any]], library_dir: str,
             recipe: dict[str, Any], gait: dict[str, Any]) -> tuple[bpy.types.Object, bpy.types.Object]:
    rigkit.reset_scene()
    arm = rigkit.build_armature(ops_skeleton.ARMATURE_NAME, skeleton["bones"])
    ops_skeleton._bake_clips(arm, skeleton, gait)
    branches = {b["branch_id"]: b for b in skeleton["branches"]}
    pieces = []
    for fill in recipe["fills"]:
        br = branches[fill["branch_id"]]
        part = parts[fill["part_id"]]
        s = br["length_m"] / part["length_m"]
        mesh = _import_part_mesh(f"{library_dir}/{part['asset']['fbx']}")
        names = br["bone_names"]
        _rename_groups(mesh, {f"b{i}": names[min(i, len(names) - 1)] for i in range(32)})
        mesh.data.transform(snap_matrix_blender(br["snap"], s))
        pieces.append(mesh)
        if fill["connector_part_id"]:
            conn = parts[fill["connector_part_id"]]
            cm = _import_part_mesh(f"{library_dir}/{conn['asset']['fbx']}")
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
    ground.from_pydata([(-3, -3, 0), (3, -3, 0), (3, 3, 0), (-3, 3, 0)], [], [(0, 1, 2, 3)])
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
