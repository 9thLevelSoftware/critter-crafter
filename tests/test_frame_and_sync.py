from pathlib import Path

from critter_crafter.blender.frame import MATRIX, to_blender, to_gltf

ROOT = Path(__file__).resolve().parents[1]
PKG_GOLDEN = ROOT / "unity" / "com.ninthlevelsoftware.crittercrafter" / "Tests" / "Editor" / "Golden"


def test_frame_mapping_is_proper_involution():
    for v in [(1.0, 2.0, 3.0), (-0.3, 0.0, 1.66)]:
        assert to_gltf(to_blender(v)) == v
    m = MATRIX
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    assert det == 1.0  # a rotation: face winding and chirality survive the mapping
    for v in [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]:
        assert tuple(sum(m[r][c] * v[c] for c in range(3)) for r in range(3)) == to_blender(v)


def test_catalog_forward_is_blender_plus_y():
    # Unity maps Blender -Y to Unity -Z, so glTF +Z (forward) must be Blender +Y to reach Unity +Z.
    assert to_blender((0.0, 0.0, 1.0)) == (-0.0, 1.0, 0.0)


def test_unity_package_golden_copy_is_in_sync():
    for name in ("catalog.json", "recipes.json"):
        assert (PKG_GOLDEN / name).read_bytes() == (ROOT / "tests" / "golden" / name).read_bytes(), (
            f"copy tests/golden/{name} into the Unity package Tests/Editor/Golden")
