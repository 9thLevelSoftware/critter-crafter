"""Read GLB/FBX skin rest information independently of node animation poses."""
from __future__ import annotations
import bisect
import json
import math
import os
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any
from .. import mathutil as mu


REQUIRED_CLIPS = {"idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"}
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_COMPONENT_FORMAT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
                     5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}


def matrix_multiply(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def matrix_inverse(matrix):
    rows = [list(row) + [float(i == j) for j in range(4)] for i, row in enumerate(matrix)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda row: abs(rows[row][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        divisor = rows[col][col]
        if abs(divisor) < 1e-12:
            raise ValueError("CC_EXPORT_SINGULAR_BIND")
        rows[col] = [v / divisor for v in rows[col]]
        for row in range(4):
            if row != col:
                factor = rows[row][col]
                rows[row] = [a-factor*b for a,b in zip(rows[row], rows[col])]
    return [r[4:] for r in rows]


def transform_error(a, b):
    distance = math.sqrt(sum((a[i][3]-b[i][3])**2 for i in range(3)))
    columns_a = [mu.normalize([a[i][j] for i in range(3)]) for j in range(3)]
    columns_b = [mu.normalize([b[i][j] for i in range(3)]) for j in range(3)]
    trace = sum(mu.dot(x,y) for x,y in zip(columns_a, columns_b))
    angle = math.degrees(math.acos(max(-1., min(1., (trace-1)/2))))
    return distance, angle


def bone_matrix(bone):
    y = mu.normalize(mu.sub(bone["tail_m"], bone["head_m"]))
    z = mu.normalize(mu.sub(bone["up_m"], mu.scale(y, mu.dot(y, bone["up_m"]))))
    x = mu.cross(y, z)
    return [[x[i], y[i], z[i], bone["head_m"][i]] for i in range(3)] + [[0,0,0,1]]


def _node_matrix(node):
    if "matrix" in node:
        result = [[node["matrix"][j*4+i] for j in range(4)] for i in range(4)]
    else:
        q = node.get("rotation", [0,0,0,1]); s = node.get("scale", [1,1,1]); t = node.get("translation", [0,0,0])
        columns = [mu.quat_rotate(q, [s[j] if i == j else 0 for i in range(3)]) for j in range(3)]
        result = [[columns[j][i] for j in range(3)] + [t[i]] for i in range(3)] + [[0,0,0,1]]
    if any(not math.isfinite(float(value)) for row in result for value in row):
        raise ValueError(f"CC_GLB_NODE_NONFINITE: {node.get('name', '<unnamed>')}")
    return result


def _rotation_matrix(matrix):
    columns = [mu.normalize([matrix[i][j] for i in range(3)]) for j in range(3)]
    return [[columns[j][i] for j in range(3)] + [0.] for i in range(3)] + [[0., 0., 0., 1.]]


def _quat_matrix(q):
    columns = [mu.quat_rotate(q, [1. if i == j else 0. for i in range(3)]) for j in range(3)]
    return [[columns[j][i] for j in range(3)] + [0.] for i in range(3)] + [[0., 0., 0., 1.]]


def _transform_point(matrix, point):
    return [sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3)]


def _distance(a, b):
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def _normalized_component(value: int | float, component_type: int, normalized: bool) -> float | int:
    if not normalized or component_type == 5126:
        return value
    if component_type == 5120:
        return max(float(value) / 127., -1.)
    if component_type == 5121:
        return float(value) / 255.
    if component_type == 5122:
        return max(float(value) / 32767., -1.)
    if component_type == 5123:
        return float(value) / 65535.
    return value


def _read_elements(document: dict[str, Any], binary: bytes, *, view_index: int, byte_offset: int,
                   component_type: int, components: int, count: int, stride: int | None = None,
                   normalized: bool = False) -> list[list[float | int]]:
    view = document["bufferViews"][view_index]
    if view.get("buffer", 0) != 0:
        raise ValueError("CC_GLB_EXTERNAL_BUFFER")
    try:
        code, size = _COMPONENT_FORMAT[component_type]
    except KeyError as exc:
        raise ValueError(f"CC_GLB_ACCESSOR_COMPONENT: {component_type}") from exc
    packed = components * size
    step = stride if stride is not None else view.get("byteStride", packed)
    if step < packed:
        raise ValueError("CC_GLB_ACCESSOR_STRIDE")
    start = int(view.get("byteOffset", 0)) + int(byte_offset)
    view_end = int(view.get("byteOffset", 0)) + int(view["byteLength"])
    result = []
    for index in range(count):
        position = start + index * step
        if position < 0 or position + packed > view_end or position + packed > len(binary):
            raise ValueError("CC_GLB_ACCESSOR_RANGE")
        raw = struct.unpack_from("<" + code * components, binary, position)
        result.append([_normalized_component(value, component_type, normalized) for value in raw])
    return result


def _read_accessor(document: dict[str, Any], binary: bytes, accessor_index: int) -> list[list[float | int]]:
    try:
        accessor = document["accessors"][accessor_index]
        components = _COMPONENTS[accessor["type"]]
        component_type = int(accessor["componentType"])
        count = int(accessor["count"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"CC_GLB_ACCESSOR: {accessor_index}") from exc
    if "bufferView" in accessor:
        values = _read_elements(document, binary, view_index=int(accessor["bufferView"]),
                                byte_offset=int(accessor.get("byteOffset", 0)),
                                component_type=component_type, components=components, count=count,
                                normalized=bool(accessor.get("normalized", False)))
    else:
        values = [[0.] * components for _ in range(count)]
    sparse = accessor.get("sparse")
    if sparse:
        sparse_count = int(sparse["count"])
        indices_spec = sparse["indices"]
        indices = _read_elements(document, binary, view_index=int(indices_spec["bufferView"]),
                                 byte_offset=int(indices_spec.get("byteOffset", 0)),
                                 component_type=int(indices_spec["componentType"]), components=1,
                                 count=sparse_count)
        values_spec = sparse["values"]
        replacements = _read_elements(document, binary, view_index=int(values_spec["bufferView"]),
                                      byte_offset=int(values_spec.get("byteOffset", 0)),
                                      component_type=component_type, components=components,
                                      count=sparse_count, normalized=bool(accessor.get("normalized", False)))
        previous = -1
        for index_value, replacement in zip(indices, replacements, strict=True):
            index = int(index_value[0])
            if not previous < index < count:
                raise ValueError("CC_GLB_ACCESSOR_SPARSE_INDEX")
            values[index] = replacement
            previous = index
    return values


def _normalize_quaternion(q):
    length = math.sqrt(sum(float(value) ** 2 for value in q))
    if length < 1e-12:
        raise ValueError("CC_GLB_ZERO_QUATERNION")
    return [float(value) / length for value in q]


def _slerp(a, b, alpha):
    qa, qb = _normalize_quaternion(a), _normalize_quaternion(b)
    dot = sum(x * y for x, y in zip(qa, qb, strict=True))
    if dot < 0.:
        qb, dot = [-value for value in qb], -dot
    dot = max(-1., min(1., dot))
    if dot > .9995:
        return _normalize_quaternion([(1 - alpha) * x + alpha * y for x, y in zip(qa, qb, strict=True)])
    angle = math.acos(dot)
    scale = math.sin(angle)
    return [(math.sin((1 - alpha) * angle) * x + math.sin(alpha * angle) * y) / scale
            for x, y in zip(qa, qb, strict=True)]


def _sample_sampler(times, outputs, interpolation: str, time_s: float, path: str):
    if not times:
        raise ValueError("CC_GLB_ANIMATION_KEYS_EMPTY")
    scalar_times = [float(value[0]) for value in times]
    if any(right <= left for left, right in zip(scalar_times, scalar_times[1:])):
        raise ValueError("CC_GLB_ANIMATION_KEYS_ORDER")
    if time_s <= scalar_times[0]:
        index = 1 if interpolation == "CUBICSPLINE" else 0
        return list(outputs[index])
    if time_s >= scalar_times[-1]:
        index = 3 * (len(scalar_times) - 1) + 1 if interpolation == "CUBICSPLINE" else len(scalar_times) - 1
        return list(outputs[index])
    upper = bisect.bisect_right(scalar_times, time_s)
    lower = upper - 1
    duration = scalar_times[upper] - scalar_times[lower]
    alpha = (time_s - scalar_times[lower]) / duration
    if interpolation == "STEP":
        return list(outputs[lower])
    if interpolation == "LINEAR":
        if path == "rotation":
            return _slerp(outputs[lower], outputs[upper], alpha)
        return [(1 - alpha) * float(a) + alpha * float(b)
                for a, b in zip(outputs[lower], outputs[upper], strict=True)]
    if interpolation == "CUBICSPLINE":
        previous, following = outputs[3 * lower + 1], outputs[3 * upper + 1]
        outgoing, incoming = outputs[3 * lower + 2], outputs[3 * upper]
        a2, a3 = alpha * alpha, alpha * alpha * alpha
        result = [((2 * a3 - 3 * a2 + 1) * float(previous[i]) +
                   duration * (a3 - 2 * a2 + alpha) * float(outgoing[i]) +
                   (-2 * a3 + 3 * a2) * float(following[i]) +
                   duration * (a3 - a2) * float(incoming[i]))
                  for i in range(len(previous))]
        return _normalize_quaternion(result) if path == "rotation" else result
    raise ValueError(f"CC_GLB_ANIMATION_INTERPOLATION: {interpolation}")


def read_glb(path: Path):
    data = path.read_bytes()
    try:
        if len(data) < 12:
            raise ValueError(f"CC_GLB_HEADER: {path}")
        magic, version, size = struct.unpack_from("<III", data)
        if magic != 0x46546C67 or version != 2 or size != len(data):
            raise ValueError(f"CC_GLB_HEADER: {path}")
        offset, document, binary = 12, None, b""
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset); offset += 8
            chunk = data[offset:offset+length]; offset += length
            if kind == 0x4E4F534A:
                document = json.loads(chunk)
            elif kind == 0x004E4942:
                binary = chunk
        if document is None:
            raise ValueError("CC_GLB_JSON_MISSING")
        return document, binary
    except (struct.error, json.JSONDecodeError) as exc:
        raise ValueError(f"CC_GLB_HEADER: {path}") from exc


def validate_glb_rest(path: Path, skeleton: dict) -> dict:
    document, binary = read_glb(path)
    nodes = document["nodes"]
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.get("children", [])}
    global_matrices = {}
    def world(index):
        if index not in global_matrices:
            local = _node_matrix(nodes[index])
            global_matrices[index] = matrix_multiply(world(parents[index]), local) if index in parents else local
        return global_matrices[index]
    expected = {b["name"]: bone_matrix(b) for b in skeleton["bones"]}
    actual, diagnostics = {}, []
    for node_index, node in enumerate(nodes):
        if "skin" not in node:
            continue
        skin = document["skins"][node["skin"]]
        if "inverseBindMatrices" not in skin:
            diagnostics.append("CC_GLB_BIND_MISSING")
            continue
        accessor = document["accessors"][skin["inverseBindMatrices"]]
        if accessor["componentType"] != 5126 or accessor["type"] != "MAT4" or accessor["count"] != len(skin["joints"]):
            diagnostics.append("CC_GLB_BIND_FORMAT")
            continue
        view = document["bufferViews"][accessor["bufferView"]]
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        stride = view.get("byteStride", 64)
        for i, joint in enumerate(skin["joints"]):
            values = struct.unpack_from("<16f", binary, start + stride*i)
            inverse_bind = [[values[col*4+row] for col in range(4)] for row in range(4)]
            # glTF skinning ignores the skinned mesh node transform. Our exporter
            # writes geometry in the catalog bind frame (identity bind shape).
            actual[nodes[joint]["name"]] = matrix_inverse(inverse_bind)
    max_position = max_angle = 0.
    for name, expected_matrix in expected.items():
        if name not in actual:
            diagnostics.append(f"CC_GLB_BONE_MISSING: {name}")
            continue
        position, angle = transform_error(expected_matrix, actual[name])
        max_position, max_angle = max(max_position, position), max(max_angle, angle)
        if position > .0001 or angle > .1:
            diagnostics.append(f"CC_GLB_REST: {name}: {position:.7f} m, {angle:.6f} degrees")
    clips = [a.get("name", "") for a in document.get("animations", [])]
    if set(clips) != REQUIRED_CLIPS:
        diagnostics.append(f"CC_GLB_CLIPS: {clips}")
    return {"passed": not diagnostics, "bones_checked": len(expected), "clips": clips,
            "max_position_error_m": max_position, "max_angle_error_deg": max_angle, "diagnostics": diagnostics}


def _node_parents(nodes: list[dict[str, Any]]) -> dict[int, int]:
    parents: dict[int, int] = {}
    for parent, node in enumerate(nodes):
        for child_value in node.get("children", []):
            child = int(child_value)
            if child in parents:
                raise ValueError(f"CC_GLB_NODE_MULTIPLE_PARENTS: {child}")
            parents[child] = parent
    return parents


def _world_matrices(local_matrices, parents):
    result = {}
    active: set[int] = set()

    def world(index):
        if index in result:
            return result[index]
        if index in active:
            raise ValueError("CC_GLB_NODE_CYCLE")
        active.add(index)
        local = local_matrices[index]
        result[index] = matrix_multiply(world(parents[index]), local) if index in parents else local
        active.remove(index)
        return result[index]

    for node_index in range(len(local_matrices)):
        world(node_index)
    return result


def _prepare_animation(document, binary, animation, duration_s, bone_names, diagnostics):
    nodes = document.get("nodes", [])
    cache = {}
    channels = []
    targets = set()
    for channel in animation.get("channels", []):
        target = channel.get("target", {})
        node_index = target.get("node")
        path = target.get("path")
        if not isinstance(node_index, int) or not 0 <= node_index < len(nodes) or path not in {"translation", "rotation", "scale"}:
            diagnostics.append(f"CC_GLB_MOTION_TARGET: {animation.get('name', '')}: {target}")
            continue
        node_name = nodes[node_index].get("name", "")
        if node_name not in bone_names:
            diagnostics.append(f"CC_GLB_MOTION_TARGET_EXTRA: {animation.get('name', '')}: {node_name or node_index}")
            continue
        target_key = (node_index, path)
        if target_key in targets:
            diagnostics.append(f"CC_GLB_MOTION_TARGET_DUPLICATE: {animation.get('name', '')}: {node_name}.{path}")
            continue
        targets.add(target_key)
        try:
            sampler_index = int(channel["sampler"])
            sampler = animation["samplers"][sampler_index]
            if sampler_index not in cache:
                input_accessor = document["accessors"][int(sampler["input"])]
                if input_accessor.get("type") != "SCALAR" or input_accessor.get("componentType") != 5126:
                    raise ValueError("CC_GLB_ANIMATION_INPUT_TYPE")
                times = _read_accessor(document, binary, int(sampler["input"]))
                outputs = _read_accessor(document, binary, int(sampler["output"]))
                if (any(not math.isfinite(float(value)) for item in times for value in item) or
                        any(not math.isfinite(float(value)) for item in outputs for value in item)):
                    raise ValueError("CC_GLB_ANIMATION_NONFINITE")
                interpolation = sampler.get("interpolation", "LINEAR")
                multiplier = 3 if interpolation == "CUBICSPLINE" else 1
                if interpolation not in {"LINEAR", "STEP", "CUBICSPLINE"}:
                    raise ValueError(f"CC_GLB_ANIMATION_INTERPOLATION: {interpolation}")
                if len(outputs) != len(times) * multiplier:
                    raise ValueError("CC_GLB_ANIMATION_OUTPUT_COUNT")
                scalar_times = [float(item[0]) for item in times]
                if any(right <= left for left, right in zip(scalar_times, scalar_times[1:])):
                    raise ValueError("CC_GLB_ANIMATION_KEYS_ORDER")
                declared_min, declared_max = input_accessor.get("min"), input_accessor.get("max")
                if (not isinstance(declared_min, list) or len(declared_min) != 1 or
                        not isinstance(declared_max, list) or len(declared_max) != 1 or
                        not math.isclose(float(declared_min[0]), min(scalar_times), abs_tol=1e-7) or
                        not math.isclose(float(declared_max[0]), max(scalar_times), abs_tol=1e-7)):
                    raise ValueError("CC_GLB_ANIMATION_INPUT_BOUNDS")
                if (not scalar_times or abs(scalar_times[0]) > 1e-6 or
                        abs(scalar_times[-1] - duration_s) > 1e-5):
                    diagnostics.append(
                        f"CC_GLB_MOTION_RANGE: {animation.get('name', '')}: "
                        f"{scalar_times[0] if scalar_times else 'empty'}..{scalar_times[-1] if scalar_times else 'empty'} "
                        f"expected 0..{duration_s}"
                    )
                cache[sampler_index] = (times, outputs, interpolation)
            times, outputs, interpolation = cache[sampler_index]
            expected_components = 4 if path == "rotation" else 3
            if outputs and len(outputs[0]) != expected_components:
                raise ValueError(f"CC_GLB_ANIMATION_OUTPUT_TYPE: {node_name}.{path}")
            channels.append((node_index, path, times, outputs, interpolation))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            diagnostics.append(f"CC_GLB_MOTION_CHANNEL: {animation.get('name', '')}: {exc}")
    return channels


def _sample_node_matrices(nodes, channels, time_s):
    overrides: dict[int, dict[str, list[float]]] = {}
    for node_index, path, times, outputs, interpolation in channels:
        overrides.setdefault(node_index, {})[path] = _sample_sampler(times, outputs, interpolation, time_s, path)
    result = []
    for node_index, node in enumerate(nodes):
        values = overrides.get(node_index)
        if not values:
            result.append(_node_matrix(node))
            continue
        if "matrix" in node:
            raise ValueError(f"CC_GLB_ANIMATED_MATRIX_NODE: {node.get('name', node_index)}")
        animated = {
            "translation": values.get("translation", node.get("translation", [0., 0., 0.])),
            "rotation": values.get("rotation", node.get("rotation", [0., 0., 0., 1.])),
            "scale": values.get("scale", node.get("scale", [1., 1., 1.])),
        }
        result.append(_node_matrix(animated))
    return result


def _expected_world_rotations(nodes, parents, node_names, sample_bones):
    deltas = {bone["name"]: _normalize_quaternion(bone["rotation_xyzw"]) for bone in sample_bones}
    local = []
    for node in nodes:
        rest = _rotation_matrix(_node_matrix(node))
        name = node.get("name", "")
        local.append(matrix_multiply(rest, _quat_matrix(deltas[name])) if name in node_names and name in deltas else rest)
    return _world_matrices(local, parents)


def validate_glb_motion(path: Path, skeleton: dict, motion: dict) -> dict:
    """Compare every 30 FPS evaluated Blender bone sample with its exported GLB clip."""
    diagnostics: list[str] = []
    document, binary = read_glb(path)
    nodes = document.get("nodes", [])
    try:
        parents = _node_parents(nodes)
    except ValueError as exc:
        parents = {}
        diagnostics.append(str(exc))

    skeleton_id = str(skeleton.get("skeleton_id", ""))
    if motion.get("skeleton_id") != skeleton_id:
        diagnostics.append(f"CC_GLB_MOTION_SKELETON: {motion.get('skeleton_id')} != {skeleton_id}")
    if motion.get("sample_source") != "evaluated_blender":
        diagnostics.append(f"CC_GLB_MOTION_SOURCE: {motion.get('sample_source')}")
    if motion.get("fps") != 30:
        diagnostics.append(f"CC_GLB_MOTION_FPS: {motion.get('fps')}")

    bones = skeleton.get("bones", [])
    bone_names = [str(bone.get("name", "")) for bone in bones]
    bone_set = set(bone_names)
    if len(bone_set) != len(bone_names) or list(motion.get("bone_names", [])) != bone_names:
        diagnostics.append(f"CC_GLB_MOTION_BONES: expected {bone_names}, got {motion.get('bone_names', [])}")
    named_nodes: dict[str, list[int]] = {}
    for node_index, node in enumerate(nodes):
        if node.get("name") in bone_set:
            named_nodes.setdefault(node["name"], []).append(node_index)
    for name in bone_names:
        matches = named_nodes.get(name, [])
        if len(matches) != 1:
            diagnostics.append(f"CC_GLB_MOTION_NODE: {name}: count={len(matches)}")
    node_by_name = {name: matches[0] for name, matches in named_nodes.items() if len(matches) == 1}
    referenced_skins = {node.get("skin") for node in nodes if isinstance(node.get("skin"), int)}
    expected_joint_nodes = set(node_by_name.values())
    skin_matches = False
    for skin_index in referenced_skins:
        if not 0 <= skin_index < len(document.get("skins", [])):
            continue
        joints = document["skins"][skin_index].get("joints", [])
        if len(joints) == len(expected_joint_nodes) and set(joints) == expected_joint_nodes:
            skin_matches = True
            break
    if not skin_matches:
        diagnostics.append(f"CC_GLB_MOTION_SKIN: expected joints {sorted(expected_joint_nodes)}")

    animation_list = document.get("animations", [])
    animation_names = [str(animation.get("name", "")) for animation in animation_list]
    motion_clip_list = motion.get("clips", [])
    motion_names = [str(clip.get("name", "")) for clip in motion_clip_list]
    if set(animation_names) != REQUIRED_CLIPS or len(animation_names) != len(REQUIRED_CLIPS):
        diagnostics.append(f"CC_GLB_MOTION_CLIPS: GLB={animation_names}")
    if set(motion_names) != REQUIRED_CLIPS or len(motion_names) != len(REQUIRED_CLIPS):
        diagnostics.append(f"CC_GLB_MOTION_CLIPS: motion={motion_names}")
    animations = {animation.get("name"): animation for animation in animation_list}
    motion_clips = {clip.get("name"): clip for clip in motion_clip_list}

    socket_bones = {branch["bone_names"][0] for branch in skeleton.get("branches", []) if branch.get("bone_names")}
    lengths = {bone["name"]: _distance(bone["head_m"], bone["tail_m"]) for bone in bones}
    max_position = max_head = max_tail = max_angle = max_socket = 0.
    worst_position = worst_angle = worst_socket = None
    samples_checked = bone_samples_checked = clips_checked = 0

    for clip_name in sorted(REQUIRED_CLIPS):
        animation = animations.get(clip_name)
        clip = motion_clips.get(clip_name)
        if animation is None or clip is None:
            continue
        clips_checked += 1
        frames = clip.get("frames")
        fps = clip.get("fps")
        samples = clip.get("samples", [])
        if not isinstance(frames, int) or frames < 0 or fps != 30:
            diagnostics.append(f"CC_GLB_MOTION_RANGE: {clip_name}: frames={frames}, fps={fps}")
            continue
        duration_s = frames / 30.
        if abs(float(clip.get("duration_s", -1.)) - duration_s) > 1e-6:
            diagnostics.append(f"CC_GLB_MOTION_RANGE: {clip_name}: duration={clip.get('duration_s')} expected={duration_s}")
        sample_frames = [sample.get("frame") for sample in samples]
        if sample_frames != list(range(frames + 1)):
            diagnostics.append(f"CC_GLB_MOTION_RANGE: {clip_name}: frames={sample_frames}")
        channels = _prepare_animation(document, binary, animation, duration_s, bone_set, diagnostics)
        for sample in samples:
            frame = sample.get("frame")
            if not isinstance(frame, int) or not 0 <= frame <= frames:
                continue
            samples_checked += 1
            try:
                local = _sample_node_matrices(nodes, channels, frame / 30.)
                actual_world = _world_matrices(local, parents)
            except ValueError as exc:
                diagnostics.append(f"CC_GLB_MOTION_SAMPLE: {clip_name}@{frame}: {exc}")
                continue
            sample_bones = sample.get("bones", [])
            sample_names = [bone.get("name") for bone in sample_bones]
            if set(sample_names) != bone_set or len(sample_names) != len(bone_names):
                diagnostics.append(f"CC_GLB_MOTION_SAMPLE_BONES: {clip_name}@{frame}: {sample_names}")
                continue
            try:
                expected_rotations = _expected_world_rotations(nodes, parents, bone_set, sample_bones)
            except (KeyError, TypeError, ValueError) as exc:
                diagnostics.append(f"CC_GLB_MOTION_ROTATION_DATA: {clip_name}@{frame}: {exc}")
                continue
            for expected_bone in sample_bones:
                name = expected_bone["name"]
                node_index = node_by_name.get(name)
                if node_index is None:
                    continue
                bone_samples_checked += 1
                actual_matrix = actual_world[node_index]
                head_error = _distance([actual_matrix[i][3] for i in range(3)], expected_bone["head_m"])
                actual_tail = _transform_point(actual_matrix, [0., lengths[name], 0.])
                tail_error = _distance(actual_tail, expected_bone["tail_m"])
                _, angle_error = transform_error(_rotation_matrix(actual_matrix), expected_rotations[node_index])
                max_head, max_tail, max_angle = max(max_head, head_error), max(max_tail, tail_error), max(max_angle, angle_error)
                point_error = max(head_error, tail_error)
                if point_error > max_position:
                    max_position = point_error
                    worst_position = {"clip": clip_name, "frame": frame, "bone": name,
                                      "point": "head" if head_error >= tail_error else "tail",
                                      "error_m": point_error}
                if angle_error >= max_angle:
                    worst_angle = {"clip": clip_name, "frame": frame, "bone": name,
                                   "error_deg": angle_error}
                if name in socket_bones and head_error > max_socket:
                    max_socket = head_error
                    worst_socket = {"clip": clip_name, "frame": frame, "bone": name, "error_m": head_error}

    if max_position > .0001:
        diagnostics.append(f"CC_GLB_MOTION_POSITION: {worst_position}")
    if max_angle > .1:
        diagnostics.append(f"CC_GLB_MOTION_ROTATION: {worst_angle}")
    return {
        "passed": not diagnostics,
        "position_tolerance_m": .0001,
        "angle_tolerance_deg": .1,
        "clips": animation_names,
        "clips_checked": clips_checked,
        "samples_checked": samples_checked,
        "bones_checked": len(bone_names),
        "bone_samples_checked": bone_samples_checked,
        "max_position_error_m": max_position,
        "max_head_error_m": max_head,
        "max_tail_error_m": max_tail,
        "max_angle_error_deg": max_angle,
        "max_socket_position_error_m": max_socket,
        "worst_position": worst_position,
        "worst_angle": worst_angle,
        "worst_socket": worst_socket,
        "diagnostics": diagnostics,
    }


def _mat4_accessor_value(values: list[float | int]) -> list[list[float]]:
    if len(values) != 16:
        raise ValueError("CC_GLB_SURFACE_BIND_FORMAT")
    return [[float(values[column * 4 + row]) for column in range(4)] for row in range(4)]


def _prepare_surface_vertices(document: dict[str, Any], binary: bytes, diagnostics: list[str]):
    """Precompute inverse-bind vertex terms for fast animated Y evaluation."""
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    skins = document.get("skins", [])
    prepared: list[tuple[dict[str, int], list[tuple[int, float, float, float, float]]]] = []
    material_slots: set[int] = set()
    max_influences = 0

    def report_once(code: str, detail: str) -> None:
        prefix = code + ":"
        if not any(item == code or item.startswith(prefix) for item in diagnostics):
            diagnostics.append(f"{code}: {detail}")

    for node_index, node in enumerate(nodes):
        if "mesh" not in node or "skin" not in node:
            continue
        try:
            mesh_index, skin_index = int(node["mesh"]), int(node["skin"])
            mesh, skin = meshes[mesh_index], skins[skin_index]
            joints = [int(value) for value in skin["joints"]]
            if not joints or any(not 0 <= joint < len(nodes) for joint in joints):
                raise ValueError("CC_GLB_SURFACE_JOINTS")
            bind_accessor_index = int(skin["inverseBindMatrices"])
            bind_accessor = document["accessors"][bind_accessor_index]
            if (bind_accessor.get("componentType") != 5126 or bind_accessor.get("type") != "MAT4" or
                    int(bind_accessor.get("count", -1)) != len(joints)):
                raise ValueError("CC_GLB_SURFACE_BIND_FORMAT")
            inverse_binds = [_mat4_accessor_value(value)
                             for value in _read_accessor(document, binary, bind_accessor_index)]
            if any(not math.isfinite(value) for matrix in inverse_binds for row in matrix for value in row):
                raise ValueError("CC_GLB_SURFACE_BIND_NONFINITE")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            diagnostics.append(f"CC_GLB_SURFACE_SKIN: node {node_index}: {exc}")
            continue

        for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
            label = f"node {node_index} primitive {primitive_index}"
            attributes = primitive.get("attributes", {})
            try:
                position_index = int(attributes["POSITION"])
                position_accessor = document["accessors"][position_index]
                if position_accessor.get("type") != "VEC3" or position_accessor.get("componentType") != 5126:
                    raise ValueError("CC_GLB_SURFACE_POSITION_FORMAT")
                positions = _read_accessor(document, binary, position_index)
                if not positions:
                    raise ValueError("CC_GLB_SURFACE_POSITION_EMPTY")
                if any(not math.isfinite(float(value)) for position in positions for value in position):
                    raise ValueError("CC_GLB_SURFACE_POSITION_NONFINITE")
                used_indices: list[int]
                if "indices" in primitive:
                    index_accessor_index = int(primitive["indices"])
                    index_accessor = document["accessors"][index_accessor_index]
                    if (index_accessor.get("type") != "SCALAR" or
                            index_accessor.get("componentType") not in {5121, 5123, 5125}):
                        raise ValueError("CC_GLB_SURFACE_INDEX_FORMAT")
                    used_indices = sorted({int(value[0]) for value in _read_accessor(
                        document, binary, index_accessor_index
                    )})
                else:
                    used_indices = list(range(len(positions)))
                if any(not 0 <= index < len(positions) for index in used_indices):
                    raise ValueError("CC_GLB_SURFACE_INDEX_RANGE")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                diagnostics.append(f"CC_GLB_SURFACE_PRIMITIVE: {label}: {exc}")
                continue

            influence_sets = []
            for set_index in (0, 1):
                joints_key, weights_key = f"JOINTS_{set_index}", f"WEIGHTS_{set_index}"
                if joints_key not in attributes and weights_key not in attributes:
                    continue
                if joints_key not in attributes or weights_key not in attributes:
                    diagnostics.append(f"CC_GLB_SURFACE_ATTRIBUTES: {label}: {joints_key}/{weights_key}")
                    influence_sets = []
                    break
                try:
                    joints_index, weights_index = int(attributes[joints_key]), int(attributes[weights_key])
                    joints_accessor, weights_accessor = (
                        document["accessors"][joints_index], document["accessors"][weights_index]
                    )
                    if (joints_accessor.get("type") != "VEC4" or
                            joints_accessor.get("componentType") not in {5121, 5123} or
                            joints_accessor.get("normalized", False)):
                        raise ValueError("CC_GLB_SURFACE_JOINT_FORMAT")
                    if (weights_accessor.get("type") != "VEC4" or
                            weights_accessor.get("componentType") not in {5121, 5123, 5126} or
                            (weights_accessor.get("componentType") != 5126 and
                             not weights_accessor.get("normalized", False))):
                        raise ValueError("CC_GLB_SURFACE_WEIGHT_FORMAT")
                    joint_values = _read_accessor(document, binary, joints_index)
                    weight_values = _read_accessor(document, binary, weights_index)
                    if len(joint_values) != len(positions) or len(weight_values) != len(positions):
                        raise ValueError("CC_GLB_SURFACE_ATTRIBUTE_COUNT")
                    influence_sets.append((joint_values, weight_values))
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    diagnostics.append(f"CC_GLB_SURFACE_ATTRIBUTES: {label}: {exc}")
                    influence_sets = []
                    break
            if not influence_sets:
                report_once("CC_GLB_SURFACE_ATTRIBUTES", f"{label}: missing skin influences")
                continue
            if isinstance(primitive.get("material"), int):
                material_slots.add(int(primitive["material"]))

            for vertex_index in used_indices:
                influences: list[tuple[int, float, float, float, float]] = []
                weight_sum = 0.0
                for joint_values, weight_values in influence_sets:
                    for joint_value, weight_value in zip(joint_values[vertex_index], weight_values[vertex_index], strict=True):
                        weight = float(weight_value)
                        if not math.isfinite(weight) or weight < 0:
                            report_once("CC_GLB_SURFACE_WEIGHTS", f"{label}: invalid vertex weight")
                            continue
                        if weight <= 1e-8:
                            continue
                        joint_slot = int(joint_value)
                        if not 0 <= joint_slot < len(joints):
                            report_once("CC_GLB_SURFACE_JOINT_INDEX", f"{label}: {joint_slot}")
                            continue
                        bind_point = _transform_point(inverse_binds[joint_slot], positions[vertex_index])
                        if any(not math.isfinite(float(value)) for value in bind_point):
                            report_once("CC_GLB_SURFACE_BIND_NONFINITE", label)
                            continue
                        influences.append((joints[joint_slot], weight, *bind_point))
                        weight_sum += weight
                if abs(weight_sum - 1.0) > 1e-4:
                    report_once("CC_GLB_SURFACE_WEIGHTS", f"{label}: sum={weight_sum:.7f}")
                if len(influences) > 4:
                    report_once("CC_GLB_SURFACE_INFLUENCES", f"{label}: {len(influences)} > 4")
                max_influences = max(max_influences, len(influences))
                if influences:
                    prepared.append(({
                        "node": node_index,
                        "mesh": mesh_index,
                        "primitive": primitive_index,
                        "vertex": vertex_index,
                    }, influences))
    if not prepared:
        diagnostics.append("CC_GLB_SURFACE_MISSING: no skinned vertices")
    return prepared, max_influences, len(material_slots)


def validate_glb_surface(path: Path, skeleton: dict, motion: dict) -> dict:
    """Evaluate actual skinned GLB vertices at every authored 30 FPS frame."""
    diagnostics: list[str] = []
    document, binary = read_glb(path)
    nodes = document.get("nodes", [])
    try:
        parents = _node_parents(nodes)
    except ValueError as exc:
        parents = {}
        diagnostics.append(str(exc))

    skeleton_id = str(skeleton.get("skeleton_id", ""))
    if motion.get("skeleton_id") != skeleton_id:
        diagnostics.append(f"CC_GLB_SURFACE_SKELETON: {motion.get('skeleton_id')} != {skeleton_id}")
    if motion.get("fps") != 30:
        diagnostics.append(f"CC_GLB_SURFACE_FPS: {motion.get('fps')}")
    vertices, max_influences, material_count = _prepare_surface_vertices(document, binary, diagnostics)

    animation_list = document.get("animations", [])
    animation_names = [str(animation.get("name", "")) for animation in animation_list]
    clip_list = motion.get("clips", [])
    clip_names = [str(clip.get("name", "")) for clip in clip_list]
    if set(animation_names) != REQUIRED_CLIPS or len(animation_names) != len(REQUIRED_CLIPS):
        diagnostics.append(f"CC_GLB_SURFACE_CLIPS: GLB={animation_names}")
    if set(clip_names) != REQUIRED_CLIPS or len(clip_names) != len(REQUIRED_CLIPS):
        diagnostics.append(f"CC_GLB_SURFACE_CLIPS: motion={clip_names}")
    animations = {animation.get("name"): animation for animation in animation_list}
    clips = {clip.get("name"): clip for clip in clip_list}
    bone_names = {str(bone.get("name", "")) for bone in skeleton.get("bones", [])}

    minimum_y = math.inf
    worst_surface = None
    per_clip_bounds: dict[str, dict[str, Any]] = {}
    clips_checked = frames_checked = vertex_samples_checked = 0
    for clip_name in sorted(REQUIRED_CLIPS):
        animation, clip = animations.get(clip_name), clips.get(clip_name)
        if animation is None or clip is None:
            continue
        frames, fps = clip.get("frames"), clip.get("fps")
        if not isinstance(frames, int) or frames < 0 or fps != 30:
            diagnostics.append(f"CC_GLB_SURFACE_RANGE: {clip_name}: frames={frames}, fps={fps}")
            continue
        clips_checked += 1
        duration_s = frames / 30.0
        channels = _prepare_animation(document, binary, animation, duration_s, bone_names, diagnostics)
        clip_minimum = math.inf
        clip_worst_frame = None
        for frame in range(frames + 1):
            frames_checked += 1
            try:
                local = _sample_node_matrices(nodes, channels, frame / 30.0)
                world = _world_matrices(local, parents)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                diagnostics.append(f"CC_GLB_SURFACE_SAMPLE: {clip_name}@{frame}: {exc}")
                continue
            frame_minimum = math.inf
            for provenance, influences in vertices:
                y = 0.0
                for joint_node, weight, bind_x, bind_y, bind_z in influences:
                    matrix = world[joint_node]
                    y += weight * (
                        matrix[1][0] * bind_x + matrix[1][1] * bind_y +
                        matrix[1][2] * bind_z + matrix[1][3]
                    )
                if not math.isfinite(y):
                    if not any(item.startswith("CC_GLB_SURFACE_COMPUTED_NONFINITE") for item in diagnostics):
                        diagnostics.append(
                            f"CC_GLB_SURFACE_COMPUTED_NONFINITE: {clip_name}@{frame}: {provenance}"
                        )
                    continue
                frame_minimum = min(frame_minimum, y)
                if y < minimum_y:
                    minimum_y = y
                    worst_surface = {
                        "clip": clip_name,
                        "frame": frame,
                        "min_y_m": y,
                        **provenance,
                    }
            vertex_samples_checked += len(vertices)
            if frame_minimum < clip_minimum:
                clip_minimum, clip_worst_frame = frame_minimum, frame
        if clip_minimum < math.inf:
            per_clip_bounds[clip_name] = {
                "min_y_m": clip_minimum,
                "max_below_ground_m": max(0.0, -clip_minimum),
                "worst_frame": clip_worst_frame,
                "frames_checked": frames + 1,
            }

    if minimum_y == math.inf:
        minimum_y = 0.0
    penetration = max(0.0, -minimum_y)
    if penetration > .005 + 1e-9:
        diagnostics.append(f"CC_GLB_SURFACE_GROUND: {worst_surface}: penetration={penetration:.7f}m")
    return {
        "passed": not diagnostics,
        "ground_tolerance_m": .005,
        "clips": animation_names,
        "clips_checked": clips_checked,
        "frames_checked": frames_checked,
        "vertices_checked": len(vertices),
        "vertex_samples_checked": vertex_samples_checked,
        "max_influences": max_influences,
        "material_count": material_count,
        "min_surface_y_m": minimum_y,
        "max_below_ground_m": penetration,
        "worst_surface": worst_surface,
        "per_clip_bounds": per_clip_bounds,
        "diagnostics": diagnostics,
    }


def validate_glb_export(path: Path, skeleton: dict, motion: dict | None = None) -> dict:
    """Return the rest report and, when supplied, the evaluated motion report."""
    rest = validate_glb_rest(path, skeleton)
    if motion is None:
        return {"passed": rest["passed"], "rest": rest}
    animation = validate_glb_motion(path, skeleton, motion)
    return {"passed": rest["passed"] and animation["passed"], "rest": rest, "motion": animation}


def _report_once(diagnostics: list[str], code: str, detail: str) -> None:
    prefix = code + ":"
    if not any(item == code or item.startswith(prefix) for item in diagnostics):
        diagnostics.append(f"{code}: {detail}")


def _reaches_node(root: int, joint: int, parents: dict[int, int]) -> bool:
    seen: set[int] = set()
    index: int | None = joint
    while index is not None and index not in seen:
        if index == root:
            return True
        seen.add(index)
        index = parents.get(index)
    return False


def _glb_skin_root_ok(skin: dict[str, Any], nodes: list[dict[str, Any]],
                      parents: dict[int, int], diagnostics: list[str], label: str) -> None:
    joints = skin.get("joints")
    if not isinstance(joints, list) or not joints:
        _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: missing joints")
        return
    try:
        joint_indices = [int(value) for value in joints]
    except (TypeError, ValueError):
        _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: invalid joints")
        return
    if any(not 0 <= joint < len(nodes) for joint in joint_indices):
        _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: joint out of range")
        return
    ancestor_sets: list[set[int]] = []
    for joint in joint_indices:
        seen: list[int] = []
        index: int | None = joint
        while index is not None and index not in seen:
            seen.append(index)
            index = parents.get(index)
        ancestor_sets.append(set(seen))
    common = set.intersection(*ancestor_sets) if ancestor_sets else set()
    if "skeleton" in skin:
        try:
            root = int(skin["skeleton"])
        except (TypeError, ValueError):
            _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: {skin.get('skeleton')}")
            return
        if not 0 <= root < len(nodes) or not all(_reaches_node(root, joint, parents) for joint in joint_indices):
            _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: {root}")
        return
    if not common:
        _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: missing")


def validate_glb_skin_contract(path: Path, *, max_influences: int = 4) -> dict:
    """Always-on GLB skin gate: JOINTS/WEIGHTS pairing, weights, joint indices, skin root."""
    diagnostics: list[str] = []
    try:
        document, binary = read_glb(path)
    except (ValueError, struct.error) as exc:
        return {"passed": False, "diagnostics": [str(exc)], "max_influences": 0, "vertices_checked": 0}
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    skins = document.get("skins", [])
    try:
        parents = _node_parents(nodes)
    except ValueError as exc:
        parents = {}
        diagnostics.append(str(exc))

    observed_influences = 0
    vertices_checked = 0
    skinned_meshes = 0
    for node_index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        label = f"node {node_index}"
        if "skin" not in node:
            _report_once(diagnostics, "CC_GLB_SKIN_ROOT", f"{label}: mesh without skin")
            continue
        try:
            mesh_index, skin_index = int(node["mesh"]), int(node["skin"])
            mesh, skin = meshes[mesh_index], skins[skin_index]
            joints = [int(value) for value in skin["joints"]]
            if not joints or any(not 0 <= joint < len(nodes) for joint in joints):
                raise ValueError("CC_GLB_SURFACE_JOINTS")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            diagnostics.append(f"CC_GLB_SURFACE_SKIN: {label}: {exc}")
            continue
        skinned_meshes += 1
        _glb_skin_root_ok(skin, nodes, parents, diagnostics, label)

        for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
            prim_label = f"{label} primitive {primitive_index}"
            attributes = primitive.get("attributes", {})
            try:
                position_index = int(attributes["POSITION"])
                positions = _read_accessor(document, binary, position_index)
                if "indices" in primitive:
                    used_indices = sorted({int(value[0]) for value in _read_accessor(
                        document, binary, int(primitive["indices"])
                    )})
                else:
                    used_indices = list(range(len(positions)))
                if any(not 0 <= index < len(positions) for index in used_indices):
                    raise ValueError("CC_GLB_SURFACE_INDEX_RANGE")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                diagnostics.append(f"CC_GLB_SURFACE_PRIMITIVE: {prim_label}: {exc}")
                continue

            influence_sets = []
            for set_index in (0, 1):
                joints_key, weights_key = f"JOINTS_{set_index}", f"WEIGHTS_{set_index}"
                if joints_key not in attributes and weights_key not in attributes:
                    continue
                if joints_key not in attributes or weights_key not in attributes:
                    diagnostics.append(f"CC_GLB_SURFACE_ATTRIBUTES: {prim_label}: {joints_key}/{weights_key}")
                    influence_sets = []
                    break
                try:
                    joints_index, weights_index = int(attributes[joints_key]), int(attributes[weights_key])
                    joints_accessor, weights_accessor = (
                        document["accessors"][joints_index], document["accessors"][weights_index]
                    )
                    if (joints_accessor.get("type") != "VEC4" or
                            joints_accessor.get("componentType") not in {5121, 5123} or
                            joints_accessor.get("normalized", False)):
                        raise ValueError("CC_GLB_SURFACE_JOINT_FORMAT")
                    if (weights_accessor.get("type") != "VEC4" or
                            weights_accessor.get("componentType") not in {5121, 5123, 5126} or
                            (weights_accessor.get("componentType") != 5126 and
                             not weights_accessor.get("normalized", False))):
                        raise ValueError("CC_GLB_SURFACE_WEIGHT_FORMAT")
                    joint_values = _read_accessor(document, binary, joints_index)
                    weight_values = _read_accessor(document, binary, weights_index)
                    if len(joint_values) != len(positions) or len(weight_values) != len(positions):
                        raise ValueError("CC_GLB_SURFACE_ATTRIBUTE_COUNT")
                    influence_sets.append((joint_values, weight_values))
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    diagnostics.append(f"CC_GLB_SURFACE_ATTRIBUTES: {prim_label}: {exc}")
                    influence_sets = []
                    break
            if not influence_sets:
                _report_once(diagnostics, "CC_GLB_SURFACE_ATTRIBUTES", f"{prim_label}: missing skin influences")
                continue

            for vertex_index in used_indices:
                vertices_checked += 1
                weight_sum = 0.0
                influences = 0
                for joint_values, weight_values in influence_sets:
                    for joint_value, weight_value in zip(
                        joint_values[vertex_index], weight_values[vertex_index], strict=True
                    ):
                        weight = float(weight_value)
                        joint_slot = int(joint_value)
                        if not 0 <= joint_slot < len(joints):
                            _report_once(diagnostics, "CC_GLB_SURFACE_JOINT_INDEX",
                                         f"{prim_label}: {joint_slot}")
                            continue
                        if not math.isfinite(weight) or weight < 0:
                            _report_once(diagnostics, "CC_GLB_SURFACE_WEIGHTS",
                                         f"{prim_label}: invalid vertex weight")
                            continue
                        if weight <= 1e-8:
                            continue
                        influences += 1
                        weight_sum += weight
                if influences == 0:
                    _report_once(diagnostics, "CC_GLB_SURFACE_WEIGHTS", f"{prim_label}: unweighted vertex")
                if abs(weight_sum - 1.0) > 1e-4:
                    _report_once(diagnostics, "CC_GLB_SURFACE_WEIGHTS", f"{prim_label}: sum={weight_sum:.7f}")
                if influences > max_influences:
                    _report_once(diagnostics, "CC_GLB_SURFACE_INFLUENCES",
                                 f"{prim_label}: {influences} > {max_influences}")
                observed_influences = max(observed_influences, influences)
    if skinned_meshes == 0:
        _report_once(diagnostics, "CC_GLB_SKIN_ROOT", "no skinned mesh")
    return {
        "passed": not diagnostics,
        "max_influences": observed_influences,
        "vertices_checked": vertices_checked,
        "diagnostics": diagnostics,
    }


_FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


def _read_fbx_prop(data: bytes, offset: int) -> tuple[Any, int]:
    try:
        return _read_fbx_prop_raw(data, offset)
    except (struct.error, zlib.error, IndexError):
        raise ValueError("CC_FBX_HEADER") from None


def _read_fbx_prop_raw(data: bytes, offset: int) -> tuple[Any, int]:
    if offset >= len(data):
        raise ValueError("CC_FBX_HEADER")
    code = chr(data[offset])
    offset += 1
    if code == "Y":
        return struct.unpack_from("<h", data, offset)[0], offset + 2
    if code == "C":
        return data[offset] != 0, offset + 1
    if code == "I":
        return struct.unpack_from("<i", data, offset)[0], offset + 4
    if code == "F":
        return struct.unpack_from("<f", data, offset)[0], offset + 4
    if code == "D":
        return struct.unpack_from("<d", data, offset)[0], offset + 8
    if code == "L":
        return struct.unpack_from("<q", data, offset)[0], offset + 8
    if code in "RS":
        length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        raw = data[offset:offset + length]
        offset += length
        if code == "S":
            return raw.decode("utf-8", "replace"), offset
        return raw, offset
    if code in "cdilfb":
        count, encoding, length = struct.unpack_from("<III", data, offset)
        offset += 12
        blob = data[offset:offset + length]
        offset += length
        if encoding == 1:
            blob = zlib.decompress(blob)
        fmt = {"c": "?", "b": "b", "i": "i", "l": "q", "f": "f", "d": "d"}[code]
        size = struct.calcsize("<" + fmt)
        if len(blob) < count * size:
            raise ValueError("CC_FBX_HEADER")
        return list(struct.unpack_from("<" + fmt * count, blob)), offset
    raise ValueError(f"CC_FBX_PROP: {code}")


def _read_fbx_node(data: bytes, offset: int, large: bool) -> tuple[dict[str, Any] | None, int]:
    header = 25 if large else 13
    if offset + header > len(data):
        raise ValueError("CC_FBX_HEADER")
    if large:
        end, nprops, proplen = struct.unpack_from("<QQQ", data, offset)
        namelen = data[offset + 24]
        offset += 25
    else:
        end, nprops, proplen = struct.unpack_from("<III", data, offset)
        namelen = data[offset + 12]
        offset += 13
    if end == 0 and nprops == 0 and proplen == 0 and namelen == 0:
        return None, offset
    name = data[offset:offset + namelen].decode("ascii", "replace")
    offset += namelen
    props_end = offset + proplen
    props = []
    for _ in range(nprops):
        value, offset = _read_fbx_prop(data, offset)
        props.append(value)
    offset = props_end
    children = []
    while offset < end:
        child, offset = _read_fbx_node(data, offset, large)
        if child is None:
            break
        children.append(child)
    return {"name": name, "props": props, "children": children}, end


def read_fbx(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if not data.startswith(_FBX_MAGIC):
        raise ValueError(f"CC_FBX_HEADER: {path}")
    try:
        version = struct.unpack_from("<I", data, 23)[0]
        offset = 27
        large = version >= 7500
        nodes = []
        while offset < len(data):
            node, offset = _read_fbx_node(data, offset, large)
            if node is None:
                break
            nodes.append(node)
        return nodes
    except (struct.error, zlib.error, IndexError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("CC_FBX"):
            raise
        raise ValueError(f"CC_FBX_HEADER: {path}") from exc


def _fbx_child(node: dict[str, Any], name: str) -> dict[str, Any] | None:
    for child in node.get("children") or []:
        if child["name"] == name:
            return child
    return None


def _fbx_id(node: dict[str, Any]) -> int | None:
    props = node.get("props") or []
    if props and isinstance(props[0], int):
        return int(props[0])
    return None


def _fbx_subtype(node: dict[str, Any]) -> str:
    props = node.get("props") or []
    if len(props) > 2 and isinstance(props[2], str):
        return props[2]
    return ""


def validate_fbx_skin_contract(path: Path, *, max_influences: int = 4) -> dict:
    """Always-on FBX skin gate: cluster Indexes/Weights, weights, joint indices, skin root."""
    diagnostics: list[str] = []
    try:
        roots = read_fbx(path)
    except (ValueError, struct.error, zlib.error) as exc:
        detail = str(exc) if str(exc).startswith("CC_FBX") else f"CC_FBX_HEADER: {path}"
        return {"passed": False, "diagnostics": [detail], "max_influences": 0, "vertices_checked": 0}

    objects = next((node for node in roots if node["name"] == "Objects"), None)
    connections = next((node for node in roots if node["name"] == "Connections"), None)
    if objects is None:
        return {"passed": False, "diagnostics": ["CC_FBX_SKIN_ROOT: missing Objects"],
                "max_influences": 0, "vertices_checked": 0}

    child_of: dict[int, list[int]] = {}
    parent_of: dict[int, list[int]] = {}
    for node in (connections.get("children") if connections else []) or []:
        if node["name"] != "C":
            continue
        props = node.get("props") or []
        if len(props) < 3 or not isinstance(props[1], int) or not isinstance(props[2], int):
            continue
        child, parent = int(props[1]), int(props[2])
        child_of.setdefault(parent, []).append(child)
        parent_of.setdefault(child, []).append(parent)

    def linked(a: int, b: int) -> bool:
        return b in child_of.get(a, []) or a in child_of.get(b, [])

    geometries = [node for node in objects.get("children") or [] if node["name"] == "Geometry"]
    skins = [node for node in objects.get("children") or []
             if node["name"] == "Deformer" and _fbx_subtype(node) == "Skin"]
    clusters = [node for node in objects.get("children") or []
                if node["name"] == "Deformer" and _fbx_subtype(node) == "Cluster"]
    limbs = [node for node in objects.get("children") or []
             if node["name"] == "Model" and _fbx_subtype(node) == "LimbNode"]
    limb_ids = {identity for node in limbs if (identity := _fbx_id(node)) is not None}

    observed_influences = 0
    vertices_checked = 0
    if not geometries:
        _report_once(diagnostics, "CC_FBX_SKIN_ROOT", "missing Geometry")
        return {"passed": False, "diagnostics": diagnostics, "max_influences": 0, "vertices_checked": 0}

    for geometry in geometries:
        geo_id = _fbx_id(geometry)
        vertices_node = _fbx_child(geometry, "Vertices")
        vertex_values = vertices_node["props"][0] if vertices_node and vertices_node.get("props") else []
        vertex_count = (len(vertex_values) // 3) if isinstance(vertex_values, list) else 0
        bound_skins = [skin for skin in skins if geo_id is not None and linked(int(_fbx_id(skin) or -1), geo_id)]
        if not bound_skins:
            bound_skins = skins if len(geometries) == 1 else []
        if not bound_skins:
            _report_once(diagnostics, "CC_FBX_SKIN_ROOT", f"geometry {geo_id}: missing Skin")
            continue
        per_vertex: list[list[float]] = [[] for _ in range(vertex_count)] if vertex_count else []
        bound_limbs = 0
        for skin in bound_skins:
            skin_id = _fbx_id(skin)
            bound_clusters = [cluster for cluster in clusters
                              if skin_id is not None and linked(int(_fbx_id(cluster) or -1), skin_id)]
            if not bound_clusters:
                _report_once(diagnostics, "CC_FBX_SKIN_ROOT", f"skin {skin_id}: missing Cluster")
                continue
            for cluster in bound_clusters:
                cluster_id = _fbx_id(cluster)
                if cluster_id is not None and limb_ids.intersection(
                    child_of.get(cluster_id, []) + parent_of.get(cluster_id, [])
                ):
                    bound_limbs += 1
                indexes_node, weights_node = _fbx_child(cluster, "Indexes"), _fbx_child(cluster, "Weights")
                has_indexes = indexes_node is not None and indexes_node.get("props")
                has_weights = weights_node is not None and weights_node.get("props")
                if bool(has_indexes) != bool(has_weights):
                    _report_once(diagnostics, "CC_FBX_SKIN_ATTRIBUTES",
                                 f"cluster {cluster_id}: Indexes/Weights")
                    continue
                indexes = indexes_node["props"][0] if has_indexes else []
                weights = weights_node["props"][0] if has_weights else []
                if not isinstance(indexes, list) or not isinstance(weights, list):
                    _report_once(diagnostics, "CC_FBX_SKIN_ATTRIBUTES",
                                 f"cluster {cluster_id}: Indexes/Weights")
                    continue
                if len(indexes) != len(weights):
                    _report_once(diagnostics, "CC_FBX_SKIN_ATTRIBUTES",
                                 f"cluster {cluster_id}: {len(indexes)} indexes / {len(weights)} weights")
                    continue
                if vertex_count == 0 and indexes:
                    vertex_count = max(int(index) for index in indexes) + 1
                    per_vertex = [[] for _ in range(vertex_count)]
                for index_value, weight_value in zip(indexes, weights, strict=True):
                    index = int(index_value)
                    weight = float(weight_value)
                    if not 0 <= index < vertex_count:
                        _report_once(diagnostics, "CC_FBX_SKIN_JOINT_INDEX",
                                     f"cluster {cluster_id}: vertex {index}")
                        continue
                    if not math.isfinite(weight) or weight < 0:
                        _report_once(diagnostics, "CC_FBX_SKIN_WEIGHTS",
                                     f"cluster {cluster_id}: invalid weight")
                        continue
                    if weight <= 1e-8:
                        continue
                    per_vertex[index].append(weight)
        if bound_limbs == 0:
            _report_once(diagnostics, "CC_FBX_SKIN_ROOT", f"geometry {geo_id}: missing LimbNode")
        if not per_vertex:
            _report_once(diagnostics, "CC_FBX_SKIN_ROOT", f"geometry {geo_id}: no weighted vertices")
            continue
        for weights in per_vertex:
            vertices_checked += 1
            influences = len(weights)
            weight_sum = sum(weights)
            if influences == 0:
                _report_once(diagnostics, "CC_FBX_SKIN_WEIGHTS", f"geometry {geo_id}: unweighted vertex")
            if abs(weight_sum - 1.0) > 1e-4:
                _report_once(diagnostics, "CC_FBX_SKIN_WEIGHTS",
                             f"geometry {geo_id}: sum={weight_sum:.7f}")
            if influences > max_influences:
                _report_once(diagnostics, "CC_FBX_SKIN_INFLUENCES",
                             f"geometry {geo_id}: {influences} > {max_influences}")
            observed_influences = max(observed_influences, influences)
    return {
        "passed": not diagnostics,
        "max_influences": observed_influences,
        "vertices_checked": vertices_checked,
        "diagnostics": diagnostics,
    }


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validator_log_path(log_dir: Path, glb: Path, root: Path | None) -> Path:
    if root is not None:
        try:
            rel = glb.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f"{glb.parent.name}/{glb.name}"
    else:
        rel = f"{glb.parent.name}/{glb.name}"
    return log_dir / (rel.replace("/", "__") + ".json")


def run_gltf_validator(tool: str, path: Path, *, log_dir: Path | None = None,
                       root: Path | None = None) -> dict:
    """Run the Khronos native CLI. Errors fail; never .cmd/.bat, never a GLB outside root."""
    raw = Path(tool)
    if ".." in raw.parts:
        return {"passed": False, "diagnostics": ["CC_GLTF_VALIDATOR: path escapes"]}
    suffix = raw.suffix.lower()
    if suffix in {".cmd", ".bat", ".com"}:
        return {"passed": False, "diagnostics": [f"CC_GLTF_VALIDATOR: refused {suffix} executable"]}
    if os.name == "nt" and suffix != ".exe":
        return {"passed": False, "diagnostics": ["CC_GLTF_VALIDATOR: validator must be a .exe"]}
    if os.name != "nt" and suffix:
        return {"passed": False, "diagnostics": ["CC_GLTF_VALIDATOR: validator must have no extension"]}
    try:
        resolved_tool = raw.resolve()
        glb = path.resolve()
    except OSError as exc:
        return {"passed": False, "diagnostics": [f"CC_GLTF_VALIDATOR: {exc}"]}
    if root is not None and not _path_inside(glb, root):
        return {"passed": False, "diagnostics": [f"CC_GLTF_VALIDATOR: {path} leaves the library"]}
    try:
        proc = subprocess.run(
            [str(resolved_tool), "--stdout", "--no-write-timestamp", str(glb)],
            capture_output=True, text=True, timeout=120, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "diagnostics": [f"CC_GLTF_VALIDATOR: {path.name}: {exc}"]}
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "no JSON report").strip()
        return {"passed": False, "diagnostics": [f"CC_GLTF_VALIDATOR: {path.name}: {detail[:500]}"]}
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _validator_log_path(log_dir, glb, root).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    issues = report.get("issues") or {}
    diagnostics = []
    for message in issues.get("messages") or []:
        if message.get("severity") != 0:
            continue
        code = message.get("code") or "ERROR"
        text = message.get("message") or ""
        diagnostics.append(f"CC_GLTF_VALIDATOR: {path.name}: {code} {text}".rstrip())
    if not diagnostics and int(issues.get("numErrors") or 0) > 0:
        diagnostics.append(f"CC_GLTF_VALIDATOR: {path.name}: {issues['numErrors']} error(s)")
    return {"passed": not diagnostics, "diagnostics": diagnostics, "report": report}


def _attach(asset_id: str, diagnostics: list[str]) -> list[str]:
    attached = []
    for item in diagnostics:
        code, _, rest = item.partition(":")
        attached.append(f"{code}: {asset_id}:{rest}" if rest else f"{code}: {asset_id}")
    return attached


def _part_max_influences(part: dict[str, Any]) -> int:
    if part.get("category") == "connector":
        spec = part.get("connector_interface") or {}
        try:
            return int(spec.get("max_influences") or 2)
        except (TypeError, ValueError):
            return 2
    return 4


def export_qa_problems(catalog: dict[str, Any], out: Path, *, validator: str | None = None,
                       log_dir: Path | None = None, include_base: bool = True,
                       include_assembled: bool = False) -> list[str]:
    """Fail-closed skin contract for built FBX/GLB. Validator Errors fail when the pinned tool is present."""
    problems: list[str] = []

    def check_file(asset_id: str, relative: str | None, *, kind: str, max_influences: int) -> None:
        if not isinstance(relative, str) or not relative:
            return
        path = out / relative
        if ".." in Path(relative).parts or not _path_inside(path, out):
            code = "CC_FBX_SKIN_ROOT" if kind == "fbx" else "CC_GLB_SKIN_ROOT"
            problems.append(f"{code}: {asset_id}: {relative} leaves the library")
            return
        if not path.is_file():
            code = "CC_FBX_SKIN_ROOT" if kind == "fbx" else "CC_GLB_SKIN_ROOT"
            problems.append(f"{code}: {asset_id}: missing {relative}")
            return
        if kind == "fbx":
            report = validate_fbx_skin_contract(path, max_influences=max_influences)
            problems.extend(_attach(asset_id, report["diagnostics"]))
            return
        report = validate_glb_skin_contract(path, max_influences=max_influences)
        problems.extend(_attach(asset_id, report["diagnostics"]))
        if validator:
            validated = run_gltf_validator(validator, path, log_dir=log_dir, root=out)
            problems.extend(_attach(asset_id, validated["diagnostics"]))

    for skeleton in catalog.get("skeletons") or []:
        asset_id = str(skeleton.get("skeleton_id", "skeleton"))
        asset = skeleton.get("asset")
        if not isinstance(asset, dict):
            continue
        if include_base:
            check_file(asset_id, asset.get("fbx"), kind="fbx", max_influences=4)
            check_file(asset_id, asset.get("glb"), kind="glb", max_influences=4)
        if include_assembled:
            check_file(asset_id, asset.get("assembled_glb"), kind="glb", max_influences=4)
    if include_base:
        for part in catalog.get("parts") or []:
            asset = part.get("asset")
            if not isinstance(asset, dict):
                continue
            max_influences = _part_max_influences(part)
            check_file(str(part.get("part_id", "part")), asset.get("fbx"), kind="fbx",
                       max_influences=max_influences)
            check_file(str(part.get("part_id", "part")), asset.get("glb"), kind="glb",
                       max_influences=max_influences)
    return problems
