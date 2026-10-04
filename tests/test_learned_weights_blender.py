"""Public asymmetric, multi-column CSR fixtures; NumPy runs in Blender, never the host."""
import json
import subprocess
from pathlib import Path

import pytest

from critter_crafter.config import find_blender

pytestmark = pytest.mark.blender


@pytest.mark.parametrize("failure", [None, "column", "vertex", "negative", "duplicate", "empty"])
def test_csr_original_indices_bind_columns_and_stable_pruning(tmp_path, failure):
    blender = find_blender()
    if not blender:
        pytest.skip("Blender unavailable")
    script = tmp_path / "check.py"
    repo_src = Path(__file__).resolve().parents[1] / "src"
    script.write_text(f'''
import sys
sys.path.insert(0, {str(repo_src)!r})
import numpy as np
from critter_crafter.parts.learned_weights import validate_csr, vertex_sha256
geometry = {{"vertices": [[0.13, 0.04, 0.0], [-0.11, -0.02, 0.71]],
             "bones": [{{"name": "b" + str(i)}} for i in range(6)]}}
failure = {failure!r}
indices = np.array([5, 3, 1, 4, 2, 0, 4, 2], dtype=np.int64)
weights = np.array([0.3, 0.2, 0.2, 0.1, 0.1, 0.1, 0.8, 0.2])
ptr = np.array([0, 6, 8], dtype=np.int64)
names = np.array(["b" + str(i) for i in range(6)])
vh = vertex_sha256(geometry["vertices"])
if failure == "column": names[0] = "other"
if failure == "vertex": vh = "changed"
if failure == "negative": weights[0] = -1
if failure == "duplicate": indices[0] = indices[1]
if failure == "empty": ptr[1] = 0
path = {str(tmp_path / "weights.npz")!r}
np.savez(path, indptr=ptr, bone_indices=indices, weights=weights, bone_ids=names, vertex_sha256=np.asarray(vh))
try:
    rows = validate_csr(path, geometry)
except ValueError as error:
    assert failure is not None, str(error)
    expected = {{"column":"CC_SKIN_BIND", "vertex":"CC_SKIN_STALE", "negative":"CC_SKIN_WEIGHTS",
                 "duplicate":"CC_SKIN_WEIGHTS", "empty":"CC_SKIN_WEIGHTS"}}[failure]
    assert expected in str(error), str(error)
else:
    assert failure is None
    assert list(rows[0]) == ["b5", "b1", "b3", "b0"], rows[0]
    assert abs(rows[0]["b5"] - 0.375) < 1e-12
    assert rows[1] == {{"b4": 0.8, "b2": 0.2}}
    assert all(abs(sum(row.values()) - 1) < 1e-12 for row in rows)
print("CSR_BOUNDARIES_OK")
''', encoding="utf-8")
    completed = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(script)],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "CSR_BOUNDARIES_OK" in completed.stdout
