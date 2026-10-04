"""Connector structural, FBX attachment and same-recipe loft/SDF deformation review."""
from __future__ import annotations

import copy
import json
import math
import shutil
from pathlib import Path
from typing import Any

import bpy
from mathutils import Quaternion, Vector

from . import ops_assemble, ops_binding_fixture, ops_partqa, ops_placeholder, ops_skeleton, rigkit
from .connector_surface import grid_spec, validate_mesh
from .frame import to_gltf

CLIPS = ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death")


def _limits(arm: Any, skeleton: dict, profile: dict, part_id: str, recipe: dict, measure: Any) -> list[str]:
    selected = {fill["branch_id"] for fill in recipe["fills"] if fill.get("connector_part_id") == part_id}
    poses = []
    arm.animation_data.action = None
    for branch in skeleton["branches"]:
        if branch["branch_id"] not in selected:
            continue
        bone = arm.pose.bones[branch["bone_names"][0]]
        for axis, component, sign in (("swing_x", 0, -1), ("twist", 1, 1), ("swing_y", 2, 1)):
            for boundary in profile["joints"][0]["limits_deg"][axis]:
                ops_partqa._neutral(arm, skeleton)
                angles = [0.0, 0.0, 0.0]
                angles[component] = math.radians(boundary) * sign
                from mathutils import Euler
                bone.rotation_quaternion = Euler(angles, "XYZ").to_quaternion()
                bpy.context.view_layer.update()
                pose_name = f"{branch['branch_id']}.{axis}.{boundary}"
                measure(pose_name)
                poses.append(pose_name)
    ops_skeleton.clear_pose(arm)
    return poses


def _evaluate(args: dict, part: dict, profile: dict) -> dict:
    qa = {**args, "measure": [part["part_id"]]}
    arm, body = ops_partqa._tagged_assemble(qa)
    mesh = body.data
    attr = mesh.attributes[ops_partqa.TAG + part["part_id"]]
    members = {i for i, value in enumerate(attr.data) if value.value > .5}
    mesh.attributes.remove(attr)
    if not members:
        raise ValueError("CC_CONNECTORQA_UNTAGGED: connector absent from recipe")
    rest = [v.co.copy() for v in mesh.vertices]
    edges = [tuple(e.vertices) for e in mesh.edges if all(v in members for v in e.vertices)]
    faces = [p.index for p in mesh.polygons if all(v in members for v in p.vertices)]
    lengths = [(rest[a] - rest[b]).length for a, b in edges]
    if not edges or not faces or min(lengths) <= 1e-7:
        raise ValueError("CC_CONNECTOR_DEGENERATE: imported connector has missing or collapsed edges")
    normals = [p.normal.copy() for p in mesh.polygons]
    dominant = {i: body.vertex_groups[max(mesh.vertices[i].groups, key=lambda g: g.weight).group].name for i in members}
    bind = {bone.name: bone.matrix_local.copy() for bone in arm.data.bones}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    report = {"strain_p01": math.inf, "strain_p99": 0.0, "flipped": 0.0, "max_penetration_m": 0.0,
              "clips": {}, "vertices": len(members), "faces": len(faces), "worst_poses": {}}

    def measure(pose_name: str = "stride_ik") -> None:
        depsgraph.update()
        evaluated = body.evaluated_get(depsgraph)
        deformed = evaluated.to_mesh()
        try:
            ratios = [(deformed.vertices[a].co - deformed.vertices[b].co).length / length
                      for (a, b), length in zip(edges, lengths)]
            flipped = 0
            for fi in faces:
                owner = dominant[mesh.polygons[fi].vertices[0]]
                carried = (arm.pose.bones[owner].matrix @ bind[owner].inverted()).to_3x3() @ normals[fi]
                if carried.length > 0 and deformed.polygons[fi].normal.dot(carried.normalized()) < -.2:
                    flipped += 1
            values = {"strain_p01": ops_partqa._quantile(ratios, .01),
                      "strain_p99": ops_partqa._quantile(ratios, .99),
                      "flipped": flipped / len(faces),
                      "max_penetration_m": max(0.0, -min(
                          (body.matrix_world @ deformed.vertices[i].co).z for i in members))}
            for key, value in values.items():
                improved = value < report[key] if key == "strain_p01" else value > report[key]
                if improved:
                    report[key] = value
                    report["worst_poses"][key] = pose_name
        finally:
            evaluated.to_mesh_clear()

    ops_skeleton.clear_pose(arm)
    measure("bind")
    ops_partqa._neutral(arm, args["skeleton"])
    measure("neutral")
    for clip in CLIPS:
        action = bpy.data.actions.get(clip)
        if action is None:
            raise ValueError(f"CC_CONNECTORQA_CLIP: missing required clip {clip}")
        arm.animation_data.action = action
        start, end = map(round, action.frame_range)
        for frame in range(start, end + 1):
            bpy.context.scene.frame_set(frame)
            measure(f"{clip}:{frame}")
        report["clips"][clip] = end - start + 1
    report["stride_poses"] = ops_partqa._stride_poses(arm, args["skeleton"], measure)
    report["joint_limit_poses"] = _limits(arm, args["skeleton"], profile, part["part_id"], args["recipe"], measure)
    return report


