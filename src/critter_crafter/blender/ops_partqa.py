"""Part QA: deform assembled parts through a skeleton's built clips and measure the damage.

The recipe is assembled with the same binding math as ``ops_assemble``; each part listed in
``measure`` is tagged before the join so its vertices can be followed through the deformation.
For every sampled clip frame the op compares the skinned surface with the straight bind pose:

* **edge strain**: deformed / bind edge length (a joint that pinches or tears shows up at the tails),
* **flipped faces**: faces whose normal turned against the normal carried by their dominant bone
  (collapse and candy-wrapper artefacts invert faces).

Optional renders show the assembled creature at chosen clip frames for human review.
"""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Quaternion, Vector

from . import ops_assemble, ops_skeleton, rigkit
from .frame import to_blender

TAG = "__qa__"


def _tagged_assemble(args: dict[str, Any]) -> tuple[bpy.types.Object, bpy.types.Object]:
    measure = set(args["measure"])
    original = ops_assemble._import_part_mesh

    def tagging_import(path: str) -> bpy.types.Object:
        mesh = original(path)
        for part_id in measure:
            part = args["parts"][part_id]
            if path.replace("\\", "/").endswith(part["asset"]["fbx"]):
                # A point attribute, not a vertex group: assembly checks the bone groups exactly,
                # and joined meshes fill a missing attribute with zero.
                attr = mesh.data.attributes.new(TAG + part_id, "FLOAT", "POINT")
                attr.data.foreach_set("value", [1.0] * len(mesh.data.vertices))
        return mesh

    ops_assemble._import_part_mesh = tagging_import
    try:
        return ops_assemble.assemble(args["skeleton"], args["parts"], args["library_dir"], args["recipe"],
                                     args["gait_profile"])
    finally:
        ops_assemble._import_part_mesh = original


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def run(args: dict[str, Any]) -> dict[str, Any]:
    arm, body = _tagged_assemble(args)
    mesh = body.data
    members: dict[str, set[int]] = {}
    for pid in args["measure"]:
        attr = mesh.attributes.get(TAG + pid)
        if attr is None:
            raise ValueError(f"CC_PARTQA_UNTAGGED: {pid} is not in the assembled recipe")
        values = [0.0] * len(mesh.vertices)
        attr.data.foreach_get("value", values)
        members[pid] = {i for i, v in enumerate(values) if v > 0.5}
        mesh.attributes.remove(attr)
    dominant: list[str] = []
    for v in mesh.vertices:
        best, best_w = "", -1.0
        for g in v.groups:
            if g.weight > best_w:
                best, best_w = body.vertex_groups[g.group].name, g.weight
        dominant.append(best)
    rest = [v.co.copy() for v in mesh.vertices]
    rest_normals = [p.normal.copy() for p in mesh.polygons]
    edges = {pid: [e.vertices[:] for e in mesh.edges if e.vertices[0] in verts and e.vertices[1] in verts]
             for pid, verts in members.items()}
    faces = {pid: [p.index for p in mesh.polygons if all(i in verts for i in p.vertices)]
             for pid, verts in members.items()}
    rest_len = {pid: [(rest[a] - rest[b]).length for a, b in es] for pid, es in edges.items()}
    bind = {b.name: b.matrix_local.copy() for b in arm.data.bones}

    clips = args.get("clips") or [c["name"] for c in args["skeleton"].get("asset", {}).get("clips", [])] \
        or [a.name for a in bpy.data.actions]
    step = max(1, int(args.get("frame_step", 2)))
    report: dict[str, Any] = {pid: {"clips": {}} for pid in members}
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def fresh() -> dict[str, dict[str, float]]:
        return {pid: {"strain_lo": math.inf, "strain_hi": 0.0, "strain_p01": math.inf, "strain_p99": 0.0,
                      "flipped": 0.0} for pid in members}

    def measure(worst: dict[str, dict[str, float]]) -> None:
        depsgraph.update()
        evaluated = body.evaluated_get(depsgraph)
        deformed = evaluated.to_mesh()
        pos = [v.co.copy() for v in deformed.vertices]
        normals = [p.normal.copy() for p in deformed.polygons]
        evaluated.to_mesh_clear()
        pose = {pb.name: pb.matrix @ bind[pb.name].inverted() for pb in arm.pose.bones if pb.name in bind}
        for pid in members:
            ratios = [((pos[a] - pos[b]).length / r) for (a, b), r in zip(edges[pid], rest_len[pid]) if r > 1e-7]
            flipped = 0
            for fi in faces[pid]:
                owner = dominant[mesh.polygons[fi].vertices[0]]
                carried = (pose[owner].to_3x3() @ rest_normals[fi]) if owner in pose else rest_normals[fi]
                if carried.length > 0 and normals[fi].dot(carried.normalized()) < -0.2:
                    flipped += 1
            w = worst[pid]
            w["strain_lo"] = min(w["strain_lo"], min(ratios))
            w["strain_hi"] = max(w["strain_hi"], max(ratios))
            w["strain_p01"] = min(w["strain_p01"], _quantile(ratios, 0.01))
            w["strain_p99"] = max(w["strain_p99"], _quantile(ratios, 0.99))
            w["flipped"] = max(w["flipped"], flipped / max(1, len(faces[pid])))

    for clip in clips:
        action = bpy.data.actions.get(clip)
        if action is None:
            raise ValueError(f"CC_PARTQA_CLIP: {clip} is not baked on {args['skeleton']['skeleton_id']}")
        arm.animation_data.action = action
        start, end = map(round, action.frame_range)
        worst = fresh()
        for frame in range(start, end + 1, step):
            bpy.context.scene.frame_set(frame)
            measure(worst)
        for pid in members:
            report[pid]["clips"][clip] = {k: round(v, 5) for k, v in worst[pid].items()}
    if args.get("stride"):
        worst = fresh()
        poses = _stride_poses(arm, args["skeleton"], lambda: measure(worst))
        if poses:
            for pid in members:
                report[pid]["clips"]["stride_ik"] = {k: round(v, 5) for k, v in worst[pid].items()}
                report[pid]["stride_poses"] = poses
    for pid in members:
        clips_report = report[pid]["clips"].values()
        report[pid].update(
            vertices=len(members[pid]), faces=len(faces[pid]),
            strain_p01=min(c["strain_p01"] for c in clips_report),
            strain_p99=max(c["strain_p99"] for c in clips_report),
            strain_lo=min(c["strain_lo"] for c in clips_report),
            strain_hi=max(c["strain_hi"] for c in clips_report),
            flipped=max(c["flipped"] for c in clips_report),
        )
    out: dict[str, Any] = {"skeleton_id": args["skeleton"]["skeleton_id"], "parts": report}
    if args.get("shots"):
        out["pngs"] = _render(arm, body, args)
    # A single-skeleton creature with every clip, for looking at the real part in another tool.
    if args.get("out_fbx"):
        ops_skeleton.clear_pose(arm)
        rigkit.export_fbx(args["out_fbx"], [arm, body], animated=True)
        out["fbx"] = args["out_fbx"]
    if args.get("out_glb"):
        ops_skeleton.clear_pose(arm)
        rigkit.export_glb(args["out_glb"], [arm, body], animated=True)
        out["glb"] = args["out_glb"]
    return out


