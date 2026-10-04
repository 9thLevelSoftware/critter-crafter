"""Blender regressions for authored reference-part cross-section dimensions."""

from copy import deepcopy

import pytest

from critter_crafter.blender.connector_surface import grid_spec
from critter_crafter.blender.runner import run_op
from critter_crafter.config import find_blender
from critter_crafter.library.commands import _catalog

pytestmark = pytest.mark.blender
if not find_blender():
    pytest.skip("Blender 5.x not found", allow_module_level=True)


def test_reference_connector_uses_its_authored_elliptical_thickness(tmp_path):
    catalog = _catalog()
    part = deepcopy(next(item for item in catalog["parts"]
                         if item["part_id"] == "reference_connector_serpentine_limbless_articulated_balanced_v3_body_v1"))
    part["dimensions_m"][1] = part["dimensions_m"][0]
    template = next(item for item in catalog["binding_profiles"]
                    if (item["binding_profile_id"], item["binding_profile_version"])
                    == (part["binding_profile_id"], part["binding_profile_version"]))
    circular = run_op("placeholder", {
        "part": part, "template": template,
        "out_fbx": str(tmp_path / "circle.fbx"), "out_glb": str(tmp_path / "circle.glb"),
    })["result"]
    part["dimensions_m"][1] = .10
    result = run_op("placeholder", {
        "part": part, "template": template,
        "out_fbx": str(tmp_path / "ellipse.fbx"), "out_glb": str(tmp_path / "ellipse.glb"),
    })["result"]

    lower, upper = result["part_space_bounds_m"]
    circular_lower, circular_upper = circular["part_space_bounds_m"]
    width, thickness = upper[0] - lower[0], upper[1] - lower[1]
    circular_width = circular_upper[0] - circular_lower[0]
    circular_thickness = circular_upper[1] - circular_lower[1]
    assert 0 < thickness < width
    assert width / thickness > circular_width / circular_thickness
    radii = [dimension / 2 for dimension in part["dimensions_m"][:2]]
    voxel, _ = grid_spec(*radii, part["connector_span_m"], part["connector_surface"])
    for axis, radius in enumerate(radii):
        assert max(abs(lower[axis]), abs(upper[axis])) <= radius + voxel + 1e-8
    assert result["triangles"] <= part["max_triangles"]
    assert result["max_influences"] <= 4
    assert result["min_weight_sum"] == pytest.approx(1.0)
    assert result["max_weight_sum"] == pytest.approx(1.0)
