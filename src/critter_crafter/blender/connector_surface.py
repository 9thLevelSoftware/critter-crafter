"""Native Blender SDF collars, evaluated and simplified into ordinary bound meshes."""
from __future__ import annotations

import math
from typing import Any

from .frame import to_blender, to_gltf

DEFAULT_SURFACE = {"method": "sdf_grid", "voxel_divisions": 24, "fillet_iterations": 3, "bulge": 1.08}


def grid_spec(rx: float, ry: float, span: list[float], surface: dict[str, Any]) -> tuple[float, tuple[int, int, int]]:
    if (set(surface) != set(DEFAULT_SURFACE) or surface.get("method") != "sdf_grid"
            or type(surface.get("voxel_divisions")) is not int or not 12 <= surface["voxel_divisions"] <= 64
            or type(surface.get("fillet_iterations")) is not int or not 0 <= surface["fillet_iterations"] <= 8
            or not isinstance(surface.get("bulge"), (float, int)) or not 1 <= surface["bulge"] <= 1.2):
        raise ValueError("CC_CONNECTOR_SURFACE: invalid native SDF settings")
    if len(span) != 2 or not all(math.isfinite(v) for v in (rx, ry, *span)) or min(rx, ry) <= 0 or not span[0] < 0 < span[1]:
        raise ValueError("CC_CONNECTOR_ENVELOPE: invalid physical envelope")
    length = span[1] - span[0]
    voxel = min(rx, ry, length) / surface["voxel_divisions"]
    cells = tuple(math.ceil(size / voxel) + 8 for size in (2 * rx, 2 * ry, length))
    if max(cells) > 1024 or math.prod(cells) > 256 ** 3:
        raise ValueError(f"CC_CONNECTOR_GRID_LIMIT: padded grid {cells}; voxel size must not be coarsened")
    return voxel, cells


def profile_angle(profile: dict[str, Any]) -> float:
    limits = profile["joints"][0]["limits_deg"]
    angle = max(abs(float(value)) for axis in ("swing_x", "swing_y", "twist") for value in limits[axis])
    if not math.isfinite(angle) or not 0 <= angle < 180:
        raise ValueError("CC_CONNECTOR_DEFORMATION: two-bone LBS cannot preserve an interface at 180 degrees")
    return math.radians(angle)


def deformation_radius(z: float, span: list[float], angle: float, margin: float = .6) -> float:
    """Circular radius bound for positive smoothstep-LBS Jacobians at every bend up to angle.

    det(A) = D = 1-2*w*(1-w)*(1-cos(theta)); the rank-one weight-gradient update has
    determinant factor 1+w'/D*((2*w-1)*(1-cos(theta))*z +/- sin(theta)*r).
    At a symmetric midpoint this gives r <= (1-margin)*span/(3*tan(theta/2)).
    Minimize (a+b*cos(theta))/sin(theta) analytically, including its interior critical angle.
    """
    length = span[1] - span[0]
    t = min(1.0, max(0.0, (z - span[0]) / length))
    w = t * t * (3 - 2 * t)
    derivative = 6 * t * (1 - t) / length
    if derivative <= 1e-12 or angle <= 1e-12:
        return math.inf
    a = (1 - margin) * (1 - 2 * w * (1 - w)) / derivative + (2 * w - 1) * z
    b = (1 - margin) * 2 * w * (1 - w) / derivative - (2 * w - 1) * z
    worst_angle = angle
    if a > 0 and -1 < -b / a < 1:
        worst_angle = min(angle, math.acos(-b / a))
    radius = (a + b * math.cos(worst_angle)) / math.sin(worst_angle)
    if radius <= 0 or not math.isfinite(radius):
        raise ValueError("CC_CONNECTOR_DEFORMATION: asymmetric span reverses the interface centerline")
    return radius


def _interval_radius(low: float, high: float, span: list[float], angle: float) -> float:
    """Lower bound the .25-margin radius for every z and bend angle in this interval."""
    if angle <= 1e-12:
        return math.inf
    length = span[1] - span[0]
    t0 = min(1.0, max(0.0, (low - span[0]) / length))
    t1 = min(1.0, max(0.0, (high - span[0]) / length))
    w0, w1 = t0*t0*(3-2*t0), t1*t1*(3-2*t1)
    max_t_product = .25 if t0 <= .5 <= t1 else max(t0*(1-t0), t1*(1-t1))
    derivative_max = 6 * max_t_product / length
    if derivative_max <= 1e-12:
        return math.inf
    max_w_product = .25 if w0 <= .5 <= w1 else max(w0*(1-w0), w1*(1-w1))
    cosine_loss = 1 - math.cos(angle)
    determinant_min = 1 - 2 * max_w_product * cosine_loss
    axial_min = min((2*w-1)*z for w in (w0, w1) for z in (low, high))
    numerator_min = .75 * determinant_min / derivative_max + min(0.0, axial_min) * cosine_loss
    sine_max = 1.0 if angle >= math.pi / 2 else math.sin(angle)
    return max(0.0, numerator_min / sine_max)


