"""Assemble once in the authoritative bind basis and bake a light-independent atlas."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import closest_point_on_tri

from . import ops_assemble, ops_skeleton, rigkit
from .frame import to_blender

SOURCE_UV = "CC_Source"
ATLAS_UV = "CC_Atlas"
TRANSITION = "CC_Transition"


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _implicit_surface_normal(socket):
    if not socket.is_linked:
        return True
    link = socket.links[0]
    node = link.from_node
    if node.type != "NORMAL_MAP" or link.from_socket.name != "Normal" or node.space != "TANGENT":
        return False
    color, strength = node.inputs["Color"], node.inputs["Strength"]
    return (not color.is_linked and not strength.is_linked
            and np.isfinite(strength.default_value)
            and all(abs(a-b) <= 1e-6 for a, b in zip(color.default_value[:3], (.5, .5, 1))))


class ColorSource:
    """The deliberately bounded source-color graph supported by an opaque atlas."""
    def __init__(self, material, root: Path, textures: set[str], pixels: dict):
        self.material = material
        self.root = root
        self.textures = textures
        self.pixels = pixels
        if not material or not material.use_nodes:
            raise ValueError("CC_BAKE_MATERIAL: source must have an explicit node material")
        outputs = [n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
        if len(outputs) != 1 or not outputs[0].inputs["Surface"].is_linked:
            raise ValueError("CC_BAKE_MATERIAL: missing active surface")
        output = outputs[0]
        if output.inputs["Volume"].is_linked or output.inputs["Displacement"].is_linked:
            raise ValueError("CC_BAKE_MATERIAL: volume/displacement cannot be flattened")
        shader = output.inputs["Surface"].links[0].from_node
        if shader.type == "EMISSION":
            if shader.inputs["Strength"].is_linked or abs(shader.inputs["Strength"].default_value - 1) > 1e-6:
                raise ValueError("CC_BAKE_MATERIAL: emission strength must be one")
            self.socket = shader.inputs["Color"]
            self.kind = "emission_source"
        elif shader.type == "BSDF_PRINCIPLED":
            for name in ("Metallic", "Transmission Weight", "Coat Weight", "Sheen Weight", "Subsurface Weight", "Anisotropic"):
                socket = shader.inputs.get(name)
                if socket and (socket.is_linked or abs(socket.default_value) > 1e-6):
                    raise ValueError(f"CC_BAKE_MATERIAL: unsupported {name}")
            for name, value in (("Alpha", 1.0), ("Roughness", .75)):
                socket = shader.inputs[name]
                if socket.is_linked or abs(socket.default_value - value) > 1e-5:
                    raise ValueError(f"CC_BAKE_MATERIAL: unsupported {name}")
            if not _implicit_surface_normal(shader.inputs["Normal"]):
                raise ValueError("CC_BAKE_MATERIAL: unsupported Normal")
            for name in ("Coat Normal", "Tangent"):
                if shader.inputs.get(name) and shader.inputs[name].is_linked:
                    raise ValueError(f"CC_BAKE_MATERIAL: unsupported {name}")
            emission = shader.inputs.get("Emission Strength")
            emission_color = shader.inputs.get("Emission Color")
            if emission and (emission.is_linked or (
                abs(emission.default_value) > 1e-6 and emission_color
                and (emission_color.is_linked or any(abs(c) > 1e-6 for c in emission_color.default_value[:3]))
            )):
                raise ValueError("CC_BAKE_MATERIAL: emissive Principled requires a separate emission material")
            self.socket = shader.inputs["Base Color"]
            self.kind = "albedo"
        else:
            raise ValueError(f"CC_BAKE_MATERIAL: unsupported surface {shader.type}")
        self.expression = self._expression(self.socket, set())

    def _expression(self, socket, seen):
        if not socket.is_linked:
            value = list(socket.default_value)
            if len(value) != 4 or abs(value[3] - 1) > 1e-6 or any(not 0 <= c <= 1 for c in value):
                raise ValueError("CC_BAKE_MATERIAL: nonopaque/out-of-range color")
            return {"type": "constant", "rgba": value}
        link = socket.links[0]
        node = link.from_node
        if node in seen:
            raise ValueError("CC_BAKE_MATERIAL: cyclic color graph")
        seen = seen | {node}
        if node.type == "RGB":
            value = list(node.outputs[0].default_value)
            if any(not 0 <= c <= 1 for c in value):
                raise ValueError("CC_BAKE_MATERIAL: out-of-range RGB source")
            return {"type": "constant", "rgba": value}
        if node.type == "TEX_IMAGE" and link.from_socket.name == "Color":
            image = node.image
            if not image or image.source not in {"FILE", "GENERATED"} or node.projection != "FLAT" or node.extension not in {"REPEAT", "EXTEND"} or node.interpolation not in {"Linear", "Closest"}:
                raise ValueError("CC_BAKE_MATERIAL: unsupported image projection/sampling")
            vector = node.inputs["Vector"]
            if vector.is_linked:
                uv_link = vector.links[0]
                explicit_uv = uv_link.from_node.type == "UVMAP"
                active_uv = uv_link.from_node.type == "TEX_COORD" and uv_link.from_socket.name == "UV"
                if not (explicit_uv or active_uv):
                    raise ValueError("CC_BAKE_MATERIAL: image coordinates must be original UVs")
                if explicit_uv and uv_link.from_node.uv_map not in {"", SOURCE_UV}:
                    raise ValueError("CC_BAKE_MATERIAL: unexpected UV layer")
            if image.name not in self.pixels:
                width, height = image.size
                if not width or not height:
                    raise ValueError("CC_BAKE_MATERIAL: unreadable image")
                data = np.empty(width * height * 4, dtype=np.float32)
                image.pixels.foreach_get(data)
                data = data.reshape((height, width, 4))
                if not np.isfinite(data).all() or np.any(data[:, :, 3] < 1 - 1e-5) or np.any(data[:, :, :3] < 0) or np.any(data[:, :, :3] > 1 + 1e-5):
                    raise ValueError("CC_BAKE_MATERIAL: nonopaque/invalid source image")
                self.pixels[image.name] = data
            data = self.pixels[image.name]
            reference = None
            if not image.packed_file:
                path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    raise ValueError(f"CC_BAKE_TEXTURE_PATH: {image.filepath}")
                reference = path.relative_to(self.root).as_posix()
                self.textures.add(reference)
            uv = node.id_data.nodes.new("ShaderNodeUVMap")
            uv.uv_map = SOURCE_UV
            node.id_data.links.new(uv.outputs["UV"], vector)
            # Image names are Blender session IDs, not portable source identity.
            return {"type": "image", "path": reference, "pixels_sha256": hashlib.sha256(data.tobytes()).hexdigest(),
                    "size": [data.shape[1], data.shape[0]], "extension": node.extension,
                    "interpolation": node.interpolation, "colorspace": image.colorspace_settings.name,
                    "image_id": image.name}
        if node.type == "MIX_RGB" and node.blend_type == "MULTIPLY" and not node.use_clamp and not node.inputs[0].is_linked and abs(node.inputs[0].default_value - 1) < 1e-6:
            return {"type": "multiply", "a": self._expression(node.inputs[1], seen), "b": self._expression(node.inputs[2], seen)}
        raise ValueError(f"CC_BAKE_MATERIAL: unsupported color node {node.type}")

    def descriptor(self):
        def portable(expr):
            return {key: portable(value) if isinstance(value, dict) else value for key, value in expr.items() if key != "image_id"}
        return {"kind": self.kind, "color": portable(self.expression)}

    def sample(self, uv):
        def evaluate(expr):
            if expr["type"] == "constant":
                return np.array(expr["rgba"][:3], dtype=float)
            if expr["type"] == "multiply":
                return evaluate(expr["a"]) * evaluate(expr["b"])
            image = self.pixels[expr["image_id"]]
            h, w = image.shape[:2]
            point = np.array(uv, dtype=float)
            point = point % 1 if expr["extension"] == "REPEAT" else np.clip(point, 0, 1)
            x, y = point * [w, h] - .5
            if expr["interpolation"] == "Closest":
                ix, iy = int(np.floor(x + .5)), int(np.floor(y + .5))
                if expr["extension"] == "REPEAT":
                    return image[iy % h, ix % w, :3].astype(float)
                return image[min(max(iy, 0), h - 1), min(max(ix, 0), w - 1), :3].astype(float)
            ix, iy = int(np.floor(x)), int(np.floor(y))
            fx, fy = x - ix, y - iy
            def pixel(a, b):
                if expr["extension"] == "REPEAT":
                    return image[b % h, a % w, :3]
                return image[min(max(b, 0), h - 1), min(max(a, 0), w - 1), :3]
            return (pixel(ix, iy) * (1-fx) + pixel(ix+1, iy) * fx) * (1-fy) + (pixel(ix, iy+1) * (1-fx) + pixel(ix+1, iy+1) * fx) * fy
        color = evaluate(self.expression)
        if not np.isfinite(color).all() or np.any(color < 0) or np.any(color > 1 + 1e-5):
            raise ValueError("CC_BAKE_MATERIAL: color outside opaque atlas range")
        return color

    def emit(self):
        tree = self.material.node_tree
        output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
        emission = tree.nodes.new("ShaderNodeEmission")
        if self.socket.is_linked:
            tree.links.new(self.socket.links[0].from_socket, emission.inputs["Color"])
        else:
            emission.inputs["Color"].default_value = self.socket.default_value
        tree.links.new(emission.outputs[0], output.inputs["Surface"])


class SocketSurface:
    def __init__(self, mesh, bone, socket, radius, sources):
        mesh.data.calc_loop_triangles()
        group = mesh.vertex_groups.get(bone)
        if group is None:
            raise ValueError(f"CC_BAKE_SOCKET: missing adjacent bone {bone}")
        triangles = []
        for tri in mesh.data.loop_triangles:
            vertices = [mesh.data.vertices[i] for i in tri.vertices]
            weighted = any(any(g.group == group.index and g.weight > 1e-6 for g in v.groups) for v in vertices)
            near = (closest_point_on_tri(socket, *(v.co for v in vertices)) - socket).length <= radius
            if weighted and near:
                triangles.append(tri)
        if not triangles:
            raise ValueError(f"CC_BAKE_SOCKET: no actual {bone} socket-surface neighborhood")
        self.mesh = mesh
        self.triangles = triangles
        self.sources = sources
        self.bvh = BVHTree.FromPolygons([v.co for v in mesh.data.vertices], [tuple(t.vertices) for t in triangles], all_triangles=True)

    def sample(self, position):
        point, _, index, _ = self.bvh.find_nearest(position)
        if point is None:
            raise ValueError("CC_BAKE_SOCKET: no adjacent surface")
        tri = self.triangles[index]
        a, b, c = (self.mesh.data.vertices[i].co for i in tri.vertices)
        v0, v1, v2 = b-a, c-a, point-a
        d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
        denom = d00*d11-d01*d01
        if abs(denom) < 1e-20:
            raise ValueError("CC_BAKE_SOCKET: degenerate adjacent triangle")
        beta = (d11*v2.dot(v0)-d01*v2.dot(v1))/denom
        gamma = (d00*v2.dot(v1)-d01*v2.dot(v0))/denom
        uv_layer = self.mesh.data.uv_layers[SOURCE_UV]
        uv = sum((uv_layer.data[loop].uv * weight for loop, weight in zip(tri.loops, (1-beta-gamma, beta, gamma))), Vector((0, 0)))
        return self.sources[tri.material_index].sample(uv)


def _prepare(root, manifest, textures, pixels, *, inspect):
    def prepare(arm, records, branches):
        sources = {}
        for record in records:
            mesh = record["object"]
            had_uv = bool(mesh.data.uv_layers.active)
            if had_uv:
                previous_uv = mesh.data.uv_layers.active.name
                for slot in mesh.material_slots:
                    if slot.material and slot.material.use_nodes:
                        for node in slot.material.node_tree.nodes:
                            if node.type == "UVMAP" and node.uv_map == previous_uv:
                                node.uv_map = SOURCE_UV
                mesh.data.uv_layers.active.name = SOURCE_UV
            else:
                mesh.data.uv_layers.new(name=SOURCE_UV)
            slot_sources = [ColorSource(slot.material, root, textures, pixels) for slot in mesh.material_slots]
            def textured(expression):
                return expression["type"] == "image" or any(
                    textured(value) for value in expression.values() if isinstance(value, dict))
            if not had_uv and any(textured(source.expression) for source in slot_sources):
                raise ValueError("CC_BAKE_MATERIAL: textured mesh has no original UVs")
            sources[mesh.name] = slot_sources
            manifest.append({"branch_id": record["branch_id"], "part_id": record["part_id"],
                             "connector": record["connector"], "slots": [s.descriptor() for s in slot_sources]})
        bodies = {r["branch_id"]: r["object"] for r in records if not r["connector"]}
        for record, descriptor in zip(records, manifest):
            if not record["connector"]:
                continue
            branch = branches[record["branch_id"]]
            mesh = record["object"]
            socket = Vector(to_blender(branch["snap"]["position_m"]))
            radius = max((v.co - socket).length for v in mesh.data.vertices) * 2 + .001
            child = bodies[branch["branch_id"]]
            parent = bodies.get(branch["parent_branch"])
            if parent is None:
                candidates = [body for body in bodies.values() if body is not child and body.vertex_groups.get(branch["attach_bone"])]
                if len(candidates) != 1:
                    raise ValueError("CC_BAKE_SOCKET: missing/ambiguous actual parent surface")
                parent = candidates[0]
            parent_surface = SocketSurface(parent, branch["attach_bone"], socket, radius, sources[parent.name])
            child_surface = SocketSurface(child, branch["bone_names"][0], socket, radius, sources[child.name])
            child_group = mesh.vertex_groups[branch["bone_names"][0]].index
            attribute = mesh.data.color_attributes.new(name=TRANSITION, type="FLOAT_COLOR", domain="CORNER")
            colors = []
            for loop in mesh.data.loops:
                vertex = mesh.data.vertices[loop.vertex_index]
                weight = next((g.weight for g in vertex.groups if g.group == child_group), 0.0)
                # The existing connector's authoritative analytical axial smoothstep is its child influence.
                color = parent_surface.sample(vertex.co) * (1-weight) + child_surface.sample(vertex.co) * weight
                attribute.data[loop.index].color = (*color, 1)
                colors.extend(color)
            descriptor["transition_sha256"] = hashlib.sha256(np.array(colors, dtype=np.float32).tobytes()).hexdigest()
            if not inspect:
                material = bpy.data.materials.new(f"Transition_{record['branch_id']}")
                material.use_nodes = True
                tree = material.node_tree
                tree.nodes.clear()
                output = tree.nodes.new("ShaderNodeOutputMaterial")
                emission = tree.nodes.new("ShaderNodeEmission")
                color = tree.nodes.new("ShaderNodeVertexColor")
                color.layer_name = TRANSITION
                tree.links.new(color.outputs["Color"], emission.inputs["Color"])
                tree.links.new(emission.outputs[0], output.inputs["Surface"])
                mesh.data.materials.clear()
                mesh.data.materials.append(material)
                for polygon in mesh.data.polygons:
                    polygon.material_index = 0
        if not inspect:
            for record in records:
                if not record["connector"]:
                    for source in sources[record["object"].name]:
                        source.emit()
    return prepare


def _signature(arm, body):
    return _hash({"vertices": [list(v.co) for v in body.data.vertices],
                  "polygons": [list(p.vertices) for p in body.data.polygons],
                  "weights": [[(body.vertex_groups[g.group].name, g.weight) for g in v.groups] for v in body.data.vertices],
                  "bind": [(b.name, list(map(list, b.matrix_local))) for b in arm.data.bones]})


def _fbx_material_fixture(args):
    from .ops_binding_fixture import _skinned_asset
    root = Path(args["library_dir"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    albedo = "#9b6874"
    _skinned_asset(root / "source.fbx",
                   [{"name": "b0", "parent": "", "head_m": [0, 0, 0],
                     "tail_m": [0, 1, 0], "up_m": [0, 0, 1]}],
                   [(-.5, -.5, 0), (.5, -.5, 0), (0, .5, 0), (0, 0, 1)],
                   [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)],
                   [{"b0": 1}] * 4, [("SourceMaterial", albedo)], [0] * 4)
    rigkit.reset_scene()
    body = ops_assemble._import_part_mesh(str(root / "source.fbx"))
    material = body.data.materials[0]
    shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    normal_socket = shader.inputs["Normal"]
    if not normal_socket.is_linked or normal_socket.links[0].from_node.type != "NORMAL_MAP":
        raise ValueError("FBX fixture did not exercise the imported default NormalMap")
    normal_map = normal_socket.links[0].from_node
    unsupported = args.get("unsupported_normal")
    if unsupported == "texture":
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.new("NormalTexture", width=2, height=2)
        material.node_tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
    elif unsupported == "bump":
        bump = material.node_tree.nodes.new("ShaderNodeBump")
        material.node_tree.links.new(bump.outputs["Normal"], normal_socket)
    elif unsupported == "nonneutral":
        normal_map.inputs["Color"].default_value = (.65, .5, 1, 1)
    source = ColorSource(material, root, set(), {})
    source_albedo = source.sample((.5, .5))
    ops_assemble._setup_render(256, 256)
    ops_assemble._camera("iso", 1.3)
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"

    def render(name):
        path = root / f"{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(path), check_existing=False)
        values = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(values)
        bpy.data.images.remove(image)
        return values.reshape((-1, 4))[:, :3]

    imported_pixels = render("imported_normal")
    material.node_tree.links.remove(normal_socket.links[0])
    implicit_pixels = render("implicit_normal")
    shading_error = float(np.max(np.abs(imported_pixels - implicit_pixels)))
    rigkit.select_only([body])
    body.data.uv_layers.new(name=SOURCE_UV)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=.05)
    bpy.ops.object.mode_set(mode="OBJECT")
    source.emit()
    image = bpy.data.images.new("AlbedoProof", width=64, height=64, alpha=False, float_buffer=True)
    target = material.node_tree.nodes.new("ShaderNodeTexImage")
    target.image = image
    material.node_tree.nodes.active = target
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT", margin=2, use_clear=True)
    values = np.empty(64 * 64 * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    values = values.reshape((64, 64, 4))
    colors = []
    for polygon in body.data.polygons:
        uv = sum((body.data.uv_layers[SOURCE_UV].data[i].uv for i in polygon.loop_indices),
                 Vector((0, 0))) / len(polygon.loop_indices)
        colors.append(values[min(int(uv.y*64), 63), min(int(uv.x*64), 63), :3])
    return {"default_normal_shading_error": shading_error,
            "source_albedo_error": float(np.max(np.abs(source_albedo-np.array(rigkit.hex_to_linear(albedo)[:3])))),
            "baked_albedo_error": float(np.max(np.abs(np.array(colors)-source_albedo))),
            "paired_images": [str(root / "imported_normal.png"), str(root / "implicit_normal.png")]}


def _material_fixture(args):
    root = Path(args["library_dir"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    rigkit.reset_scene()
    image = bpy.data.images.new("SocketGradient", width=8, height=8, alpha=False, float_buffer=True)
    values = np.array([((x+.5)/8, (y+.5)/8, .2, 1) for y in range(8) for x in range(8)], dtype=np.float32)
    image.pixels.foreach_set(values.reshape(-1))
    image.filepath_raw = str(root / "gradient.png")
    image.file_format = "PNG"
    image.save()
    parent_material = rigkit.flesh_material("Parent", "#ffffff")
    texture = parent_material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    shader = next(n for n in parent_material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    parent_material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    if args.get("unsupported"):
        shader.inputs[args["unsupported"]].default_value = .5
    child_material = rigkit.flesh_material("Child", "#ffffff")
    child_shader = next(n for n in child_material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    child_shader.inputs["Base Color"].default_value = (.8, .6, .4, 1)

    def triangle(name, material, bone):
        data = bpy.data.meshes.new(name)
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        data.materials.append(material)
        uv = data.uv_layers.new(name=SOURCE_UV)
        for loop, value in zip(uv.data, ((0, 0), (1, 0), (0, 1))):
            loop.uv = value
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.vertex_groups.new(name=bone).add([0, 1, 2], 1, "REPLACE")
        return obj

    parent = triangle("ParentSurface", parent_material, "parent")
    child = triangle("ChildSurface", child_material, "child")
    connector = triangle("Connector", rigkit.flesh_material("Connector", "#ffffff"), "parent")
    connector.vertex_groups["parent"].add([0, 1, 2], .75, "REPLACE")
    connector.vertex_groups.new(name="child").add([0, 1, 2], .25, "REPLACE")
    for vertex, position in zip(connector.data.vertices, ((.25, .25, .1), (.26, .25, .1), (.25, .26, .1))):
        vertex.co = position
    records = [{"object": obj, "branch_id": branch, "part_id": branch, "connector": is_connector}
               for obj, branch, is_connector in ((parent, "parent_branch", False), (child, "child_branch", False),
                                                (connector, "child_branch", True))]
    manifest, textures, pixels = [], set(), {}
    branches = {
        "parent_branch": {"branch_id": "parent_branch", "parent_branch": "", "attach_bone": "root",
                          "bone_names": ["parent"], "snap": {"position_m": [0, 0, 0],
                          "rotation_xyzw": [0, 0, 0, 1]}},
        "child_branch": {"branch_id": "child_branch", "parent_branch": "parent_branch",
                         "attach_bone": "parent", "bone_names": ["child"],
                         "snap": {"position_m": [-.25, 0, .25], "rotation_xyzw": [0, 0, 0, 1]}},
    }
    _prepare(root, manifest, textures, pixels, inspect=True)(None, records, branches)
    attribute = connector.data.color_attributes[TRANSITION]
    return {"transition_color": list(attribute.data[0].color),
            "expected_color": [.3875, .3375, .25, 1],
            "texture_paths": sorted(textures),
            "source_uv": [list(v.uv) for v in parent.data.uv_layers[SOURCE_UV].data],
            "connector_weights": [[g.weight for g in v.groups] for v in connector.data.vertices]}


def _capture(arm, body, directory, label):
    directory.mkdir(parents=True, exist_ok=True)
    height = max(v.co.z for v in body.data.vertices) + .3
    ops_assemble._setup_render(512, 512)
    ops_assemble._camera("iso", height)
    frames = []
    for clip in ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"):
        action = bpy.data.actions.get(clip)
        if action is None:
            raise ValueError(f"CC_BUILT_CLIPS_MISSING: {clip}")
        arm.animation_data.action = action
        for phase in (0.0, .5, 1.0):
            start, end = action.frame_range
            bpy.context.scene.frame_set(int(round(start + (end-start)*phase)))
            evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                frames.append(np.array([list(evaluated.matrix_world @ v.co) for v in mesh.vertices]))
            finally:
                evaluated.to_mesh_clear()
        bpy.context.scene.render.filepath = str(directory / f"{label}_{clip}.png")
        bpy.ops.render.render(write_still=True)
    return frames


def run(args: dict[str, Any]) -> dict[str, Any]:
    root = Path(args["library_dir"]).resolve()
    inspect = args.get("inspect", False)
    if args.get("material_fixture"):
        return _material_fixture(args)
    if args.get("fbx_material_fixture"):
        return _fbx_material_fixture(args)
    live_frames = None
    if args.get("proof_dir") and not inspect:
        live_arm, live_body = ops_assemble.assemble(args["skeleton"], args["parts"], str(root),
                                                  args["recipe"], args["gait_profile"])
        live_frames = _capture(live_arm, live_body, Path(args["proof_dir"]), "live")
    manifest, textures, pixels = [], set(), {}
    arm, body = ops_assemble.assemble(args["skeleton"], args["parts"], str(root), args["recipe"], args["gait_profile"],
                                    prepare_pieces=_prepare(root, manifest, textures, pixels, inspect=inspect))
    if inspect:
        return {"materials": manifest, "texture_paths": sorted(textures)}
    if _hash(manifest) != args["expected_material_sha256"]:
        raise ValueError("CC_BAKE_STALE: materials changed after inspection")
    triangles = rigkit.triangle_count(body)
    influences = max(len([g for g in v.groups if g.weight > 0]) for v in body.data.vertices)
    if triangles > 30000 or len(arm.data.bones) > 120 or influences > 4:
        raise ValueError("CC_BAKE_BUDGET: triangles/bones/influences")
    before = _signature(arm, body)
    original_uv = np.array([list(loop.uv) for loop in body.data.uv_layers[SOURCE_UV].data], dtype=np.float32)
    body.data.uv_layers.new(name=ATLAS_UV)
    body.data.uv_layers.active = body.data.uv_layers[ATLAS_UV]
    body.data.uv_layers[ATLAS_UV].active_render = True
    rigkit.select_only([body])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.151917, island_margin=0.0, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    # FRACTION measures final UV-space separation; two 32px gutters need a 64px gap.
    bpy.ops.uv.pack_islands(rotate=True, margin_method="FRACTION", margin=64/4096)
    bpy.ops.object.mode_set(mode="OBJECT")
    image = bpy.data.images.new("CreatureAtlas", width=4096, height=4096, alpha=False, float_buffer=False)
    image.generated_color = (0, 0, 0, 1)
    image.colorspace_settings.name = "sRGB"
    for material in body.data.materials:
        target = material.node_tree.nodes.new("ShaderNodeTexImage")
        target.image = image
        material.node_tree.nodes.active = target
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    arm.data.pose_position = "REST"
    bpy.ops.object.bake(type="EMIT", margin=32, margin_type="EXTEND", use_clear=True)
    atlas_pixels = np.empty(4096 * 4096 * 4, dtype=np.float32)
    image.pixels.foreach_get(atlas_pixels)
    atlas_pixels[3::4] = 1.0
    image.pixels.foreach_set(atlas_pixels)
    del atlas_pixels
    image.filepath_raw = args["out_png"]
    image.file_format = "PNG"
    image.save()
    # Unity reads UV0; retain the original layer as UV1 only AFTER all source sampling/baking.
    body.data.uv_layers.remove(body.data.uv_layers[SOURCE_UV])
    preserved = body.data.uv_layers.new(name=SOURCE_UV)
    preserved.data.foreach_set("uv", original_uv.reshape(-1))
    body.data.uv_layers.active = body.data.uv_layers[ATLAS_UV]
    body.data.uv_layers[ATLAS_UV].active_render = True
    material = rigkit.flesh_material("CreatureAtlasOpaque", "#ffffff")
    tree = material.node_tree
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = ATLAS_UV
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    tree.links.new(uv.outputs["UV"], texture.inputs["Vector"])
    shader = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    body.data.materials.clear()
    body.data.materials.append(material)
    for polygon in body.data.polygons:
        polygon.material_index = 0
    if _signature(arm, body) != before:
        raise ValueError("CC_BAKE_BIND: atlas operation changed geometry/skin/bind")
    arm.data.pose_position = "POSE"
    ops_skeleton.clear_pose(arm)
    rigkit.export_fbx(args["out_fbx"], [arm, body], animated=False)
    pose_error = None
    if live_frames is not None:
        baked_frames = _capture(arm, body, Path(args["proof_dir"]), "baked")
        pose_error = max(float(np.max(np.linalg.norm(a-b, axis=1))) for a, b in zip(live_frames, baked_frames))
        if pose_error > 1e-5:
            raise ValueError(f"CC_BAKE_BIND: clip pose changed {pose_error}m")
        (Path(args["proof_dir"]) / "comparison.json").write_text(
            json.dumps({"max_pose_error_m": pose_error, "geometry_bind_sha256": before,
                        "clips": ["idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"],
                        "frames_per_clip": 3, "atlas_size": 4096, "gutter_px": 32}, sort_keys=True),
            encoding="utf-8")
    return {"triangles": triangles, "bones": len(arm.data.bones), "materials": len(body.data.materials),
            "max_influences": influences, "geometry_bind_sha256": before, "atlas_size": [4096, 4096],
            "gutter_px": 32, "clip_source": args["skeleton"]["asset"]["fbx"],
            "comparison_max_pose_error_m": pose_error}
