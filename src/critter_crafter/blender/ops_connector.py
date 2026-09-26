"""Two-bone watertight connectors: SDF volume-grid union skinned to b0/b1.

Parent and child end-cap ellipsoids plus a connecting tube are unioned with
Blender 5 SDF Grid Boolean. Voxel size is derived from a target triangle count.
Deform topology is Grid-to-Mesh after in-tree bmesh repair; voxel remesh is not
the shipped surface. Axial two-bone weights match the placeholder loft.
"""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy

from . import ops_placeholder, rigkit
from .frame import to_blender, to_gltf

# Task 1: limb3 plantigrade leg_L (girth 0.2233 m). SDF 748 tris, 0 holes,
# 0 non-manifold, span-accurate; loft 264 tris faceted collar. SDF is the baker.
TARGET_SDF_TRIS = 600


def _radii(part: dict[str, Any]) -> tuple[float, float]:
    reference = part.get("inventory_kind") == "reference" or part.get("source") == "reference"
    if reference:
        return float(part["dimensions_m"][0]) * 0.5, float(part["dimensions_m"][1]) * 0.5
    r = float(part["connector_radius_m"])
    return r, r


def _voxel_size(rx: float, ry: float, length: float, target_tris: int) -> float:
    r_eq = math.sqrt(max(1e-12, rx * ry))
    surface = 2.0 * math.pi * r_eq * length + 2.0 * math.pi * r_eq * r_eq
    voxel = math.sqrt(max(1e-12, 2.0 * surface / max(1, target_tris)))
    extent = max(2.0 * rx, 2.0 * ry, length, 1e-4)
    # Keep enough voxels to reconstruct the ellipse; do not use girth/24.
    return min(max(voxel, extent / 40.0), extent / 8.0)


def _obj_from_bmesh(name: str, bm: bmesh.types.BMesh) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _ellipsoid(name: str, z: float, rx: float, ry: float, rz: float, segments: int = 16) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=max(8, segments // 2), radius=1.0)
    for vert in bm.verts:
        sx, sy, sz = vert.co
        vert.co = to_blender((sx * rx, sz * ry, sy * rz + z))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _obj_from_bmesh(name, bm)


def _elliptical_cylinder(name: str, z0: float, z1: float, rx: float, ry: float, segments: int = 16) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=True, segments=segments, radius1=1.0, radius2=1.0, depth=1.0
    )
    length = z1 - z0
    mid = 0.5 * (z0 + z1)
    for vert in bm.verts:
        x, y, z = vert.co
        vert.co = to_blender((x * rx, y * ry, z * length + mid))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _obj_from_bmesh(name, bm)


def _link_sdf(nodes, links, obj: bpy.types.Object, voxel: float) -> Any:
    info = nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = obj
    info.transform_space = "ORIGINAL"
    info.inputs["As Instance"].default_value = False
    sdf = nodes.new("GeometryNodeMeshToSDFGrid")
    sdf.inputs["Voxel Size"].default_value = voxel
    sdf.inputs["Band Width"].default_value = 4
    links.new(info.outputs["Geometry"], sdf.inputs["Mesh"])
    return sdf


def _apply_sdf_union(volumes: list[bpy.types.Object], voxel: float) -> bpy.types.Object:
    group = bpy.data.node_groups.new("CCConnectorSDF", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    n_out = nodes.new("NodeGroupOutput")
    boolean = nodes.new("GeometryNodeSDFGridBoolean")
    boolean.operation = "UNION"
    union_in = next(socket for socket in boolean.inputs if socket.is_multi_input)
    for volume in volumes:
        sdf = _link_sdf(nodes, links, volume, voxel)
        links.new(sdf.outputs["SDF Grid"], union_in)
    to_mesh = nodes.new("GeometryNodeGridToMesh")
    to_mesh.inputs["Threshold"].default_value = 0.0
    to_mesh.inputs["Adaptivity"].default_value = 0.0
    links.new(boolean.outputs["Grid"], to_mesh.inputs["Grid"])
    links.new(to_mesh.outputs["Mesh"], n_out.inputs[0])

    dummy = None
    try:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=0.01)
        dummy = _obj_from_bmesh("cc_connector_sdf", bm)
        mod = dummy.modifiers.new("SDF", "NODES")
        mod.node_group = group
        rigkit.select_only([dummy])
        applied = bpy.ops.object.modifier_apply(modifier=mod.name)
        if "FINISHED" not in applied:
            raise RuntimeError(f"CC_CONNECTOR_NONMANIFOLD: SDF modifier apply {applied}")
        tris = rigkit.triangle_count(dummy)
        if not dummy.data.polygons or (len(dummy.data.vertices) <= 8 and tris <= 12):
            raise ValueError("CC_CONNECTOR_NONMANIFOLD: SDF union produced no surface")
        return dummy
    finally:
        if dummy is not None:
            leftover = dummy.modifiers.get("SDF")
            if leftover is not None:
                dummy.modifiers.remove(leftover)
        if group.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(bpy.data.node_groups[group.name])