def certify_skin_envelope(vertices: list, faces: list, span: list[float], angle: float) -> float:
    """Certify complete triangle interiors, returning their conservative radius/bound ratio.

    A fixed-z triangle section is a line segment whose greatest transverse norm lies on
    an edge. Each complete edge is covered by certified intervals: norm convexity bounds
    radius by interval endpoint norms, and interval arithmetic bounds all permitted z/angles.
    Samples only identify failure witnesses; they never establish acceptance.
    """
    seen = set()
    maximum_fraction = 0.0
    for face in faces:
        for first, second in zip(face, (*face[1:], face[0])):
            edge = (min(first, second), max(first, second))
            if edge in seen:
                continue
            seen.add(edge)
            stack = [(vertices[first], vertices[second], 0)]
            while stack:
                a, b, depth = stack.pop()
                low, high = min(a[2], b[2]), max(a[2], b[2])
                bound = _interval_radius(low, high, span, angle)
                radius = max(math.hypot(a[0], a[1]), math.hypot(b[0], b[1]))
                if bound > 0 and radius <= bound + 1e-8:
                    maximum_fraction = max(maximum_fraction, radius / bound)
                    continue
                midpoint = tuple((a[i] + b[i]) / 2 for i in range(3))
                for point in (a, midpoint, b):
                    actual = math.hypot(point[0], point[1])
                    permitted = deformation_radius(point[2], span, angle, .25)
                    if actual > permitted + 1e-8:
                        raise ValueError(f"CC_CONNECTOR_DEFORMATION: edge={edge}, canonical_m={[a, b]}, "
                                         f"witness_m={point}, radius_m={actual}, radius_bound_m={permitted}, "
                                         f"mesh_margin=0.25, angle_deg={math.degrees(angle)}")
                if depth >= 20:
                    raise ValueError(f"CC_CONNECTOR_DEFORMATION: uncertified edge={edge}, canonical_m={[a, b]}, "
                                     f"radius_m={radius}, interval_radius_bound_m={bound}, mesh_margin=0.25")
                stack.append((a, midpoint, depth + 1))
                stack.append((midpoint, b, depth + 1))
    return maximum_fraction


def _cap_radius(t: float) -> float:
    if t < .08:
        return math.sqrt(max(0.0, 1 - ((.08 - t) / .08) ** 2))
    if t > .92:
        return math.sqrt(max(0.0, 1 - ((t - .92) / .08) ** 2))
    return 1.0