def _render(arm: bpy.types.Object, body: bpy.types.Object, args: dict[str, Any]) -> list[str]:
    """Review stills framed on the whole creature across every shot (long bellies and tails included)."""
    size = int(args.get("size", 320))
    ops_assemble._setup_render(size, size)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo, hi = Vector((math.inf,) * 3), Vector((-math.inf,) * 3)
    for shot in args["shots"]:
        _pose_shot(arm, shot)
        evaluated = body.evaluated_get(depsgraph)
        deformed = evaluated.to_mesh()
        for v in deformed.vertices:
            lo = Vector(map(min, lo, v.co))
            hi = Vector(map(max, hi, v.co))
        evaluated.to_mesh_clear()
    centre, extent = (lo + hi) / 2, max(hi - lo)
    pngs = []
    for shot in args["shots"]:
        _pose_shot(arm, shot)
        ops_assemble._camera(shot.get("view", "three_quarter"), hi.z + 0.3)
        cam = bpy.context.scene.camera
        direction = (cam.location - Vector((0.0, 0.0, (hi.z + 0.3) * 0.45))).normalized()
        cam.location = centre + direction * max(20.0, extent * 4)
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam.data.ortho_scale = max(1.0, extent * 1.2)
        bpy.context.scene.render.filepath = shot["path"]
        bpy.ops.render.render(write_still=True)
        pngs.append(shot["path"])
    return pngs


