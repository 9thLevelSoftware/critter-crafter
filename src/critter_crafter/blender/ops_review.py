"""Render actual authored/built actions with stable animated bounds and a ground grid."""
from __future__ import annotations
import json
import math
from pathlib import Path
import bpy
from mathutils import Quaternion, Vector
from . import ops_assemble, ops_skeleton, rigkit
from .frame import to_blender


def _grid() -> None:
    vertices, faces = [], []
    for line in range(-12, 13):
        coordinate = line * .5
        width = .005 if line else .012
        for horizontal in (False, True):
            start = len(vertices)
            points = [(coordinate-width, -6, .002), (coordinate+width, -6, .002),
                      (coordinate+width, 6, .002), (coordinate-width, 6, .002)]
            vertices.extend((p[1],p[0],p[2]) if horizontal else p for p in points)
            faces.append(tuple(range(start,start+4)))
    mesh = bpy.data.meshes.new("ReviewGrid")
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new("ReviewGrid", mesh)
    obj.data.materials.append(rigkit.flesh_material("M_Grid", "#505862"))
    bpy.context.scene.collection.objects.link(obj)


def run(args: dict) -> dict:
    bpy.ops.wm.open_mainfile(filepath=args["blend"])
    arm = bpy.data.objects[ops_skeleton.ARMATURE_NAME]
    skeleton = args["skeleton"]
    motion = json.loads(Path(args["motion"]).read_text(encoding="utf-8"))
    minima = [s["mesh_bounds_min_m"] for c in motion["clips"] for s in c["samples"]]
    maxima = [s["mesh_bounds_max_m"] for c in motion["clips"] for s in c["samples"]]
    low = [min(v[i] for v in minima) for i in range(3)]
    high = [max(v[i] for v in maxima) for i in range(3)]
    centre = Vector(to_blender([(a+b)/2 for a,b in zip(low,high)]))
    extent = max(b-a for a,b in zip(low,high))
    ops_assemble._setup_render(int(args.get("size", 384)), int(args.get("size", 384)))
    _grid()
    camera = bpy.data.objects.new("ReviewCamera", bpy.data.cameras.new("ReviewCamera"))
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(.6, extent*1.3)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    views = {"front": (0,0,1), "side": (1,0,0), "top": (0,1,.001), "three_quarter": (1,.6,1)}
    outputs = []
    for shot in args["shots"]:
        if shot.get("clip") == "neutral":
            ops_skeleton.clear_pose(arm)
            neutral = skeleton["neutral_pose"]
            for item in neutral["rotations"]:
                x,y,z,w = item["rotation_xyzw"]
                arm.pose.bones[item["bone_name"]].rotation_quaternion = Quaternion((w,x,y,z))
            arm.pose.bones["root"].location = ops_skeleton._root_local(arm, neutral["root_offset_m"])
            bpy.context.view_layer.update()
        else:
            arm.animation_data.action = bpy.data.actions[shot["clip"]]
            bpy.context.scene.frame_set(shot.get("frame", 0))
        direction = Vector(to_blender(views[shot.get("view", "three_quarter")])).normalized()
        camera.location = centre + direction * max(10., extent*4)
        camera.rotation_euler = (centre-camera.location).to_track_quat("-Z", "Y").to_euler()
        output = Path(shot["path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(output)
        bpy.ops.render.render(write_still=True)
        outputs.append(str(output))
    return {"skeleton_id": skeleton["skeleton_id"], "pngs": outputs}
