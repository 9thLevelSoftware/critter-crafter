from __future__ import annotations

import copy
import json
import math
import struct
from pathlib import Path

import pytest

from critter_crafter.library.export_validation import (
    matrix_inverse,
    matrix_multiply,
    transform_error,
    validate_glb_motion,
    validate_glb_surface,
)


def test_rest_comparison_checks_rotation_as_well_as_translation():
    identity = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
    turned = [[0., -1., 0., .00005], [1., 0., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
    position, angle = transform_error(identity, turned)
    assert abs(position - .00005) < 1e-10
    assert abs(angle - 90.) < 1e-8
    product = matrix_multiply(turned, matrix_inverse(turned))
    assert all(abs(product[i][j] - identity[i][j]) < 1e-9 for i in range(4) for j in range(4))


CLIPS = ("idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death")


def _qz(degrees: float) -> list[float]:
    half = math.radians(degrees) / 2
    return [0.0, 0.0, math.sin(half), math.cos(half)]


def _qx(degrees: float) -> list[float]:
    half = math.radians(degrees) / 2
    return [math.sin(half), 0.0, 0.0, math.cos(half)]


def _rotate(q: list[float], v: list[float]) -> list[float]:
    x, y, z, w = q
    uv = [y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]]
    uuv = [y * uv[2] - z * uv[1], z * uv[0] - x * uv[2], x * uv[1] - y * uv[0]]
    return [v[i] + 2 * (w * uv[i] + uuv[i]) for i in range(3)]


def _qmul(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


class _GlbBuilder:
    def __init__(self) -> None:
        self.binary = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def accessor(self, values: list[list[float]], kind: str, *, stride_padding: int = 0,
                 component_type: int = 5126, normalized: bool = False) -> int:
        components = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        code, size = {5121: ("B", 1), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}[component_type]
        while len(self.binary) % 4:
            self.binary.append(0)
        view_start = len(self.binary)
        accessor_offset = 4 if stride_padding else 0
        if accessor_offset:
            self.binary.extend(b"pad!")
        stride = components * size + stride_padding
        for value in values:
            self.binary.extend(struct.pack("<" + code * components, *value))
            self.binary.extend(b"\0" * stride_padding)
        view = {"buffer": 0, "byteOffset": view_start,
                "byteLength": len(values) * stride + accessor_offset}
        if stride_padding:
            view["byteStride"] = stride
        self.views.append(view)
        accessor = {"bufferView": len(self.views) - 1, "byteOffset": accessor_offset,
                    "componentType": component_type, "count": len(values), "type": kind}
        if normalized:
            accessor["normalized"] = True
        if kind == "SCALAR":
            accessor["min"] = [min(v[0] for v in values)]
            accessor["max"] = [max(v[0] for v in values)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def write(self, path: Path, *, clip_names: tuple[str, ...] = CLIPS, animate_extra: bool = False,
              omit_child_joint: bool = False, surface_y: float | None = None,
              root_end_y: float = 0.0, surface_weight: float = 1.0,
              root_end_rotation_deg: float = 90.0, bind_nonfinite: bool = False) -> None:
        times = self.accessor([[0.0], [2 / 30]], "SCALAR", stride_padding=4)
        root_t = self.accessor([[0.0, 0.0, 0.0], [.2, root_end_y, 0.0]], "VEC3")
        root_r = self.accessor([_qz(0), _qz(root_end_rotation_deg)], "VEC4")
        child_r = self.accessor([_qx(0), _qx(90)], "VEC4")
        extra_t = self.accessor([[0.0, 0.0, 0.0], [0.0, .1, 0.0]], "VEC3") if animate_extra else None
        identity = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
        if bind_nonfinite:
            identity[0] = float("nan")
        child_inverse = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., -1., 0., 1.]
        inverse_binds = self.accessor([identity] if omit_child_joint else [identity, child_inverse], "MAT4")
        animations = []
        for name in clip_names:
            samplers = [
                {"input": times, "output": root_t, "interpolation": "LINEAR"},
                {"input": times, "output": root_r, "interpolation": "LINEAR"},
                {"input": times, "output": child_r, "interpolation": "LINEAR"},
            ]
            channels = [
                {"sampler": 0, "target": {"node": 0, "path": "translation"}},
                {"sampler": 1, "target": {"node": 0, "path": "rotation"}},
                {"sampler": 2, "target": {"node": 1, "path": "rotation"}},
            ]
            if animate_extra:
                samplers.append({"input": times, "output": extra_t, "interpolation": "LINEAR"})
                channels.append({"sampler": 3, "target": {"node": 2, "path": "translation"}})
            animations.append({"name": name, "samplers": samplers, "channels": channels})
        meshes = []
        mesh_index = None
        if surface_y is not None:
            positions = self.accessor([
                [-.1, surface_y, 0.0], [.1, surface_y, 0.0], [0.0, surface_y, .1]
            ], "VEC3")
            joints = self.accessor([[0, 0, 0, 0]] * 3, "VEC4", component_type=5121)
            weights = self.accessor([[surface_weight, 0.0, 0.0, 0.0]] * 3, "VEC4")
            mesh_index = 0
            meshes = [{"primitives": [{"attributes": {
                "POSITION": positions, "JOINTS_0": joints, "WEIGHTS_0": weights,
            }}]}]
        display_node = {"name": "display_mesh", "skin": 0}
        if mesh_index is not None:
            display_node["mesh"] = mesh_index
        document = {
            "asset": {"version": "2.0"}, "scene": 0,
            "scenes": [{"nodes": [0, 2]}],
            "nodes": [{"name": "root", "children": [1]},
                      {"name": "child", "translation": [0.0, 1.0, 0.0]},
                      display_node],
            "skins": [{"joints": [0] if omit_child_joint else [0, 1], "inverseBindMatrices": inverse_binds}],
            "animations": animations, "bufferViews": self.views, "accessors": self.accessors,
            "buffers": [{"byteLength": len(self.binary)}],
        }
        if meshes:
            document["meshes"] = meshes
        json_chunk = json.dumps(document, separators=(",", ":")).encode()
        json_chunk += b" " * (-len(json_chunk) % 4)
        binary_chunk = bytes(self.binary) + b"\0" * (-len(self.binary) % 4)
        total = 12 + 8 + len(json_chunk) + 8 + len(binary_chunk)
        path.write_bytes(struct.pack("<III", 0x46546C67, 2, total) +
                         struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk +
                         struct.pack("<II", len(binary_chunk), 0x004E4942) + binary_chunk)


def _fixture_motion() -> tuple[dict, dict]:
    skeleton = {
        "skeleton_id": "fixture", "bones": [
            {"name": "root", "parent": "", "head_m": [0, 0, 0], "tail_m": [0, 1, 0], "up_m": [0, 0, 1]},
            {"name": "child", "parent": "root", "head_m": [0, 1, 0], "tail_m": [0, 2, 0], "up_m": [0, 0, 1]},
        ],
        "branches": [{"branch_id": "arm", "bone_names": ["child"]}],
    }
    clips = []
    for clip_name in CLIPS:
        samples = []
        for frame in range(3):
            alpha = frame / 2
            root_q = _qz(90 * alpha)
            child_q = _qx(90 * alpha)
            root_head = [.2 * alpha, 0.0, 0.0]
            root_tail = [root_head[i] + _rotate(root_q, [0, 1, 0])[i] for i in range(3)]
            child_world_q = _qmul(root_q, child_q)
            child_tail = [root_tail[i] + _rotate(child_world_q, [0, 1, 0])[i] for i in range(3)]
            samples.append({"frame": frame, "root_position_m": root_head, "bones": [
                {"name": "root", "head_m": root_head, "tail_m": root_tail, "rotation_xyzw": root_q},
                {"name": "child", "head_m": root_tail, "tail_m": child_tail, "rotation_xyzw": child_q},
            ]})
        clips.append({"name": clip_name, "frames": 2, "fps": 30, "duration_s": 2 / 30, "samples": samples})
    motion = {"schema_version": "motion-samples-1", "sample_source": "evaluated_blender",
              "skeleton_id": "fixture", "fps": 30, "bone_names": ["root", "child"], "clips": clips}
    return skeleton, motion


def test_glb_motion_sampling_matches_world_bones_and_local_deltas(tmp_path: Path) -> None:
    path = tmp_path / "animated.glb"
    _GlbBuilder().write(path)
    skeleton, motion = _fixture_motion()

    report = validate_glb_motion(path, skeleton, motion)

    assert report["passed"], report["diagnostics"]
    assert report["clips_checked"] == 8
    assert report["samples_checked"] == 24
    assert report["bone_samples_checked"] == 48
    assert report["max_position_error_m"] < 1e-6
    assert report["max_angle_error_deg"] < 1e-4
    assert report["max_socket_position_error_m"] < 1e-6


def test_glb_motion_reports_pose_range_clip_and_unknown_target_failures(tmp_path: Path) -> None:
    path = tmp_path / "bad.glb"
    _GlbBuilder().write(path, clip_names=CLIPS[:-1], animate_extra=True)
    skeleton, motion = _fixture_motion()
    motion = copy.deepcopy(motion)
    motion["clips"][0]["samples"][1]["bones"][1]["head_m"][0] += .01
    motion["clips"][1]["samples"].pop()
    motion["clips"][2]["samples"][1]["bones"][1]["rotation_xyzw"] = [0.0, 0.0, 0.0, 1.0]

    report = validate_glb_motion(path, skeleton, motion)

    assert not report["passed"]
    codes = "\n".join(report["diagnostics"])
    assert "CC_GLB_MOTION_CLIPS" in codes
    assert "CC_GLB_MOTION_TARGET_EXTRA" in codes
    assert "CC_GLB_MOTION_RANGE" in codes
    assert "CC_GLB_MOTION_POSITION" in codes
    assert "CC_GLB_MOTION_ROTATION" in codes


def test_glb_motion_rejects_a_catalog_bone_missing_from_the_skin(tmp_path: Path) -> None:
    path = tmp_path / "missing_joint.glb"
    _GlbBuilder().write(path, omit_child_joint=True)
    skeleton, motion = _fixture_motion()

    report = validate_glb_motion(path, skeleton, motion)

    assert not report["passed"]
    assert any("CC_GLB_MOTION_SKIN" in diagnostic for diagnostic in report["diagnostics"])


def test_glb_surface_samples_actual_skinned_vertices_and_rejects_penetration(tmp_path: Path) -> None:
    path = tmp_path / "surface_below.glb"
    _GlbBuilder().write(path, surface_y=.01, root_end_y=-.02, root_end_rotation_deg=0)
    skeleton, motion = _fixture_motion()

    report = validate_glb_surface(path, skeleton, motion)

    assert not report["passed"]
    assert report["clips_checked"] == 8
    assert report["frames_checked"] == 24
    assert report["vertices_checked"] == 3
    assert report["vertex_samples_checked"] == 72
    assert report["min_surface_y_m"] == pytest.approx(-.01, abs=1e-6)
    assert report["max_below_ground_m"] == pytest.approx(.01, abs=1e-6)
    assert report["worst_surface"]["frame"] == 2
    assert report["worst_surface"]["node"] == 2
    assert report["worst_surface"]["mesh"] == 0
    assert report["worst_surface"]["primitive"] == 0
    assert report["worst_surface"]["vertex"] == 0
    assert set(report["per_clip_bounds"]) == set(CLIPS)
    assert all(bounds["min_y_m"] == pytest.approx(-.01, abs=1e-6)
               for bounds in report["per_clip_bounds"].values())
    assert any("CC_GLB_SURFACE_GROUND" in diagnostic for diagnostic in report["diagnostics"])


def test_glb_surface_accepts_geometry_within_five_millimetres(tmp_path: Path) -> None:
    path = tmp_path / "surface_clear.glb"
    _GlbBuilder().write(path, surface_y=.01, root_end_y=-.0149, root_end_rotation_deg=0)
    skeleton, motion = _fixture_motion()

    report = validate_glb_surface(path, skeleton, motion)

    assert report["passed"], report["diagnostics"]
    assert report["max_below_ground_m"] == pytest.approx(.0049, abs=1e-6)


def test_glb_surface_rejects_non_normalized_skin_weights(tmp_path: Path) -> None:
    path = tmp_path / "bad_weights.glb"
    _GlbBuilder().write(path, surface_y=.01, surface_weight=.8)
    skeleton, motion = _fixture_motion()

    report = validate_glb_surface(path, skeleton, motion)

    assert not report["passed"]
    assert any("CC_GLB_SURFACE_WEIGHTS" in diagnostic for diagnostic in report["diagnostics"])


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_glb_surface_rejects_nonfinite_positions(tmp_path: Path, bad_value: float) -> None:
    path = tmp_path / "nonfinite_position.glb"
    _GlbBuilder().write(path, surface_y=bad_value)
    skeleton, motion = _fixture_motion()

    report = validate_glb_surface(path, skeleton, motion)

    assert not report["passed"]
    assert any("CC_GLB_SURFACE_POSITION_NONFINITE" in diagnostic for diagnostic in report["diagnostics"])


def test_glb_surface_rejects_nonfinite_inverse_bind_animation_and_weights(tmp_path: Path) -> None:
    skeleton, motion = _fixture_motion()
    cases = [
        ("bind", {"bind_nonfinite": True}, "CC_GLB_SURFACE_BIND_NONFINITE"),
        ("animation", {"root_end_y": float("inf")}, "CC_GLB_ANIMATION_NONFINITE"),
        ("weight", {"surface_weight": float("nan")}, "CC_GLB_SURFACE_WEIGHTS"),
    ]
    for name, options, code in cases:
        path = tmp_path / f"nonfinite_{name}.glb"
        _GlbBuilder().write(path, surface_y=.01, **options)
        report = validate_glb_surface(path, skeleton, motion)
        assert not report["passed"]
        assert any(code in diagnostic for diagnostic in report["diagnostics"]), report["diagnostics"]
