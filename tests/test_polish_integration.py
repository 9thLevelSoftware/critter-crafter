"""Actual Blender protection for authored animation polish overrides."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from critter_crafter.blender.runner import BlenderError, run_op
from critter_crafter.config import find_blender, paths
from critter_crafter.library.catalog import compile_catalog, load_sources
from critter_crafter.library.commands import skeleton_source_hash
from critter_crafter.library.export_validation import validate_glb_rest

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)

EXPECTED_CLIPS = {"idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample(motion: dict, clip_name: str, frame: int, bone_name: str) -> dict:
    clip = next(clip for clip in motion["clips"] if clip["name"] == clip_name)
    sample = next(sample for sample in clip["samples"] if sample["frame"] == frame)
    return next(bone for bone in sample["bones"] if bone["name"] == bone_name)


def _rotation_distance(left: list[float], right: list[float]) -> float:
    dot = abs(sum(a * b for a, b in zip(left, right, strict=True)))
    return 2.0 * math.acos(max(-1.0, min(1.0, dot)))


def _skeleton_args(
    root: Path,
    name: str,
    skeleton: dict,
    gait: dict,
    profiles: list[dict],
    source_fingerprint: str,
    polish_blend: Path | None = None,
) -> dict:
    output = root / name
    return {
        "skeleton": skeleton,
        "gait_profile": gait,
        "binding_profiles": profiles,
        "source_fingerprint": source_fingerprint,
        "polish_blend": str(polish_blend) if polish_blend else "",
        "out_fbx": str(output / "skeleton.fbx"),
        "out_glb": str(output / "skeleton.glb"),
        "out_blend": str(output / "skeleton.blend"),
        "out_motion": str(output / "motion.json"),
    }


@pytest.fixture(scope="module")
def polish_build(tmp_path_factory):
    root = tmp_path_factory.mktemp("polish")
    catalog = compile_catalog(load_sources(paths().data))
    skeleton = next(
        item for item in catalog["skeletons"]
        if item["skeleton_id"] == "biped_plantigrade_humanoid_balanced_v3"
    )
    gait = next(item for item in catalog["gait_profiles"] if item["hint"] == skeleton["locomotion_hint"])
    profiles = catalog["binding_profiles"]
    fingerprint = skeleton_source_hash(catalog, skeleton)
    procedural_args = _skeleton_args(root, "procedural", skeleton, gait, profiles, fingerprint)
    procedural_result = run_op("skeleton", procedural_args)["result"]
    procedural_motion = json.loads(Path(procedural_result["motion"]).read_text(encoding="utf-8"))
    idle = next(clip for clip in procedural_motion["clips"] if clip["name"] == "idle")
    frame = int(idle["frames"] // 2)
    assert any(sample["frame"] == frame for sample in idle["samples"])
    head = next(branch for branch in skeleton["branches"] if branch["gait_role"] == "head")
    bone_name = head["bone_names"][0]

    authored = root / "authored" / "master.blend"
    bad_rest = root / "bad_rest" / "master.blend"
    bad_source = root / "bad_source" / "master.blend"
    fixture = run_op("polish_fixture", {
        "input_blend": procedural_args["out_blend"],
        "authored_blend": str(authored),
        "bad_rest_blend": str(bad_rest),
        "bad_source_blend": str(bad_source),
        "action_name": "idle",
        "bone_name": bone_name,
        "frame": frame,
        "angle_deg": 3.0,
    })["result"]
    assert fixture["before_wxyz"] != fixture["after_wxyz"]
    authored_hash = _digest(authored)
    polished_args = _skeleton_args(root, "polished", skeleton, gait, profiles, fingerprint, authored)
    polished_result = run_op("skeleton", polished_args)["result"]
    polished_motion = json.loads(Path(polished_result["motion"]).read_text(encoding="utf-8"))
    return {
        "root": root,
        "skeleton": skeleton,
        "gait": gait,
        "profiles": profiles,
        "fingerprint": fingerprint,
        "procedural_result": procedural_result,
        "procedural_motion": procedural_motion,
        "polished_args": polished_args,
        "polished_result": polished_result,
        "polished_motion": polished_motion,
        "authored": authored,
        "authored_hash": authored_hash,
        "bad_rest": bad_rest,
        "bad_source": bad_source,
        "frame": frame,
        "bone_name": bone_name,
    }


def test_authored_mid_idle_head_key_survives_polish_without_mutating_source(polish_build):
    procedural = _sample(
        polish_build["procedural_motion"], "idle", polish_build["frame"], polish_build["bone_name"]
    )
    polished = _sample(
        polish_build["polished_motion"], "idle", polish_build["frame"], polish_build["bone_name"]
    )
    assert _rotation_distance(procedural["rotation_xyzw"], polished["rotation_xyzw"]) > math.radians(2.0)
    assert _digest(polish_build["authored"]) == polish_build["authored_hash"]


def test_polished_build_keeps_rest_hierarchy_and_complete_clip_set(polish_build):
    skeleton = polish_build["skeleton"]
    report = validate_glb_rest(Path(polish_build["polished_args"]["out_glb"]), skeleton)
    assert report["passed"], report["diagnostics"]
    assert set(polish_build["polished_motion"]["bone_names"]) == {bone["name"] for bone in skeleton["bones"]}
    assert {clip["name"] for clip in polish_build["polished_motion"]["clips"]} == EXPECTED_CLIPS
    assert {clip["name"] for clip in polish_build["procedural_motion"]["clips"]} == EXPECTED_CLIPS


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("bad_rest", "polish rest geometry differs at bone"),
        ("bad_source", "polish source fingerprint differs from the requested skeleton build"),
    ],
)
def test_incompatible_polish_override_is_rejected(polish_build, key: str, message: str):
    args = _skeleton_args(
        polish_build["root"],
        f"reject_{key}",
        polish_build["skeleton"],
        polish_build["gait"],
        polish_build["profiles"],
        polish_build["fingerprint"],
        polish_build[key],
    )
    with pytest.raises(BlenderError, match=message):
        run_op("skeleton", args)
