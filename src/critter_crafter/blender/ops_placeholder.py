"""Procedural placeholder parts and connectors: a lofted, lumpy flesh tube skinned to the part's template chain.

Part space (docs/frame.md): origin = attachment point, +Z along the part, +Y = up reference,
dimensions_m = [width_x, thickness_y, length_z]. Connectors span z in [span0, span1] around the
snap point and are weighted from bone b0 (parent side) to b1 (child side).
"""

from __future__ import annotations

import math
import zlib
from typing import Any, Callable

import bpy

from . import rigkit
from .frame import to_blender

CAP = 0.08


def _cap(t: float, root_cap: bool = True, tip_cap: bool = True) -> float:
    if root_cap and t < CAP:
        return math.sqrt(max(0.0, 1 - ((CAP - t) / CAP) ** 2))
    if tip_cap and t > 1 - CAP:
        return math.sqrt(max(0.0, 1 - ((t - (1 - CAP)) / CAP) ** 2))
    return 1.0


PROFILES: dict[str, Callable[[float], float]] = {
    "capsule": lambda t: (1.0 - 0.35 * t + 0.06 * math.sin(t * math.pi * 6)) * _cap(t),
    "torso": lambda t: (0.8 + 0.2 * math.sin(math.pi * t)) * _cap(t),
    "sphere": lambda t: math.sqrt(max(0.0, 1 - (2 * t - 1) ** 2)),
    "cone": lambda t: (max(0.0, 1 - t) ** 0.9 + 0.02) * _cap(t, tip_cap=False),
    "cylinder": lambda t: 0.75 + 0.35 * math.sin(math.pi * t),
    "box": lambda t: _cap(t),
}


def _profile_for(part: dict[str, Any]) -> str:
    if part["category"] == "core" and part["template"] == "spine3":
        return "torso"
    return part["fallback_primitive"]


def _chain_bones(part: dict[str, Any], template: dict[str, Any]) -> list[tuple[str, float, float]]:
    """(name, z_head, z_tail) in part space."""
    if part["category"] == "connector":
        s0, s1 = part["connector_span_m"]
        return [("b0", s0, 0.0), ("b1", 0.0, s1)]
    out, z = [], 0.0
    for i, frac in enumerate(template["bone_fractions"]):
        z1 = z + frac * part["length_m"]
        out.append((f"b{i}", z, z1))
        z = z1
    return out


def _weights(z: float, bones: list[tuple[str, float, float]], connector: bool, span: list[float]) -> dict[str, float]:
    if connector:
        s0, s1 = span
        u = min(1.0, max(0.0, (z - s0) / (s1 - s0)))
        w1 = u * u * (3 - 2 * u)
        return {"b0": 1 - w1, "b1": w1}
    centers = [(n, 0.5 * (a + b)) for n, a, b in bones]
    if z <= centers[0][1]:
        return {centers[0][0]: 1.0}
    if z >= centers[-1][1]:
        return {centers[-1][0]: 1.0}
    for (n0, c0), (n1, c1) in zip(centers, centers[1:]):
        if c0 <= z <= c1:
            u = (z - c0) / (c1 - c0)
            return {n0: 1 - u, n1: u}
    return {centers[-1][0]: 1.0}


def build_mesh(part: dict[str, Any], template: dict[str, Any]) -> tuple[list, list, list[dict[str, float]]]:
    connector = part["category"] == "connector"
    bones = _chain_bones(part, template)
    if connector:
        z0, z1 = part["connector_span_m"]
        rx = ry = part["connector_radius_m"]
    else:
        z0, z1 = 0.0, part["length_m"]
        rx, ry = part["dimensions_m"][0] / 2, part["dimensions_m"][1] / 2
    profile = PROFILES[_profile_for(part)]
    seg = 16 if max(rx, ry) > 0.2 else 12
    rings = max(16, 6 * len(bones)) if not connector else 10
    seed = zlib.crc32(part["part_id"].encode()) % 1000 / 1000.0 * 2 * math.pi
    verts, faces, weights = [], [], []
    for i in range(rings + 1):
        t = i / rings
        z = z0 + t * (z1 - z0)
        r = max(0.06, profile(t))  # no collapsed rings: engines drop zero-area triangles
        for j in range(seg):
            a = 2 * math.pi * j / seg
            lump = 1 + 0.07 * math.sin(3 * a + 11 * t + seed) * math.sin(5 * math.pi * t + a + seed)
            p = (rx * r * lump * math.cos(a), ry * r * lump * math.sin(a), z)
            verts.append(to_blender(p))
            weights.append(_weights(z, bones, connector, part.get("connector_span_m", [])))
    for i in range(rings):
        for j in range(seg):
            a = i * seg + j
            b = i * seg + (j + 1) % seg
            c = (i + 1) * seg + (j + 1) % seg
            d = (i + 1) * seg + j
            faces.append((a, b, c, d))  # outward normals (verified: cross(tangent, axis) points out)
    # close both ends with triangle fans around a centre vertex
    for ring, z, bottom in ((0, z0, True), (rings, z1, False)):
        centre = len(verts)
        verts.append(to_blender((0.0, 0.0, z)))
        weights.append(_weights(z, bones, connector, part.get("connector_span_m", [])))
        for j in range(seg):
            a = ring * seg + j
            b = ring * seg + (j + 1) % seg
            faces.append((centre, b, a) if bottom else (centre, a, b))
    return verts, faces, weights


def run(args: dict[str, Any]) -> dict[str, Any]:
    part = args["part"]
    template = args["template"]
    rigkit.reset_scene()
    bones = [
        {"name": n, "parent": f"b{i - 1}" if i else "", "head_m": [0.0, 0.0, a], "tail_m": [0.0, 0.0, b], "up_m": [0.0, 1.0, 0.0]}
        for i, (n, a, b) in enumerate(_chain_bones(part, template))
    ]
    arm = rigkit.build_armature("Armature", bones)
    verts, faces, weights = build_mesh(part, template)
    mesh = bpy.data.meshes.new(part["part_id"])
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    obj = bpy.data.objects.new(part["part_id"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(rigkit.flesh_material(f"M_{part['part_id']}", part["albedo"]))
    groups = {b["name"]: obj.vertex_groups.new(name=b["name"]) for b in bones}
    for vi, w in enumerate(weights):
        for name, value in w.items():
            if value > 1e-4:
                groups[name].add([vi], value, "REPLACE")
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    tris = rigkit.triangle_count(obj)
    rigkit.export_fbx(args["out_fbx"], [arm, obj], animated=False)
    if args.get("out_glb"):
        rigkit.export_glb(args["out_glb"], [arm, obj], animated=False)
    if args.get("out_blend"):
        rigkit.save_blend(args["out_blend"])
    return {
        "part_id": part["part_id"],
        "triangles": tris,
        "vertices": len(verts),
        "bones": [b["name"] for b in bones],
    }