def _stage_inputs(args: dict, out: Path) -> dict:
    """Keep all assembly asset paths relative to one isolated review library root."""
    staged = copy.deepcopy(args)
    source_root = Path(args["library_dir"]).resolve()

    def copy_asset(asset: dict, key: str, destination: Path) -> None:
        original = source_root / asset[key]
        if not original.is_file():
            raise ValueError(f"CC_CONNECTORQA_INPUT: missing built asset {original}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        asset[key] = destination.relative_to(out).as_posix()

    copy_asset(staged["skeleton"]["asset"], "blend", out / "inputs" / "skeleton" / "master.blend")
    for part_id, part in staged["parts"].items():
        if part_id == args["part"]["part_id"]:
            continue
        copy_asset(part["asset"], "fbx", out / "inputs" / part_id / "mesh.fbx")
        if part["asset"].get("albedo_png"):
            copy_asset(part["asset"], "albedo_png", out / "inputs" / part_id / "albedo.png")
    staged["library_dir"] = str(out)
    return staged


def _comparison(args: dict, part: dict, profile: dict, out: Path) -> dict:
    bounds = [Vector((math.inf,) * 3), Vector((-math.inf,) * 3)]
    images = {"loft": [], "sdf": []}

    def visit(method: str, render: bool) -> None:
        qa = copy.deepcopy(args)
        qa["parts"][part["part_id"]]["asset"]["fbx"] = f"{method}.fbx"
        qa["measure"] = [part["part_id"]]
        arm, body = ops_partqa._tagged_assemble(qa)
        ops_assemble._setup_render(int(args.get("size", 320)), int(args.get("size", 320)))
        index = 0
        centre = (bounds[0] + bounds[1]) / 2 if render else None
        extent = max(bounds[1] - bounds[0]) if render else None

        def sample(pose_name: str = "") -> None:
            nonlocal index
            bpy.context.view_layer.update()
            if not render:
                evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh = evaluated.to_mesh()
                try:
                    for vertex in mesh.vertices:
                        point = body.matrix_world @ vertex.co
                        bounds[0] = Vector(map(min, bounds[0], point))
                        bounds[1] = Vector(map(max, bounds[1], point))
                finally:
                    evaluated.to_mesh_clear()
            else:
                ops_assemble._camera("three_quarter", 1.0)
                camera = bpy.context.scene.camera
                direction = Vector((1, -1, .65)).normalized()
                camera.location = centre + direction * max(20.0, extent * 4)
                camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
                camera.data.ortho_scale = max(1.0, extent * 1.2)
                path = str(out / f"{method}_{index:03}.png")
                bpy.context.scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                images[method].append(path)
            index += 1

        ops_skeleton.clear_pose(arm)
        sample()
        ops_partqa._neutral(arm, args["skeleton"])
        sample()
        for shot in args["shots"]:
            ops_partqa._pose_shot(arm, shot)
            sample()
        _limits(arm, args["skeleton"], profile, part["part_id"], args["recipe"], sample)

    for method in ("loft", "sdf"):
        visit(method, False)
    for method in ("loft", "sdf"):
        visit(method, True)
    return {"pngs": images, "shared_bounds_blender_m": [list(point) for point in bounds],
            "order": "bind, neutral, requested shots, selected socket joint-limit extrema"}


def _asymmetric(part: dict, profile: dict, out: Path, source: list) -> dict:
    fixture = out / "asymmetric"
    body_part, connector, _ = ops_binding_fixture._fixture_parts(fixture)
    connector.update(copy.deepcopy(part))
    connector["asset"] = {"fbx": "sdf.fbx"}
    body_part["asset"]["fbx"] = "asymmetric/" + body_part["asset"]["fbx"]
    base = {key: part[key] for key in ("binding_profile_id", "binding_profile_version", "binding_profile_hash", "girth_m", "girth_mm", "side")}
    # The public triangle fixture is an attachment marker, not a replacement profile.
    body_part.update(base)
    body_part.update(length_m=.5, length_mm=500, template="limb1")
    bones = [
        {"name": "root", "parent": "", "head_m": [0, 0, 0], "tail_m": [0, .1, 0], "up_m": [0, 0, 1]},
        {"name": "child", "parent": "root", "head_m": [.28, .45, .11], "tail_m": [.28, .45, .61], "up_m": [0, 1, 0]}]
    rigkit.reset_scene()
    rigkit.build_armature(ops_skeleton.ARMATURE_NAME, bones)
    path = fixture / "skeletons" / "fixture.blend"
    rigkit.save_blend(str(path))
    branch = {**base, "branch_id": "limb", "attach_bone": "root", "bone_names": ["child"],
        "length_m": .5, "length_mm": 500,
        "accepts": {"categories": ["limb"], "templates": ["limb1"], "tags_any": []},
        "snap": {"position_m": [.28, .45, .11], "rotation_xyzw": [0, 0, .70710678, .70710678]},
        "connector_interface": {"interface_id": "skinned_parent_child", "interface_version": "1.0.0",
            "parent_role": "parent", "child_role": "child", "parent_bone": "root", "child_bone": "child",
            "position_m": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1]}}
    skeleton = {"bones": bones, "branches": [branch], "asset": {"blend": path.relative_to(out).as_posix()}}
    recipe = {"recipe_id": "asymmetric", "fills": [{"branch_id": "limb", "part_id": body_part["part_id"], "connector_part_id": part["part_id"]}]}
    qa = {"skeleton": skeleton, "parts": {body_part["part_id"]: body_part, part["part_id"]: connector},
          "library_dir": str(out), "recipe": recipe, "gait_profile": {}, "measure": [part["part_id"]]}
    arm, body = ops_partqa._tagged_assemble(qa)
    attr = body.data.attributes[ops_partqa.TAG + part["part_id"]]
    members = [i for i, value in enumerate(attr.data) if value.value > .5]
    rest = {i: body.data.vertices[i].co.copy() for i in members}
    snap = ops_assemble.snap_matrix_blender(branch["snap"], 1.0)
    error = max(min((snap @ Vector(point) - p).length for p in rest.values()) for point in source)
    sums = [sum(g.weight for g in body.data.vertices[i].groups) for i in members]
    groups = {body.vertex_groups[g.group].name for i in members for g in body.data.vertices[i].groups}
    if error > 1e-5 or groups != {"root", "child"} or max(abs(s - 1) for s in sums) > 1e-6:
        raise ValueError(f"CC_CONNECTOR_BIND: imported attachment error={error}, groups={groups}, sums={min(sums), max(sums)}")
    inverse_snap = snap.inverted()
    axial_weight_error = 0.0
    s0, s1 = part["connector_span_m"]
    for i in members:
        z = to_gltf(inverse_snap @ rest[i])[2]
        u = min(1.0, max(0.0, (z - s0) / (s1 - s0)))
        actual_child_weight = sum(g.weight for g in body.data.vertices[i].groups
                                  if body.vertex_groups[g.group].name == "child")
        axial_weight_error = max(axial_weight_error, abs(actual_child_weight - u * u * (3 - 2 * u)))
    if axial_weight_error > 1e-5:
        raise ValueError("CC_CONNECTOR_WEIGHTS: imported weights do not follow canonical axial smoothstep")
    endpoints = {name: min(members, key=lambda i: sum(g.weight for g in body.data.vertices[i].groups
        if body.vertex_groups[g.group].name != name)) for name in ("root", "child")}
    arm.pose.bones["child"].rotation_quaternion = Quaternion((1, 0, 0), math.radians(45))
    bpy.context.view_layer.update()
    evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        parent_delta = (mesh.vertices[endpoints["root"]].co - rest[endpoints["root"]]).length
        child_delta = (mesh.vertices[endpoints["child"]].co - rest[endpoints["child"]]).length
        transforms = {name: arm.pose.bones[name].matrix @ arm.data.bones[name].matrix_local.inverted()
                      for name in ("root", "child")}
        expected_error = 0.0
        for i in members:
            expected = Vector((0, 0, 0))
            for g in body.data.vertices[i].groups:
                name = body.vertex_groups[g.group].name
                expected += (transforms[name] @ rest[i]) * g.weight
            expected_error = max(expected_error, (mesh.vertices[i].co - expected).length)
    finally:
        evaluated.to_mesh_clear()
    if expected_error > 1e-5 or child_delta <= 1e-5:
        raise ValueError("CC_CONNECTOR_BIND: parent/child deformation does not match exact bind transforms")
    return {"attachment_error_m": error, "skin_transform_error_m": expected_error,
            "max_axial_weight_error": axial_weight_error,
            "parent_endpoint_delta_m": parent_delta, "child_endpoint_delta_m": child_delta,
            "bone_groups": sorted(groups), "max_weight_sum_error": max(abs(s - 1) for s in sums)}


