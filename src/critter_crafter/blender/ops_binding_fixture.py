"""Headless asymmetric connector fixture that exercises Blender's actual FBX assembly path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy
from mathutils import Quaternion, Vector

from . import ops_assemble, rigkit
from .frame import to_blender


def _material(name: str, color: str) -> bpy.types.Material:
    return rigkit.flesh_material(name, color)


def _skinned_asset(path: Path, bones: list[dict[str, Any]], vertices: list[tuple[float, float, float]],
                   faces: list[tuple[int, ...]], weights: list[dict[str, float]],
                   materials: list[tuple[str, str]], face_materials: list[int]) -> None:
    rigkit.reset_scene()
    arm = rigkit.build_armature("FixtureArm", bones)
    mesh = bpy.data.meshes.new(path.stem)
    mesh.from_pydata(vertices, [], faces); mesh.update()
    for material_name, color in materials:
        mesh.materials.append(_material(material_name, color))
    for polygon, material_index in zip(mesh.polygons, face_materials, strict=True):
        polygon.material_index = material_index
    obj = bpy.data.objects.new(path.stem, mesh)
    bpy.context.scene.collection.objects.link(obj)
    groups = {bone["name"]: obj.vertex_groups.new(name=bone["name"]) for bone in bones}
    for index, influences in enumerate(weights):
        for name, value in influences.items():
            groups[name].add([index], value, "REPLACE")
    obj.parent = arm
    obj.modifiers.new("Armature", "ARMATURE").object = arm
    rigkit.export_fbx(str(path), [arm, obj], animated=False)


def _fixture_parts(out: Path) -> tuple[dict[str, Any], dict[str, Any], list[tuple[float, float, float]]]:
    body_path = out / "parts" / "fixture_body.fbx"
    connector_path = out / "connectors" / "fixture_connector.fbx"
    body_bones = [{"name": "b0", "parent": "", "head_m": [0, 0, 0], "tail_m": [0, 0, .5], "up_m": [0, 1, 0]}]
    _skinned_asset(body_path, body_bones,
                   [to_blender(v) for v in ((-.06, 0, 0), (.06, 0, 0), (0, .02, .5))], [(0, 1, 2)],
                   [{"b0": 1.0}] * 3, [("M_FixtureBody", "#836b7d")], [0])
    connector_bones = [
        {"name": "b0", "parent": "", "head_m": [0, 0, -.12], "tail_m": [0, 0, 0], "up_m": [0, 1, 0]},
        {"name": "b1", "parent": "b0", "head_m": [0, 0, 0], "tail_m": [0, 0, .12], "up_m": [0, 1, 0]},
    ]
    # Deliberately asymmetric x/y offsets make any attachment-axis clamp or
    # rotation mistake observable after FBX re-import and snap transformation.
    source = ((-.08, -.03, -.10), (.02, .07, -.10), (.06, -.02, -.10),
              (-.04, -.06, .10), (.09, .02, .10), (.01, .08, .10),
              (.03, -.01, 0.0), (-.02, .04, 0.0))
    _skinned_asset(connector_path, connector_bones, [to_blender(v) for v in source],
                   [(0, 1, 2), (3, 4, 5), (1, 6, 7), (1, 7, 4)],
                   [{"b0": 1.0}] * 3 + [{"b1": 1.0}] * 3 + [{"b0": .5, "b1": .5}] * 2,
                   [("M_ConnectorParent", "#715060"), ("M_ConnectorChild", "#4d7683")],
                   [0, 1, 0, 1])
    base = {"side": "C", "length_m": .5, "length_mm": 500, "girth_m": .2, "girth_mm": 200,
            "binding_profile_id": "fixture_chain", "binding_profile_version": "1.0.0",
            "binding_profile_hash": "fixture-hash", "species_tags": [], "status": "reference"}
    body = base | {"part_id": "fixture_body", "category": "limb", "template": "limb1",
                   "asset": {"fbx": "parts/fixture_body.fbx"}}
    connector = base | {"part_id": "fixture_connector", "category": "connector", "template": "connector2",
                        "connector_span_m": [-.12, .12], "connector_radius_m": .1,
                        "connector_interface": {"interface_id": "skinned_parent_child", "interface_version": "1.0.0",
                                                "bone_groups": [{"group": "b0", "role": "parent"},
                                                                {"group": "b1", "role": "child"}],
                                                "max_influences": 2, "weights_normalized": True,
                                                "position_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
                        "asset": {"fbx": "connectors/fixture_connector.fbx"}}
    return body, connector, [to_blender(point) for point in source]


def run(args: dict[str, Any]) -> dict[str, Any]:
    out = Path(args["out_dir"])
    body_part, connector_part, connector_source = _fixture_parts(out)
    if args.get("incompatible_connector"):
        connector_part["binding_profile_version"] = "2.0.0"
    skeleton_path = out / "skeletons" / "fixture.blend"
    rigkit.reset_scene()
    bones = [
        {"name": "root", "parent": "", "head_m": [0, 0, 0], "tail_m": [0, .1, 0], "up_m": [0, 0, 1]},
        {"name": "child", "parent": "root", "head_m": [.28, .24, .11], "tail_m": [.28, .24, .61], "up_m": [0, 1, 0]},
    ]
    arm = rigkit.build_armature("Skeleton", bones)
    rigkit.save_blend(str(skeleton_path))
    branch = {"branch_id": "limb", "attach_bone": "root", "bone_names": ["child"],
              "length_m": .5, "length_mm": 500, "girth_m": .2, "girth_mm": 200, "side": "C", "binding_profile_id": "fixture_chain",
              "binding_profile_version": "1.0.0", "binding_profile_hash": "fixture-hash",
              "accepts": {"categories": ["limb"], "templates": ["limb1"], "tags_any": []},
              "connector_interface": {"interface_id": "skinned_parent_child", "interface_version": "1.0.0",
                                      "parent_role": "parent", "child_role": "child", "parent_bone": "root",
                                      "child_bone": "child", "position_m": [0.0, 0.0, 0.0],
                                      "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
              "snap": {"position_m": [.28, .24, .11], "rotation_xyzw": [0, 0, .70710678, .70710678]}}
    skeleton = {"bones": bones, "branches": [branch], "asset": {"blend": "skeletons/fixture.blend"}}
    recipe = {"recipe_id": "fixture_recipe", "fills": [{"branch_id": "limb", "part_id": "fixture_body",
               "connector_part_id": "fixture_connector"}]}
    arm, assembled = ops_assemble.assemble(skeleton, {"fixture_body": body_part, "fixture_connector": connector_part},
                                            str(out), recipe, {})
    names = [material.name for material in assembled.data.materials]
    root_group = assembled.vertex_groups["root"].index
    child_group = assembled.vertex_groups["child"].index
    def weight(index: int, group: int) -> float:
        return next((item.weight for item in assembled.data.vertices[index].groups if item.group == group), 0.0)
    parent_index = next(index for index, vertex in enumerate(assembled.data.vertices)
                        if weight(index, root_group) > .999 and weight(index, child_group) < 1e-6)
    child_index = next(index for index, vertex in enumerate(assembled.data.vertices)
                       if weight(index, child_group) > .999 and weight(index, root_group) < 1e-6
                       and any(poly.material_index == names.index("M_ConnectorChild") and index in poly.vertices
                               for poly in assembled.data.polygons))
    expected = [ops_assemble.snap_matrix_blender(branch["snap"], 1.0) @ Vector(point) for point in connector_source]
    rest = [vertex.co.copy() for vertex in assembled.data.vertices]
    orientation_error = max(min((point - vertex).length for vertex in rest) for point in expected)
    arm.pose.bones["child"].rotation_mode = "QUATERNION"
    arm.pose.bones["child"].rotation_quaternion = Quaternion((.9238795, .3826834, 0, 0))
    bpy.context.view_layer.update()
    evaluated = assembled.evaluated_get(bpy.context.evaluated_depsgraph_get())
    evaluated_mesh = evaluated.to_mesh()
    parent_delta = (evaluated_mesh.vertices[parent_index].co - rest[parent_index]).length
    child_delta = (evaluated_mesh.vertices[child_index].co - rest[child_index]).length
    evaluated.to_mesh_clear()
    sums = [sum(group.weight for group in vertex.groups) for vertex in assembled.data.vertices]
    return {"connector_fbx": str(out / "connectors" / "fixture_connector.fbx"),
            "body_fbx": str(out / "parts" / "fixture_body.fbx"), "material_names": names,
            "material_count": len(names), "max_influences": max(len(vertex.groups) for vertex in assembled.data.vertices),
            "min_weight_sum": min(sums), "max_weight_sum": max(sums), "orientation_error_m": orientation_error,
            "parent_delta_m": parent_delta, "child_delta_m": child_delta}
