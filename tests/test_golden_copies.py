"""The Python and Unity test suites assert the same goldens, so the two copies must be byte-identical."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON_GOLDENS = ROOT / "tests" / "golden_v3"
UNITY_GOLDENS = ROOT / "unity" / "com.ninthlevelsoftware.crittercrafter" / "Tests" / "Editor" / "GoldenV3"


def test_python_goldens_exist():
    assert sorted(path.name for path in PYTHON_GOLDENS.glob("*.json"))


@pytest.mark.parametrize("golden", sorted(PYTHON_GOLDENS.glob("*.json")), ids=lambda path: path.name)
def test_unity_copy_is_byte_identical(golden):
    copy = UNITY_GOLDENS / golden.name
    assert copy.exists(), f"missing Unity copy: Copy-Item tests/golden_v3/*.json {UNITY_GOLDENS}"
    assert copy.read_bytes() == golden.read_bytes(), (
        f"{golden.name} differs from the Unity copy: re-run `critter recipe golden` and copy the goldens"
    )