def _pose_shot(arm: bpy.types.Object, shot: dict[str, Any]) -> None:
    action = bpy.data.actions[shot["clip"]]
    arm.animation_data.action = action
    start, end = action.frame_range
    frame = shot["frame"] if "frame" in shot else start + float(shot.get("at", 0.0)) * (end - start)
    bpy.context.scene.frame_set(int(round(frame)))

def _neutral(arm: bpy.types.Object, skeleton: dict[str, Any]) -> None:
    ops_skeleton.clear_pose(arm)
    for item in skeleton["neutral_pose"]["rotations"]:
        x, y, z, w = item["rotation_xyzw"]
        arm.pose.bones[item["bone_name"]].rotation_quaternion = Quaternion((w, x, y, z))
    arm.pose.bones["root"].location = ops_skeleton._root_local(arm, skeleton["neutral_pose"]["root_offset_m"])


def _stride_poses(arm: bpy.types.Object, skeleton: dict[str, Any], measure: Any) -> list[str]:
    """Runtime foot placement moves stepping legs beyond anything the baked overlays do (walk and run
    keep locomotor chains at neutral). Approximate its extremes with Blender IK from the neutral
    stance: every foot at the front and back of its stroke and at the swing apex. The judge compares
    real and placeholder parts on the same poses, so Blender's solver standing in for Unity's is fair.
    """
    loc = skeleton.get("locomotion") or {}
    legs = [leg for leg in loc.get("legs", []) if leg.get("chain_bones")]
    if loc.get("mode") != "legs" or not legs:
        return []
    arm.animation_data.action = None
    empties, constraints = [], []
    for leg in legs:
        empty = bpy.data.objects.new(f"qa_ik_{leg['branch_id']}", None)
        bpy.context.scene.collection.objects.link(empty)
        tip = arm.pose.bones[leg["chain_bones"][-1]]
        ik = tip.constraints.new("IK")
        ik.target = empty
        ik.chain_count = len(leg["chain_bones"])
        ik.use_tail = True
        ik.use_stretch = False
        empties.append((leg, empty))
        constraints.append((tip, ik))
    poses = {
        "stride_front": lambda leg: (0.0, 0.0, 0.5 * leg["stroke_m"]),
        "stride_back": lambda leg: (0.0, 0.0, -0.5 * leg["stroke_m"]),
        "swing_apex": lambda leg: (0.0, leg["clearance_m"], 0.0),
    }
    try:
        for offset in poses.values():
            _neutral(arm, skeleton)
            for leg, empty in empties:
                home = leg["home_m"]
                d = offset(leg)
                empty.location = Vector(to_blender((home[0] + d[0], home[1] + d[1], home[2] + d[2])))
            bpy.context.view_layer.update()
            measure()
    finally:
        for bone, ik in constraints:
            bone.constraints.remove(ik)
        for _, empty in empties:
            bpy.data.objects.remove(empty, do_unlink=True)
        ops_skeleton.clear_pose(arm)
    return list(poses)