def _repair(obj: bpy.types.Object, part_id: str) -> dict[str, int]:
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-5)
    boundary = [edge for edge in bm.edges if edge.is_boundary]
    if boundary:
        bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
    loose = [vert for vert in bm.verts if not vert.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    nonmanifold = sum(1 for edge in bm.edges if not edge.is_manifold)
    holes = sum(1 for edge in bm.edges if edge.is_boundary)
    stats = {"nonmanifold_edges": nonmanifold, "boundary_edges": holes, "faces": len(bm.faces)}
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    if nonmanifold or holes or not mesh.polygons:
        raise ValueError(f"CC_CONNECTOR_NONMANIFOLD: {part_id}")
    return stats


def _decimate(obj: bpy.types.Object, max_triangles: int) -> None:
    tris = rigkit.triangle_count(obj)
    if tris <= max_triangles:
        return
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = max_triangles / tris
    mod.use_collapse_triangulate = True
    rigkit.select_only([obj])
    applied = bpy.ops.object.modifier_apply(modifier=mod.name)
    if "FINISHED" not in applied:
        raise RuntimeError(f"CC_CONNECTOR_NONMANIFOLD: decimate apply {applied}")


def _axial_weights(obj: bpy.types.Object, span: list[float]) -> list[dict[str, float]]:
    bones = [("b0", span[0], 0.0), ("b1", 0.0, span[1])]
    return [ops_placeholder._weights(to_gltf(vert.co)[2], bones, True, span) for vert in obj.data.vertices]


def _group_weight_stats(obj: bpy.types.Object) -> tuple[int, int, float, float]:
    unweighted = 0
    max_inf = 0
    sums: list[float] = []
    for vert in obj.data.vertices:
        influences = [group.weight for group in vert.groups if group.weight > 1e-4]
        if not influences:
            unweighted += 1
            sums.append(0.0)
            continue
        max_inf = max(max_inf, len(influences))
        sums.append(sum(influences))
    if not sums:
        return 0, 0, 0.0, 0.0
    return unweighted, max_inf, min(sums), max(sums)


def _sdf_mesh(part: dict[str, Any]) -> tuple[bpy.types.Object, list[dict[str, float]], dict[str, Any]]:
    span = list(part["connector_span_m"])
    z0, z1 = float(span[0]), float(span[1])
    rx, ry = _radii(part)
    length = z1 - z0
    voxel = _voxel_size(rx, ry, length, TARGET_SDF_TRIS)
    # Each cap must stay inside [span0, span1]: center ± rz cannot cross the far end.
    rz = min(rx, ry, 0.5 * length)
    parent = _ellipsoid("cc_parent_cap", z0 + rz, rx, ry, rz)
    child = _ellipsoid("cc_child_cap", z1 - rz, rx, ry, rz)
    tube = _elliptical_cylinder("cc_join_tube", z0, z1, rx, ry)
    volumes = [parent, child, tube]
    try:
        obj = _apply_sdf_union(volumes, voxel)
    finally:
        for cap in volumes:
            mesh = cap.data
            bpy.data.objects.remove(cap, do_unlink=True)
            bpy.data.meshes.remove(mesh)
    stats = _repair(obj, part["part_id"])
    _decimate(obj, int(part["max_triangles"]))
    stats.update(_repair(obj, part["part_id"]))
    weights = _axial_weights(obj, span)
    stats.update(baker="sdf", voxel_size=round(voxel, 6))
    return obj, weights, stats


def _skin_export(part: dict[str, Any], template: dict[str, Any], obj: bpy.types.Object,
                 weights: list[dict[str, float]], args: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for poly in obj.data.polygons:
        poly.use_smooth = True
    obj.name = part["part_id"]
    obj.data.name = part["part_id"]
    bones = [
        {"name": name, "parent": f"b{i - 1}" if i else "", "head_m": [0.0, 0.0, a], "tail_m": [0.0, 0.0, b],
         "up_m": [0.0, 1.0, 0.0]}
        for i, (name, a, b) in enumerate(ops_placeholder._chain_bones(part, template))
    ]
    arm = rigkit.build_armature("Armature", bones)
    obj.data.materials.append(rigkit.flesh_material(f"M_{part['part_id']}", part.get("albedo", "#9b6874")))
    groups = {bone["name"]: obj.vertex_groups.new(name=bone["name"]) for bone in bones}
    for index, influence in enumerate(weights):
        kept = {name: value for name, value in influence.items() if value > 1e-4}
        total = sum(kept.values())
        if not kept or total <= 0:
            continue
        for name, value in kept.items():
            groups[name].add([index], value / total, "REPLACE")
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    unweighted, max_inf, min_sum, max_sum = _group_weight_stats(obj)
    if unweighted or max_inf > 2 or abs(min_sum - 1.0) > 1e-4 or abs(max_sum - 1.0) > 1e-4:
        raise ValueError(f"CC_PART_WEIGHTS: {part['part_id']}")
    swing = ops_placeholder.swing_strain_p99(arm, obj)
    tris = rigkit.triangle_count(obj)
    part_space = [to_gltf(vert.co) for vert in obj.data.vertices]
    rigkit.export_fbx(args["out_fbx"], [arm, obj], animated=False)
    if args.get("out_glb"):
        rigkit.export_glb(args["out_glb"], [arm, obj], animated=False)
    if args.get("out_blend"):
        rigkit.save_blend(args["out_blend"])
    result = {
        "part_id": part["part_id"],
        "triangles": tris,
        "vertices": len(obj.data.vertices),
        "bones": [bone["name"] for bone in bones],
        "unweighted": unweighted,
        "max_influences": max_inf,
        "min_weight_sum": min_sum,
        "max_weight_sum": max_sum,
        "swing_strain_p99": round(swing, 5),
        "part_space_bounds_m": [
            [min(point[axis] for point in part_space) for axis in range(3)],
            [max(point[axis] for point in part_space) for axis in range(3)],
        ],
    }
    result.update(extra)
    return result


def run(args: dict[str, Any]) -> dict[str, Any]:
    part = args["part"]
    template = args["template"]
    if part["category"] != "connector":
        raise ValueError(f"CC_CONNECTOR_CATEGORY: {part['part_id']}")
    rigkit.reset_scene()
    obj, weights, extra = _sdf_mesh(part)
    return _skin_export(part, template, obj, weights, args, extra)
