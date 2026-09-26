"""Real Blender FBX regression fixture for two-bone skinned connector assembly."""

import json

import pytest

from critter_crafter.blender.runner import BlenderError, run_op
from critter_crafter.config import find_blender
from critter_crafter.library.commands import _catalog
from critter_crafter.parts.commands import STRAIN_P99_LIMIT

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


def test_asymmetric_two_bone_connector_survives_fbx_import_and_assembly(tmp_path):
    result = run_op("binding_fixture", {"out_dir": str(tmp_path)})["result"]

    assert result["body_fbx"]
    assert result["connector_fbx"]
    assert result["material_count"] == 3  # body plus both connector submesh materials
    assert {"M_ConnectorParent", "M_ConnectorChild"} <= set(result["material_names"])
    assert result["max_influences"] <= 4
    assert result["min_weight_sum"] == pytest.approx(1.0)
    assert result["max_weight_sum"] == pytest.approx(1.0)
    assert result["orientation_error_m"] < 1e-5
    assert result["parent_delta_m"] < 1e-5
    assert result["child_delta_m"] > .03


def test_large_girth_connector_respects_triangle_budget_and_normalizes_weights(tmp_path):
    catalog = _catalog()
    connector = max((part for part in catalog["parts"] if part["category"] == "connector"),
                    key=lambda part: part["connector_radius_m"])
    profile = next(profile for profile in catalog["binding_profiles"]
                   if (profile["binding_profile_id"], profile["binding_profile_version"])
                   == (connector["binding_profile_id"], connector["binding_profile_version"]))
    result = run_op("connector", {
        "part": connector, "template": profile,
        "out_fbx": str(tmp_path / "connector.fbx"),
        "out_glb": str(tmp_path / "connector.glb"),
    })["result"]

    assert connector["connector_radius_m"] > .2
    assert result["triangles"] <= connector["max_triangles"]
    assert connector["max_triangles"] == 800
    assert result.get("baker") == "sdf"
    assert result["max_influences"] <= 2
    assert result["bones"] == ["b0", "b1"]
    assert result["unweighted"] == 0
    assert result["min_weight_sum"] == pytest.approx(1.0, abs=1e-4)
    assert result["max_weight_sum"] == pytest.approx(1.0, abs=1e-4)
    assert result["nonmanifold_edges"] == 0
    assert result["boundary_edges"] == 0


def test_limb3_sdf_join_compared_to_loft_at_800(tmp_path):
    catalog = _catalog()
    connector = next(part for part in catalog["parts"]
                     if part["part_id"] == "reference_connector_biped_plantigrade_humanoid_balanced_v3_leg_l_v1")
    profile = next(profile for profile in catalog["binding_profiles"]
                   if (profile["binding_profile_id"], profile["binding_profile_version"])
                   == (connector["binding_profile_id"], connector["binding_profile_version"]))
    loft = run_op("placeholder", {
        "part": connector, "template": profile, "measure_swing": True,
        "out_fbx": str(tmp_path / "loft.fbx"), "out_glb": str(tmp_path / "loft.glb"),
    })["result"]
    sdf = run_op("connector", {
        "part": connector, "template": profile,
        "out_fbx": str(tmp_path / "sdf.fbx"), "out_glb": str(tmp_path / "sdf.glb"),
    })["result"]
    print("LIMB3_CONNECTOR_COMPARE", json.dumps({"loft": loft, "sdf": sdf}, sort_keys=True))
    assert loft["triangles"] <= 800
    assert sdf["triangles"] <= 800
    assert sdf["max_influences"] <= 2
    assert sdf["bones"] == ["b0", "b1"]
    assert sdf["unweighted"] == 0
    assert sdf["min_weight_sum"] == pytest.approx(1.0, abs=1e-4)
    assert sdf["max_weight_sum"] == pytest.approx(1.0, abs=1e-4)
    assert sdf.get("baker") == "sdf"
    assert sdf["nonmanifold_edges"] == 0
    assert sdf["boundary_edges"] == 0
    s0, s1 = connector["connector_span_m"]
    lower, upper = sdf["part_space_bounds_m"]
    slop = sdf.get("voxel_size", 0.002)
    assert lower[2] >= s0 - slop
    assert upper[2] <= s1 + slop
    assert sdf["swing_strain_p99"] <= max(STRAIN_P99_LIMIT, loft["swing_strain_p99"] * 1.25)


def test_actual_assembly_rejects_incompatible_connector_profile(tmp_path):
    with pytest.raises(BlenderError, match="CC_CONNECTOR_MISMATCH"):
        run_op("binding_fixture", {"out_dir": str(tmp_path), "incompatible_connector": True})
