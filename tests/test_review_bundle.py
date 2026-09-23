import json

import pytest

from critter_crafter.skeletons.bundle import BundleError, write_bundle


def _catalog():
    return {
        "library_id": "test_library", "version": "3.0.0", "frame": "gltf_rh_yup_zfwd_m",
        "skeletons": [{
            "skeleton_id": "walker", "family": "biped", "status": "draft",
            "source_fingerprint": "source-hash", "content_fingerprint": "content-hash",
            "neutral_pose": {"rotations": [{"bone_name": "root", "rotation_xyzw": [0, 0, 0, 1]}]},
            "asset": {"glb": "skeletons/walker/walker.glb", "motion": "skeletons/walker/motion.json",
                      "assembled_glb": "skeletons/walker/assembled.glb",
                      "clips": [{"name": "walk", "frames": 30, "fps": 30, "speed_mps": 1.2}]},
        }],
    }


def _artifacts(root):
    path = root / "skeletons" / "walker"
    path.mkdir(parents=True)
    (path / "walker.glb").write_bytes(b"actual glb bytes")
    (path / "assembled.glb").write_bytes(b"actual assembled glb bytes")
    (path / "motion.json").write_text(json.dumps({"clips": [{"name": "walk", "fps": 30, "frames": 30,
        "duration_s": 1, "speed_mps": 1.2, "samples": [{"frame": 0, "contacts": []}]}]}))


def test_bundle_copies_actual_artifacts_and_local_viewer_runtime(tmp_path):
    library, out = tmp_path / "library", tmp_path / "review"
    _artifacts(library)
    pages = write_bundle(_catalog(), library, out)

    assert pages == [out / "skeletons" / "walker" / "index.html"]
    assert (out / "assets" / "walker" / "skeleton.glb").read_bytes() == b"actual glb bytes"
    assert (out / "assets" / "walker" / "motion.json").is_file()
    assert (out / "assets" / "walker" / "assembled.glb").is_file()
    assert (out / "viewer" / "vendor" / "three.module.js").is_file()
    assert (out / "viewer" / "vendor" / "three.core.js").is_file()
    assert (out / "viewer" / "vendor" / "GLTFLoader.js").is_file()
    manifest = json.loads((out / "review-manifest.json").read_text())
    entry = manifest["skeletons"][0]
    assert entry["status"] == "draft"
    assert entry["neutral_pose"]["rotations"][0]["bone_name"] == "root"
    assert entry["asset"]["assembled_glb"] == "assets/walker/assembled.glb"
    viewer = (out / "viewer" / "viewer.js").read_text()
    assert "item.position_m" in viewer
    assert "item.target_m" in viewer
    assert "state.action.paused = false" in viewer


def test_bundle_rejects_missing_built_motion_instead_of_faking_it(tmp_path):
    library = tmp_path / "library"
    _artifacts(library)
    (library / "skeletons" / "walker" / "motion.json").unlink()
    with pytest.raises(BundleError, match="CC_REVIEW_ASSET_MISSING"):
        write_bundle(_catalog(), library, tmp_path / "review")


def test_bundle_honors_selection_and_rejects_unknown_ids(tmp_path):
    library = tmp_path / "library"
    _artifacts(library)
    assert len(write_bundle(_catalog(), library, tmp_path / "review", ["walker"])) == 1
    with pytest.raises(BundleError, match="CC_REVIEW_SKELETON_UNKNOWN"):
        write_bundle(_catalog(), library, tmp_path / "unknown", ["missing"])