def _closed_surface(rx: float, ry: float, s0: float, s1: float, angle: float,
                    kind: str, bulge: float) -> tuple[list, list]:
    segments, rings = 64, 64
    span = [s0, s1]
    length = s1 - s0
    centre_t, half_t = .18, .10
    vertices, faces = [], []
    for i in range(1, rings):
        t = i / rings
        if kind == "bulge":
            t = centre_t + half_t * (2 * t - 1)
            radius = math.sqrt(max(0, 1 - ((t - centre_t) / half_t) ** 2))
        else:
            radius = _cap_radius(t)
        z = s0 + length * t
        limit = min(1.0, deformation_radius(z, span, angle) / max(rx, ry))
        if kind == "collar":
            radius *= .70 * limit
        elif kind == "guard":
            radius *= limit
        else:
            radius *= min(1.0, .70 * bulge) * limit
        for j in range(segments):
            azimuth = 2 * math.pi * j / segments
            vertices.append(to_blender((rx * radius * math.cos(azimuth),
                                       ry * radius * math.sin(azimuth), z)))
    count = rings - 1
    for i in range(count - 1):
        for j in range(segments):
            a, b = i * segments + j, i * segments + (j + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    ends = ((0, s0 + length * (centre_t - half_t)),
            (count - 1, s0 + length * (centre_t + half_t))) if kind == "bulge" else ((0, s0), (count - 1, s1))
    for index, (ring, z) in enumerate(ends):
        centre = len(vertices)
        vertices.append(to_blender((0, 0, z)))
        for j in range(segments):
            a, b = ring * segments + j, ring * segments + (j + 1) % segments
            faces.append((centre, b, a) if index == 0 else (centre, a, b))
    return vertices, faces


def validate_mesh(vertices: list, faces: list, rx: float, ry: float, span: list[float], voxel: float,
                  budget: int, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    import bmesh

    if not vertices or not faces:
        raise ValueError("CC_CONNECTOR_MESH: empty evaluated surface")
    triangles = sum(len(face) - 2 for face in faces)
    if triangles > budget:
        raise ValueError(f"CC_CONNECTOR_BUDGET: {triangles} > {budget}")
    bm = bmesh.new()
    try:
        points = [bm.verts.new(point) for point in vertices]
        for face in faces:
            bm.faces.new([points[i] for i in face])
        bm.normal_update()
        if any(not edge.is_manifold for edge in bm.edges) or any(not vert.is_manifold for vert in bm.verts):
            raise ValueError("CC_CONNECTOR_MANIFOLD: closed two-manifold surface required")
        if any(face.calc_area() <= 1e-14 for face in bm.faces):
            raise ValueError("CC_CONNECTOR_DEGENERATE: zero-area surface face")
        if bm.calc_volume(signed=True) <= 0:
            raise ValueError("CC_CONNECTOR_NORMALS: outward closed surface required")
        for point in vertices:
            x, y, z = to_gltf(point)
            radial_error = max(0, math.hypot(x / rx, y / ry) - 1) * max(rx, ry)
            if (not all(math.isfinite(v) for v in (x, y, z)) or radial_error > voxel + 1e-8
                    or abs(y) > ry + voxel + 1e-8 or z < span[0] - voxel - 1e-8 or z > span[1] + voxel + 1e-8):
                raise ValueError("CC_CONNECTOR_ENVELOPE: surface escapes physical envelope by more than one voxel")
        if profile is not None:
            angle = profile_angle(profile)
            canonical = [to_gltf(point) for point in vertices]
            envelope_fraction = certify_skin_envelope(canonical, faces, span, angle)
            from mathutils import Quaternion, Vector

            points = [Vector(point) for point in canonical]
            weights = []
            for point in canonical:
                t = min(1.0, max(0.0, (point[2] - span[0]) / (span[1] - span[0])))
                weights.append(t * t * (3 - 2 * t))
            limits = profile["joints"][0]["limits_deg"]
            for axis_name, axis in (("swing_x", (1, 0, 0)), ("swing_y", (0, 1, 0)), ("twist", (0, 0, 1))):
                for degrees in limits[axis_name]:
                    rotation = Quaternion(axis, math.radians(float(degrees)))
                    deformed = [(1 - w) * point + w * (rotation @ point) for point, w in zip(points, weights)]
                    for face in faces:
                        a, b, c = face
                        normal = (points[b] - points[a]).cross(points[c] - points[a]).normalized()
                        carried = rotation @ normal if weights[a] > .5 else normal
                        current = (deformed[b] - deformed[a]).cross(deformed[c] - deformed[a])
                        if current.length <= 1e-10 or current.normalized().dot(carried) < -.2:
                            raise ValueError(
                                f"CC_CONNECTOR_DEFORMATION: simplified face reverses at {axis_name}={degrees}; "
                                f"profile={profile.get('binding_profile_id')} rx={rx} ry={ry} span={span} "
                                f"face={face} points={[tuple(points[i]) for i in face]} "
                                f"weights={[weights[i] for i in face]} carried={tuple(carried)} "
                                f"posed_cross={tuple(current)} "
                                f"dot={current.normalized().dot(carried) if current.length else None}"
                            )
        return {"triangles": triangles, "manifold": True, "voxel_size_m": voxel,
                **({"jacobian_envelope_ratio_upper_bound": envelope_fraction} if profile is not None else {})}
    finally:
        bm.free()


def build_surface(rx: float, ry: float, span: list[float], surface: dict[str, Any], budget: int,
                  profile: dict[str, Any]) -> tuple[list, list]:
    import bpy

    voxel, _ = grid_spec(rx, ry, span, surface)
    angle = profile_angle(profile)
    objects, meshes = [], []
    tree = None
    try:
        def source(name: str, kind: str) -> Any:
            vertices, faces = _closed_surface(rx, ry, *span, angle, kind, surface["bulge"])
            mesh = bpy.data.meshes.new(name)
            meshes.append(mesh)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            objects.append(obj)
            bpy.context.scene.collection.objects.link(obj)
            return obj

        collar = source("SDF collar", "collar")
        centre = source("SDF parent-side fullness", "bulge")
        guard = source("SDF skin Jacobian envelope", "guard")
        tree = bpy.data.node_groups.new("Connector native SDF", "GeometryNodeTree")
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes, links = tree.nodes, tree.links
        group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
        info = nodes.new("GeometryNodeObjectInfo")
        info.inputs["Object"].default_value = centre
        info.transform_space = "ORIGINAL"
        guard_info = nodes.new("GeometryNodeObjectInfo")
        guard_info.inputs["Object"].default_value = guard
        guard_info.transform_space = "ORIGINAL"
        grids = [nodes.new("GeometryNodeMeshToSDFGrid") for _ in range(3)]
        for grid in grids:
            grid.inputs["Voxel Size"].default_value = voxel
            grid.inputs["Band Width"].default_value = 3
        links.new(group_in.outputs["Geometry"], grids[0].inputs["Mesh"])
        links.new(info.outputs["Geometry"], grids[1].inputs["Mesh"])
        links.new(guard_info.outputs["Geometry"], grids[2].inputs["Mesh"])
        union = nodes.new("GeometryNodeSDFGridBoolean")
        union.operation = "UNION"
        for grid in grids[:2]:
            links.new(grid.outputs["SDF Grid"], union.inputs["Grid"])
        grid_output = union.outputs["Grid"]
        if surface["fillet_iterations"]:
            fillet = nodes.new("GeometryNodeSDFGridFillet")
            fillet.inputs["Iterations"].default_value = surface["fillet_iterations"]
            links.new(grid_output, fillet.inputs["Grid"])
            grid_output = fillet.outputs["Grid"]
        bounded = nodes.new("GeometryNodeSDFGridBoolean")
        bounded.operation = "INTERSECT"
        links.new(grid_output, bounded.inputs["Grid"])
        links.new(grids[2].outputs["SDF Grid"], bounded.inputs["Grid"])
        grid_output = bounded.outputs["Grid"]
        mesher = nodes.new("GeometryNodeGridToMesh")
        mesher.inputs["Threshold"].default_value = 0
        mesher.inputs["Adaptivity"].default_value = 0
        links.new(grid_output, mesher.inputs["Grid"])
        links.new(mesher.outputs["Mesh"], group_out.inputs["Geometry"])
        collar.modifiers.new("Native SDF", "NODES").node_group = tree
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        baked = bpy.data.meshes.new_from_object(collar.evaluated_get(depsgraph), depsgraph=depsgraph)
        meshes.append(baked)
        collar.modifiers.clear()
        collar.data = baked
        baked.calc_loop_triangles()
        count = len(baked.loop_triangles)
        if count > budget:
            decimate = collar.modifiers.new("Existing connector budget", "DECIMATE")
            decimate.use_collapse_triangulate = True
            decimate.ratio = budget / count
            failure = None
            for _ in range(16):
                bpy.context.view_layer.update()
                reduced = bpy.data.meshes.new_from_object(collar.evaluated_get(depsgraph), depsgraph=depsgraph)
                meshes.append(reduced)
                reduced.calc_loop_triangles()
                if len(reduced.loop_triangles) <= budget:
                    vertices = [tuple(v.co) for v in reduced.vertices]
                    faces = [tuple(triangle.vertices) for triangle in reduced.loop_triangles]
                    try:
                        validate_mesh(vertices, faces, rx, ry, span, voxel, budget, profile)
                    except ValueError as exc:
                        if not str(exc).startswith((
                            "CC_CONNECTOR_DEFORMATION:", "CC_CONNECTOR_DEGENERATE:",
                            "CC_CONNECTOR_MANIFOLD:", "CC_CONNECTOR_NORMALS:", "CC_CONNECTOR_ENVELOPE:",
                        )):
                            raise
                        failure = exc
                    else:
                        return vertices, faces
                decimate.ratio *= .95
            else:
                raise ValueError(
                    f"CC_CONNECTOR_BUDGET: simplification cannot meet authored budget and surface/skin constraints; {failure}"
                ) from failure
        baked.calc_loop_triangles()
        vertices = [tuple(v.co) for v in baked.vertices]
        faces = [tuple(triangle.vertices) for triangle in baked.loop_triangles]
        validate_mesh(vertices, faces, rx, ry, span, voxel, budget, profile)
        return vertices, faces
    finally:
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)
