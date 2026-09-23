"""Blender regressions for authored reference-part cross-section dimensions."""

from copy import deepcopy

import pytest

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
    part["dimensions_m"][1] = .10
    template = next(item for item in catalog["binding_profiles"]
                    if (item["binding_profile_id"], item["binding_profile_version"])
                    == (part["binding_profile_id"], part["binding_profile_version"]))
    result = run_op("placeholder", {
        "part": part, "template": template,
        "out_fbx": str(tmp_path / "ellipse.fbx"), "out_glb": str(tmp_path / "ellipse.glb"),
    })["result"]

    lower, upper = result["part_space_bounds_m"]
    assert upper[0] > .20  # preserve the nominal lateral body girth
    assert lower[1] < -.04 and upper[1] > .04  # a full rounded ellipse, not a flattened plane
    assert max(abs(lower[1]), abs(upper[1])) <= .05 * 1.025 + 1e-6
    assert result["triangles"] <= part["max_triangles"]
    assert result["max_influences"] <= 4
    assert result["min_weight_sum"] == pytest.approx(1.0)
    assert result["max_weight_sum"] == pytest.approx(1.0)
