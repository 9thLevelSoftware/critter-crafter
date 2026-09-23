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
from .frame import to_blender, to_gltf

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
    if part.get("inventory_kind") == "reference" or part.get("source") == "reference":
        if part.get("category") == "core":
            return "torso"
        if part.get("category") == "head":
            return "sphere"
        return "capsule"
    if part["category"] == "core" and part["template"] == "spine3":
        return "torso"
    return part["fallback_primitive"]


def _chain_bones(part: dict[str, Any], template: dict[str, Any]) -> list[tuple[str, float, float]]:
    """(name, z_head, z_tail) in part space."""
    if part["category"] == "connector":
        s0, s1 = part["connector_span_m"]
        return [("b0", s0, 0.0), ("b1", 0.0, s1)]
    out, z = [], 0.0
    # V3 passes the exact binding profile; the legacy renderer passed a branch
    # template.  Both contracts expose authoritative bone_fractions.
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


def _reference_wrist_radius(part: dict[str, Any], template: dict[str, Any], t: float) -> float | None:
    """Return the mannequin-matched terminal wrist envelope for reference arms."""
    if (part.get("source") != "reference" and part.get("inventory_kind") != "reference") or (
            part.get("category") != "limb" or "_arm_" not in part["part_id"]):
        return None
    terminal_fraction = float(template["bone_fractions"][-1])
    terminal_start = 1.0 - terminal_fraction
    if t < terminal_start:
        return None
    # This is the same conservative radius convention used for the mannequin's
    # terminal segment: profile-length cap, authored girth cap, and a small
    # anatomical floor.  The final four rings match its wrist-to-hand taper.
    base_radius = max(.018, min(part["length_m"] * terminal_fraction * .32,
                                float(part["girth_m"]) * .5))
    u = min(1.0, max(0.0, (t - terminal_start) / terminal_fraction))
    if u <= .13:
        return base_radius * (.72 + .28 * u / .13)
    if u <= .84:
        return base_radius * (1.0 - .48 * (u - .13) / .71)
    # Keep a physical 1 mm cap ring before the existing centre fan.  A zero
    # ring produces duplicate vertices and degenerate quads in FBX/glTF.
    return max(.001, base_radius * .52 * (1.0 - (u - .84) / .16))


def build_mesh(part: dict[str, Any], template: dict[str, Any]) -> tuple[list, list, list[dict[str, float]]]:
    connector = part["category"] == "connector"
    reference = part.get("inventory_kind") == "reference" or part.get("source") == "reference"
    bones = _chain_bones(part, template)
    if connector:
        z0, z1 = part["connector_span_m"]
        if reference:
            # Reference records carry an authored physical ellipse.  The
            # nominal girth/radius remains the binding interface; thickness
            # is the ventral-safe profile envelope, formed before skinning.
            rx, ry = part["dimensions_m"][0] * .5, part["dimensions_m"][1] * .5
        else:
            rx = ry = part["connector_radius_m"]
    else:
        z0, z1 = 0.0, part["length_m"]
        if reference:
            rx, ry = part["dimensions_m"][0] * .5, part["dimensions_m"][1] * .5
        elif part.get("girth_m"):
            rx, ry = float(part["girth_m"]) * .5, float(part["girth_m"]) * .46
        else:
            rx, ry = part["dimensions_m"][0] / 2, part["dimensions_m"][1] / 2
    profile = PROFILES[_profile_for(part)]
    seg = 16 if max(rx, ry) > 0.2 else 12
    rings = (max(16, 6 * len(bones)) if not connector else min(
        10, max(3, part["max_triangles"] // (2 * seg) - 1)
    ))
    seed = zlib.crc32(part["part_id"].encode()) % 1000 / 1000.0 * 2 * math.pi
    # Reference inventory is a stable anatomy-review volume.  Keep its
    # profile deterministic rather than adding per-ID surface variation.
    lump_amount = 0.0 if reference else 0.07
    verts, faces, weights = [], [], []
    for i in range(rings + 1):
        t = i / rings
        z = z0 + t * (z1 - z0)
        wrist_radius = _reference_wrist_radius(part, template, t)
        # Reference arm tips intentionally reach an authored zero-radius hand
        # pole. Other placeholders retain a small cap to keep engine imports
        # from dropping zero-area endpoint rings.
        r = wrist_radius / (float(part["girth_m"]) * .5) if wrist_radius is not None else max(0.06, profile(t))
        for j in range(seg):
            a = 2 * math.pi * j / seg
            lump = 1 + lump_amount * math.sin(3 * a + 11 * t + seed) * math.sin(5 * math.pi * t + a + seed)
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
    obj.data.materials.append(rigkit.flesh_material(f"M_{part['part_id']}", part.get("albedo", "#9b6874")))
    groups = {b["name"]: obj.vertex_groups.new(name=b["name"]) for b in bones}
    for vi, w in enumerate(weights):
        for name, value in w.items():
            if value > 1e-4:
                groups[name].add([vi], value, "REPLACE")
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    tris = rigkit.triangle_count(obj)
    part_space = [to_gltf(vertex) for vertex in verts]
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
        "max_influences": max(len(weight) for weight in weights),
        "min_weight_sum": min(sum(weight.values()) for weight in weights),
        "max_weight_sum": max(sum(weight.values()) for weight in weights),
        "part_space_bounds_m": [
            [min(point[axis] for point in part_space) for axis in range(3)],
            [max(point[axis] for point in part_space) for axis in range(3)],
        ],
    }
