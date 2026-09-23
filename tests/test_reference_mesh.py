"""Blender-host assertions for the anatomical reference mannequin."""

import json
import subprocess

import pytest

from critter_crafter.blender.runner import run_op
from critter_crafter.config import find_blender, paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.library.commands import build_jobs

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


def test_reference_mesh_has_bounded_normalized_skinning_and_budget():
    catalog = compile_catalog(load_sources(paths().data))
    skeleton = next(item for item in catalog["skeletons"]
                    if item["skeleton_id"] == "biped_plantigrade_humanoid_balanced_v3")
    metrics = run_op("reference", {"skeleton": skeleton})["result"]

    assert metrics["technique"] == "anatomical_socket_volumes_v2"
    assert metrics["joint_radius_policy"] == "adjacent_bone_exact_v1"
    assert metrics["foot_terminal_pads"] == 2
    assert metrics["vertices"] > 0
    assert metrics["triangles"] <= 30_000
    assert metrics["materials"] <= 2
    assert metrics["max_influences"] <= 4
    assert metrics["min_weight_sum"] == pytest.approx(1.0)
    assert metrics["max_weight_sum"] == pytest.approx(1.0)


def test_all_reference_meshes_clear_the_neutral_ground_tolerance():
    catalog = compile_catalog(load_sources(paths().data))
    jobs = [{"op": "reference", "args": {"skeleton": skeleton, "neutral": True}}
            for skeleton in catalog["skeletons"]]
    results = run_op("batch", {"jobs": jobs})["results"]

    lows = {skeleton["skeleton_id"]: result["result"]["neutral_min_y_m"]
            for skeleton, result in zip(catalog["skeletons"], results, strict=True)}
    assert min(lows.values()) >= -.005, lows


def test_foot_socket_bridge_stays_above_ground_during_serpentine_walk(tmp_path):
    catalog = compile_catalog(load_sources(paths().data))
    skeleton_id = "serpentine_segmented_paired_legs_compact_v3"
    skeleton = next(item for item in catalog["skeletons"] if item["skeleton_id"] == skeleton_id)
    job = next(job for job in build_jobs(catalog, tmp_path, {skeleton_id}) if job["op"] == "skeleton")
    run_op("skeleton", job["args"])

    report = tmp_path / "socket-minima.json"
    probe = tmp_path / "socket-minima.py"
    probe.write_text(
        "import bpy, json\n"
        f"report = {str(report)!r}\n"
        "arm = bpy.data.objects['Skeleton']\n"
        "mesh = next(obj for obj in bpy.data.objects if obj.type == 'MESH' and obj.get('cc_reference_technique'))\n"
        "arm.animation_data.action = bpy.data.actions['walk']\n"
        "minima = []\n"
        "depsgraph = bpy.context.evaluated_depsgraph_get()\n"
        "for frame in (0, 24):\n"
        "    bpy.context.scene.frame_set(frame)\n"
        "    bpy.context.view_layer.update()\n"
        "    evaluated = mesh.evaluated_get(depsgraph)\n"
        "    evaluated_mesh = evaluated.to_mesh()\n"
        "    minima.append(min((evaluated.matrix_world @ vertex.co).z for vertex in evaluated_mesh.vertices))\n"
        "    evaluated.to_mesh_clear()\n"
        "Path = __import__('pathlib').Path\n"
        "Path(report).write_text(json.dumps(minima))\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(find_blender()), "-b", str(job["args"]["out_blend"]), "-noaudio", "-P", str(probe)],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    minima = json.loads(report.read_text(encoding="utf-8"))
    assert min(minima) >= -.005, minima
