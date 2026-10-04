"""Native-grid bounds and the public connector FBX/deformation path."""
import copy
import math

import pytest

from critter_crafter.blender.connector_surface import (
    DEFAULT_SURFACE, _closed_surface, certify_skin_envelope, deformation_radius, grid_spec, profile_angle,
)
from critter_crafter.blender.frame import to_gltf
from critter_crafter.blender.runner import BlenderError, run_op
from critter_crafter.config import find_blender
from critter_crafter.library.commands import _catalog


@pytest.mark.parametrize("span", [[-.04, .04], [-.039, .06], [-.08, .08]])
@pytest.mark.parametrize("kind", ["collar", "guard", "bulge"])
def test_authored_surface_keeps_smoothstep_skin_jacobian_positive_through_shoulder_bends(span, kind):
    profile = {"joints": [{"limits_deg": {"swing_x": [-120, 120], "swing_y": [-90, 90], "twist": [-90, 90]}}]}
    vertices, _ = _closed_surface(.073, .051, *span, profile_angle(profile), kind, 1.2)
    points = [to_gltf(point) for point in vertices]

    def deform(point, radians):
        x, y, z = point
        t = (z - span[0]) / (span[1] - span[0])
        w = t * t * (3 - 2 * t)
        c, s = math.cos(radians), math.sin(radians)
        return (x, (1-w)*y + w*(c*y-s*z), (1-w)*z + w*(s*y+c*z))

    for degrees in (-120, -105, -90, -55, 55, 90, 105, 120):
        for point in points[::137]:
            columns = []
            epsilon = 1e-7
            for axis in range(3):
                low, high = list(point), list(point)
                low[axis] -= epsilon
                high[axis] += epsilon
                a, b = deform(low, math.radians(degrees)), deform(high, math.radians(degrees))
                columns.append([(b[i] - a[i]) / (2 * epsilon) for i in range(3)])
            a, b, c = columns
            determinant = (a[0]*(b[1]*c[2]-b[2]*c[1]) - b[0]*(a[1]*c[2]-a[2]*c[1])
                           + c[0]*(a[1]*b[2]-a[2]*b[1]))
            assert determinant > .1


def test_actual_native_taper_triangle_has_a_continuous_safe_envelope_certificate():
    vertices = [
        (0.02773636020720005, -0.023768913000822067, -0.03048381209373474),
        (0.014650695957243443, 0.0001910597347887233, -0.01310189813375473),
        (0.007025463506579399, -0.0077490671537816525, -0.009340284392237663),
    ]
    angle, span = math.radians(120), [-.06, .06]
    assert max(math.hypot(x, y) for x, y, z in vertices) > min(
        deformation_radius(z, span, angle, .25) for x, y, z in vertices)
    maximum_fraction = certify_skin_envelope(vertices, [(0, 1, 2)], span, angle)
    assert 0 < maximum_fraction <= 1


def test_safe_vertices_cannot_hide_an_edge_crossing_the_waist_envelope():
    vertices = [(.02, 0, -.04), (.02, 0, .04), (.02, .001, .04)]
    angle, span = math.radians(120), [-.06, .06]
    assert all(math.hypot(x, y) < deformation_radius(z, span, angle, .25) for x, y, z in vertices)
    with pytest.raises(ValueError, match="CC_CONNECTOR_DEFORMATION: edge=.*witness_m="):
        certify_skin_envelope(vertices, [(0, 1, 2)], span, angle)


def test_grid_uses_smallest_physical_extent_and_includes_narrow_band():
    voxel, cells = grid_spec(.09, .045, [-.02, .06], DEFAULT_SURFACE)
    assert voxel == pytest.approx(.045 / 24)
    assert cells == (104, 56, 51)


@pytest.mark.parametrize("rx,ry,span", [(.01, 1., [-.05, .05]), (1., .25, [-.5, .5])])
def test_oversized_grid_is_rejected_instead_of_coarsened(rx, ry, span):
    settings = {**DEFAULT_SURFACE, "voxel_divisions": 64}
    with pytest.raises(ValueError, match="CC_CONNECTOR_GRID_LIMIT"):
        grid_spec(rx, ry, span, settings)


@pytest.mark.parametrize("field,value", [("bulge", 1.20001), ("bulge", float("nan")),
    ("voxel_divisions", 11), ("voxel_divisions", 64.0), ("fillet_iterations", 9),
    ("fillet_iterations", -1), ("method", "loft")])
def test_invalid_surface_cannot_enter_native_mesher(field, value):
    with pytest.raises(ValueError, match="CC_CONNECTOR_SURFACE"):
        grid_spec(.1, .1, [-.06, .06], {**DEFAULT_SURFACE, field: value})


@pytest.mark.blender
@pytest.mark.parametrize("part_id", [
    *(f"organic_collar_{size}_{shape}_v1"
      for size in ("s", "m", "l") for shape in ("straight", "bulged", "asymmetric")),
    "reference_connector_amalgam_hauled_s0013_v3_flail2_v1",
])
def test_native_connector_exports_closed_mesh_and_exact_asymmetric_binding(tmp_path, part_id):
    if not find_blender():
        pytest.skip("Blender 5.2 not found")
    catalog = _catalog()
    part = next(p for p in catalog["parts"] if p["part_id"] == part_id)
    profile = next(p for p in catalog["binding_profiles"] if p["binding_profile_hash"] == part["binding_profile_hash"])
    result = run_op("connectorqa", {"part": part, "template": profile,
        "out_dir": str(tmp_path), "structure_only": True})["result"]
    assert result["structural"]["sdf"]["manifold"]
    assert result["structural"]["sdf"]["triangles"] <= 300
    assert result["asymmetric"]["attachment_error_m"] < 1e-5
    assert result["asymmetric"]["skin_transform_error_m"] < 1e-5
    assert result["asymmetric"]["max_axial_weight_error"] <= 1e-5
    assert result["asymmetric"]["bone_groups"] == ["child", "root"]
    assert result["asymmetric"]["max_weight_sum_error"] <= 1e-6
    assert result["asymmetric"]["child_endpoint_delta_m"] > 1e-5
    assert not result["deformation_complete"]
    assert not result["passed"]


@pytest.mark.blender
def test_impossible_connector_budget_is_not_exported_as_valid(tmp_path):
    if not find_blender():
        pytest.skip("Blender 5.2 not found")
    catalog = _catalog()
    part = copy.deepcopy(next(p for p in catalog["parts"] if p["part_id"] == "organic_collar_m_bulged_v1"))
    part["max_triangles"] = 1
    profile = next(p for p in catalog["binding_profiles"] if p["binding_profile_hash"] == part["binding_profile_hash"])
    with pytest.raises(BlenderError, match="CC_CONNECTOR_(MESH|MANIFOLD|BUDGET|DEGENERATE)"):
        run_op("placeholder", {"part": part, "template": profile, "out_fbx": str(tmp_path / "bad.fbx")})
