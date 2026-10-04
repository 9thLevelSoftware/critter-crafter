import copy
import hashlib
import json

import pytest

from critter_crafter.authoring.jobs import (artifact, canonical_bytes, load_job, load_result,
                                          resolve_artifact, write_job, write_result)
from critter_crafter.parts.learned_weights import geometry_inputs, geometry_identity


@pytest.mark.parametrize("path", ["../secret", "/secret", "C:/secret", "C:secret", "folder\\secret"])
def test_artifacts_cannot_escape_job_root(tmp_path, path):
    with pytest.raises(ValueError, match="CC_AUTHORING_PATH"):
        resolve_artifact(tmp_path, path)


def test_job_identity_and_artifact_tampering_are_rejected(tmp_path):
    (tmp_path / "geometry.json").write_text("geometry")
    job = write_job(tmp_path, "skin", {"geometry": artifact(tmp_path, "geometry.json")}, {"seed": 1})
    assert load_job(tmp_path) == job
    changed = copy.deepcopy(job)
    changed["options"]["seed"] = 2
    (tmp_path / "job.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="changed job identity"):
        load_job(tmp_path)
    (tmp_path / "job.json").write_text(json.dumps(job))
    (tmp_path / "geometry.json").write_text("changed geometry")
    with pytest.raises(ValueError, match="changed artifact"):
        load_job(tmp_path)


def test_result_from_another_job_cannot_be_imported(tmp_path):
    job = write_job(tmp_path, "motion", {}, {"seed": 1})
    result_root = tmp_path / "result"
    result_root.mkdir()
    (result_root / "samples.json").write_text("samples")
    digest = hashlib.sha256(b"model").hexdigest()
    write_result(result_root, job, "upstream-revision", digest, digest,
                 {"samples": artifact(result_root, "samples.json")})
    other = write_job(tmp_path, "motion", {}, {"seed": 2})
    with pytest.raises(ValueError, match="another job"):
        load_result(result_root, other)


def test_canonical_json_is_portable_and_rejects_nonfinite_values():
    assert canonical_bytes({"z": "é", "a": 1}) == b'{"a":1,"z":"\xc3\xa9"}'
    with pytest.raises(ValueError):
        canonical_bytes({"value": float("nan")})


def test_geometry_identity_excludes_weight_selection_but_tracks_fit_profile_and_uvs():
    part = {"part_id": "fixture", "real": {"sha256": "source", "fit": {"axis": "+z", "weights": "smooth"}},
            "template": "limb3", "length_m": 1.0, "girth_m": 0.2, "max_triangles": 1000}
    profile = {"hash": "profile"}
    geometry = {"vertices": [[0.0, 0.0, 0.0]], "faces": [], "uvs": [[0.1, 0.2]]}
    before = geometry_inputs(part, profile, "producer")
    original = geometry_identity(before, geometry)
    part["real"]["fit"]["weights"] = "learned"
    part["real"]["learned_weights"] = {"sha256": "weights"}
    assert geometry_identity(geometry_inputs(part, profile, "producer"), geometry) == original
    part["real"]["fit"]["mirror_x"] = True
    assert geometry_identity(geometry_inputs(part, profile, "producer"), geometry) != original
    altered = copy.deepcopy(geometry)
    altered["uvs"][0][0] = 0.3
    assert geometry_identity(before, altered) != original
    assert geometry_identity(geometry_inputs(part, {"hash": "different"}, "producer"), geometry) != original
