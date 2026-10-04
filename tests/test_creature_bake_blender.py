from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from critter_crafter.blender.runner import BlenderError, run_op
from critter_crafter.config import find_blender
from critter_crafter.library.bake import bake_recipe, index_input_paths
from critter_crafter.recipes.generator import generate_for_skeleton

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


def test_connector_color_samples_triangle_uv_barycentrics_not_nearest_vertex(tmp_path):
    result = run_op("creaturebake", {"library_dir": str(tmp_path), "material_fixture": True})["result"]
    assert result["transition_color"] == pytest.approx(result["expected_color"], abs=2e-5)


@pytest.mark.parametrize("feature", ["Alpha", "Metallic", "Transmission Weight", "Roughness"])
def test_unsupported_material_features_fail_instead_of_silent_flatten(tmp_path, feature):
    with pytest.raises(BlenderError, match="CC_BAKE_MATERIAL"):
        run_op("creaturebake", {"library_dir": str(tmp_path), "material_fixture": True, "unsupported": feature})


def test_imported_default_normal_map_preserves_shading_and_baked_albedo(tmp_path):
    result = run_op("creaturebake", {"library_dir": str(tmp_path), "fbx_material_fixture": True})["result"]
    assert result["default_normal_shading_error"] <= 1 / 255 + 1e-5
    assert result["source_albedo_error"] < 2e-5
    assert result["baked_albedo_error"] < 2e-5


@pytest.mark.parametrize("normal", ["texture", "bump", "nonneutral"])
def test_imported_material_normal_features_cannot_be_flattened(tmp_path, normal):
    with pytest.raises(BlenderError, match="CC_BAKE_MATERIAL: unsupported Normal"):
        run_op("creaturebake", {"library_dir": str(tmp_path), "fbx_material_fixture": True,
                               "unsupported_normal": normal})


def test_actual_library_bake_and_all_eight_pose_comparisons(tmp_path):
    selected = os.environ.get("CRITTER_BAKE_LIBRARY")
    if not selected:
        pytest.skip("Set CRITTER_BAKE_LIBRARY to an existing mutable compiled library for real atlas comparison")
    root = Path(selected).resolve()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    skeleton_id = os.environ.get("CRITTER_BAKE_SKELETON", "biped_plantigrade_humanoid_balanced_v3")
    skeleton = next(s for s in catalog["skeletons"] if s["skeleton_id"] == skeleton_id)
    original_catalog = (root / "catalog.json").read_bytes()
    recipe = generate_for_skeleton(catalog, skeleton["skeleton_id"], 1)
    proof = Path(os.environ.get("CRITTER_BAKE_PROOF", str(tmp_path / "comparison")))
    entry = bake_recipe(recipe, root, allow_review=True, force=True, proof_dir=proof)
    report = json.loads((proof / "comparison.json").read_text(encoding="utf-8"))
    assert report["max_pose_error_m"] < 1e-5
    assert report["clips"] == ["idle", "walk", "run", "stun", "telegraph", "attack", "hit", "death"]
    assert report["atlas_size"] == 4096
    assert report["gutter_px"] == 32
    assert (root / "catalog.json").read_bytes() == original_catalog
    for clip in report["clips"]:
        assert (proof / f"live_{clip}.png").is_file()
        assert (proof / f"baked_{clip}.png").is_file()
    paths = index_input_paths(root)
    assert entry["fbx"] in paths and entry["albedo_png"] in paths
    assert bake_recipe(recipe, root, allow_review=True) == entry
