"""Real part: import a sourced mesh (Meshy GLB), clean it, fit it onto its binding-profile chain
(``parts/fit.py``), weight it, and export the same FBX + GLB transports as a placeholder part.

Steps: import -> join -> merge UV-seam duplicates -> drop debris islands -> decimate to budget ->
fit/straighten in part space -> sharp edges by angle -> base-colour texture resized to budget ->
straight ``b0..bN`` armature -> FBX + GLB (+ master .blend, + albedo PNG).

The source mesh is read from the asset archive and never copied into the repository; everything
this op writes is build output (library/, work/).
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any

import bmesh
import bpy

from . import rigkit
from .frame import to_blender
from ..parts.fit import chain_weights, fit

MERGE_DISTANCE_N = 1e-5     # of the bounding-box diagonal: rejoins glTF UV-seam splits only
DEBRIS_TRIANGLES = 4        # islands at or below this many triangles are removed
DECIMATE_HEADROOM = 0.97    # decimate to this fraction of the triangle budget


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _import_source(path: str) -> bpy.types.Object:
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != b"glTF":
        raise ValueError(f"CC_REALPART_FORMAT: {path} is not a binary glTF (magic {magic!r})")
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"CC_REALPART_EMPTY: {path} has no mesh")
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    rigkit.select_only(meshes)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.parent = None
    obj.data.transform(obj.matrix_world)
    obj.matrix_world.identity()
    return obj


def _clean(obj: bpy.types.Object, max_triangles: int) -> dict[str, Any]:
    mesh = obj.data
    report: dict[str, Any] = {"source_triangles": rigkit.triangle_count(obj), "source_vertices": len(mesh.vertices)}
    bm = bmesh.new()
    bm.from_mesh(mesh)
    lo = [min(v.co[k] for v in bm.verts) for k in range(3)]
    hi = [max(v.co[k] for v in bm.verts) for k in range(3)]
    diag = math.dist(lo, hi) or 1.0
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=MERGE_DISTANCE_N * diag)
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    # Islands: remove tiny debris, keep every real piece (spikes, claws, teeth). Faces are tracked by
    # identity: element indices are stale after remove_doubles.
    seen: set[bmesh.types.BMFace] = set()
    debris = []
    islands = 0
    for face in bm.faces:
        if face in seen:
            continue
        islands += 1
        stack, members = [face], []
        while stack:
            f = stack.pop()
            if f in seen:
                continue
            seen.add(f)
            members.append(f)
            for e in f.edges:
                stack.extend(g for g in e.link_faces if g not in seen)
        if sum(len(f.verts) - 2 for f in members) <= DEBRIS_TRIANGLES:
            debris.extend(members)
    if debris:
        bmesh.ops.delete(bm, geom=debris, context="FACES")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    report.update(islands=islands, debris_faces_removed=len(debris))
    tris = rigkit.triangle_count(obj)
    if tris > max_triangles * DECIMATE_HEADROOM:
        ratio = max_triangles * DECIMATE_HEADROOM / tris
        mod = obj.modifiers.new("Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        rigkit.select_only([obj])
        bpy.ops.object.modifier_apply(modifier=mod.name)
        report["decimate_ratio"] = round(ratio, 6)
    report.update(triangles=rigkit.triangle_count(obj), vertices=len(obj.data.vertices))
    return report


def _joint_loops(obj: bpy.types.Object, planes: list[float], max_triangles: int) -> dict[str, Any]:
    """Cut edge loops across the straightened part at the given chain coordinates (part +Z is
    Blender +Y). Band-edge loops are dropped first if the cuts would exceed the triangle budget."""
    def cut(zs: list[float]) -> int:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        for z in zs:
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=(0.0, z, 0.0), plane_no=(0.0, 1.0, 0.0))
        tris = sum(len(f.verts) - 2 for f in bm.faces)
        if tris <= max_triangles:
            bm.to_mesh(obj.data)
            obj.data.update()
        bm.free()
        return tris
    before = rigkit.triangle_count(obj)
    tris = cut(planes)
    if tris > max_triangles:
        joints = planes[1::3] if len(planes) % 3 == 0 else planes
        tris = cut(joints)
        planes = joints if tris <= max_triangles else []
    return {"planes": len(planes), "triangles_added": rigkit.triangle_count(obj) - before}


def _prune(weights: dict[str, float], floor: float = 1e-4) -> dict[str, float]:
    """Drop negligible influences and renormalize, so every vertex sums to exactly one."""
    kept = {name: value for name, value in weights.items() if value > floor}
    total = sum(kept.values())
    return {name: value / total for name, value in kept.items()}


def _source_texture(obj: bpy.types.Object) -> bpy.types.Image | None:
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        for link in bsdf.inputs["Base Color"].links:
            node = link.from_node
            if node.type == "TEX_IMAGE" and node.image is not None:
                return node.image
    return None


def _material(part: dict[str, Any], image: bpy.types.Image | None) -> bpy.types.Material:
    mat = rigkit.flesh_material(f"M_{part['part_id']}", part.get("albedo", "#9b6874"))
    if image is not None:
        nodes = mat.node_tree.nodes
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def _write_texture(image: bpy.types.Image, size: int, path: str) -> dict[str, Any]:
    if image.size[0] == 0:
        image.reload()
    copy = image.copy()
    copy.name = os.path.basename(path)
    if max(copy.size) > size:
        copy.scale(size, size)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    copy.filepath_raw = path
    copy.file_format = "PNG"
    copy.save()
    return {"image": copy, "size": list(copy.size)}


def run(args: dict[str, Any]) -> dict[str, Any]:
    part = args["part"]
    profile = args["template"]
    source = args["source_path"]
    expected = part.get("real", {}).get("sha256")
    actual = _sha256(source)
    if expected and actual != expected:
        raise ValueError(f"CC_REALPART_SOURCE_CHANGED: {part['part_id']}: {source} sha256 {actual} != {expected}")
    rigkit.reset_scene()
    obj = _import_source(source)
    clean = _clean(obj, int(part["max_triangles"]))
    image = _source_texture(obj)
    mesh = obj.data
    # Blender import frame -> the source's own glTF frame (Y-up importer convention).
    source_gltf = [(v.co.x, v.co.z, -v.co.y) for v in mesh.vertices]
    faces = [tuple(p.vertices) for p in mesh.polygons]
    fitted = fit(source_gltf, faces, part["real"]["fit"], part["template"], profile["bone_fractions"],
                 float(part["length_m"]), float(part["girth_m"]))
    if fitted["params"]["mirror_x"]:
        mesh.flip_normals()
    for v, p in zip(mesh.vertices, fitted["positions"]):
        v.co = to_blender(p)
    mesh.update()
    loops = _joint_loops(obj, fitted["planes"], int(part["max_triangles"]))
    weights = [_prune(chain_weights(v.co.y, fitted["bones"], fitted["params"]["weights"], fitted["bands"]))
               for v in mesh.vertices]
    if mesh.has_custom_normals:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.set_sharp_from_angle(angle=math.radians(float(part["real"]["fit"].get("sharp_angle_deg", 40.0))))
    obj.data.materials.clear()
    texture: dict[str, Any] = {}
    if image is not None and args.get("out_albedo_png"):
        texture = _write_texture(image, int(part["real"].get("texture_size", 1024)), args["out_albedo_png"])
        image = texture.pop("image")
    obj.data.materials.append(_material(part, image))
    obj.name = part["part_id"]
    mesh.name = part["part_id"]

    length = float(part["length_m"])
    bones, z = [], 0.0
    for i, frac in enumerate(profile["bone_fractions"]):
        z1 = z + float(frac) * length
        bones.append({"name": f"b{i}", "parent": f"b{i - 1}" if i else "", "head_m": [0.0, 0.0, z],
                      "tail_m": [0.0, 0.0, z1], "up_m": [0.0, 1.0, 0.0]})
        z = z1
    arm = rigkit.build_armature("Armature", bones)
    groups = {b["name"]: obj.vertex_groups.new(name=b["name"]) for b in bones}
    for vi, w in enumerate(weights):
        for name, value in w.items():
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
    previews = _render_previews(obj, args["preview_prefix"], int(args.get("preview_size", 320))) \
        if args.get("preview_prefix") else []
    positions = fitted["positions"]
    return {
        "previews": previews,
        "part_id": part["part_id"],
        "source_sha256": actual,
        "triangles": tris,
        "vertices": len(mesh.vertices),
        "bones": [b["name"] for b in bones],
        "max_influences": max(len(w) for w in weights),
        "min_weight_sum": min(sum(w.values()) for w in weights),
        "max_weight_sum": max(sum(w.values()) for w in weights),
        "joint_loops": loops,
        "part_space_bounds_m": [[min(p[k] for p in positions) for k in range(3)],
                                [max(p[k] for p in positions) for k in range(3)]],
        "clean": clean,
        "fit": fitted["metrics"],
        "texture": texture,
        "centerline": fitted["centerline"],
    }


PREVIEW_VIEWS = {  # part space (glTF): +Z along the part, +Y dorsal
    "side": (1.0, 0.0, 0.0),
    "top": (0.0, 1.0, 0.0),
    "end": (0.0, 0.0, 1.0),
    "three_quarter": (0.8, 0.6, 0.5),
}


def _render_previews(obj: bpy.types.Object, prefix: str, size: int) -> list[str]:
    """Workbench stills of the fitted, straight part in part space (fast and GPU-light)."""
    from mathutils import Vector

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x = scene.render.resolution_y = size
    world = bpy.data.worlds.new("PreviewWorld")
    world.color = (0.06, 0.065, 0.08)
    scene.world = world
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[k] for c in corners) for k in range(3)])
    hi = Vector([max(c[k] for c in corners) for k in range(3)])
    centre, radius = (lo + hi) / 2, (hi - lo).length / 2
    cam = bpy.data.objects.new("PreviewCamera", bpy.data.cameras.new("PreviewCamera"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.1 * radius
    scene.camera = cam
    out = []
    for name, view in PREVIEW_VIEWS.items():
        direction = Vector(to_blender(view)).normalized()
        cam.location = centre + direction * radius * 4
        up = Vector(to_blender((0.0, 1.0, 0.0) if name != "top" else (0.0, 0.0, 1.0)))
        cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        # Keep the part's dorsal side up in every view except the top view.
        cam_up = cam.matrix_world.to_3x3() @ Vector((0.0, 1.0, 0.0))
        if cam_up.dot(up) < 0:
            cam.rotation_euler.rotate_axis("Z", math.pi)
        path = f"{prefix}_{name}.png"
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        out.append(path)
    return out