def run(args: dict[str, Any]) -> dict[str, Any]:
    part, profile = args["part"], args["template"]
    if part["category"] != "connector" or not part.get("connector_surface"):
        raise ValueError("CC_CONNECTOR_SURFACE: native SDF connector required")
    out = Path(args["out_dir"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    variants = {}
    for method in ("loft", "sdf"):
        candidate = copy.deepcopy(part)
        if method == "loft":
            candidate.pop("connector_surface", None)
            candidate["inventory_kind"] = "reference"
        exported = ops_placeholder.run({"part": candidate, "template": profile,
            "out_fbx": str(out / f"{method}.fbx"), "out_glb": str(out / f"{method}.glb")})
        vertices, faces, weights = ops_placeholder.build_mesh(candidate, profile)
        if method == "sdf":
            rx, ry = part["dimensions_m"][0] / 2, part["dimensions_m"][1] / 2
            voxel, cells = grid_spec(rx, ry, part["connector_span_m"], part["connector_surface"])
            exported.update(validate_mesh(vertices, faces, rx, ry, part["connector_span_m"],
                                          voxel, part["max_triangles"], profile))
            exported["padded_grid_cells"] = cells
            if any(set(w) != {"b0", "b1"} or abs(sum(w.values()) - 1) > 1e-6 for w in weights):
                raise ValueError("CC_CONNECTOR_WEIGHTS: exactly the analytical parent/child groups required")
            asymmetric = _asymmetric(part, profile, out, vertices)
        variants[method] = exported
    report = {"part_id": part["part_id"], "structural": variants, "asymmetric": asymmetric,
              "deformation_complete": False, "passed": False}
    if args.get("structure_only"):
        return report
    required = ("skeleton", "parts", "library_dir", "recipe", "gait_profile")
    if any(key not in args for key in required):
        raise ValueError("CC_CONNECTORQA_INPUT: complete built assembly inputs required; structure_only is not deformation acceptance")
    args = _stage_inputs(args, out)
    metrics = {}
    for method in ("loft", "sdf"):
        qa = copy.deepcopy(args)
        candidate = qa["parts"][part["part_id"]] = copy.deepcopy(part)
        candidate["asset"]["fbx"] = f"{method}.fbx"
        qa.pop("shots", None)
        metrics[method] = _evaluate(qa, candidate, profile)
    reference, actual = metrics["loft"], metrics["sdf"]
    problems = []
    # Same deformation acceptance as parts.commands.judge; Blender does not import host Click.
    if actual["strain_p99"] > max(1.6, reference["strain_p99"] * 1.25):
        problems.append("CC_CONNECTOR_STRETCH")
    if actual["strain_p01"] < min(.6, reference["strain_p01"] * .8):
        problems.append("CC_CONNECTOR_COMPRESSION")
    if actual["flipped"] > max(.005, reference["flipped"] * 1.5 + .002):
        problems.append("CC_CONNECTOR_FLIPPED")
    if actual["max_penetration_m"] > .005:
        problems.append("CC_CONNECTOR_GROUND_PENETRATION")
    report.update(deformation_complete=True, metrics=metrics, passed=not problems, problems=problems)
    report["input_fingerprint"] = args.get("input_fingerprint", "")
    if args.get("shots"):
        report["comparison"] = _comparison(args, part, profile, out)
    (out / "qa.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if problems:
        raise ValueError("CC_CONNECTORQA_FAILED: " + ", ".join(problems))
    return report
