import copy
import json

import pytest

from critter_crafter.skeletons import review
from critter_crafter.skeletons.qa import verification_version


def qa_result(passed=True):
    return {"passed": passed, "skeleton_id": "test", "content_fingerprint": "abc",
            "qa_version": verification_version(), "sample_source": "evaluated_blender",
            "clips_checked": 8, "samples_checked": 240, "contacts_checked": 480, "joint_limits_checked": 12}


def fixture(tmp_path):
    source = {"skeleton_id": "test", "status": "draft", "branches": [{"length_m": 1.0}]}
    profile = {"binding_profile_id": "leg", "binding_profile_version": "1.0.0"}
    asset = tmp_path / "motion.json"
    asset.write_text('{"baked": true}')
    return source, [profile], {"cadence": 1.0}, [asset]


def test_fingerprint_covers_content_not_review_status(tmp_path):
    src, profiles, settings, assets = fixture(tmp_path)
    before = review.content_fingerprint(src, profiles, settings, assets)
    approved = copy.deepcopy(src)
    approved["status"] = "approved"
    assert review.content_fingerprint(approved, profiles, settings, assets) == before
    for index in range(4):
        source2, profiles2, settings2, assets2 = fixture(tmp_path)
        if index == 0:
            source2["branches"][0]["length_m"] = 1.01
        elif index == 1:
            profiles2[0]["binding_profile_version"] = "1.0.1"
        elif index == 2:
            settings2["cadence"] = 1.1
        else:
            assets2[0].write_text('{"baked": false}')
        assert review.content_fingerprint(source2, profiles2, settings2, assets2) != before


def test_missing_artifact_never_produces_current_review(tmp_path):
    src, profiles, settings, assets = fixture(tmp_path)
    assets[0].unlink()
    with pytest.raises(review.ReviewError, match="CC_REVIEW_ASSET_MISSING"):
        review.content_fingerprint(src, profiles, settings, assets)


def test_receipt_requires_actual_qa_and_unchanged_render_files(tmp_path):
    output = tmp_path / "walk.gif"
    output.write_bytes(b"GIF89a")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.make_receipt("test", "abc", {}, [output])
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.make_receipt("test", "abc", {"passed": True}, [output])
    receipt = review.make_receipt("test", "abc", qa_result(), [output], modes=["bones", "mannequin", "assembled"],
                                  views=["front", "side", "top", "three_quarter"])
    review.verify_receipt(receipt, "test", "abc")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_STALE"):
        review.verify_receipt(receipt, "test", "changed")
    output.write_bytes(b"changed")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_OUTPUT_CHANGED"):
        review.verify_receipt(receipt, "test", "abc")


def test_failed_qa_can_be_reviewed_and_rejected_but_not_approved(tmp_path):
    output = tmp_path / "index.html"
    output.write_text("Failed motion for review")
    receipt = review.make_receipt("test", "abc", qa_result(False), [output])
    review.verify_receipt(receipt, "test", "abc", require_passing=False)
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.verify_receipt(receipt, "test", "abc")
    receipt["qa"] = {}
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.verify_receipt(receipt, "test", "abc", require_passing=False)


def test_qa_bound_to_fingerprint_gates_approve_without_a_receipt():
    review.verify_qa(qa_result(), "test", "abc")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_STALE"):
        review.verify_qa(qa_result(), "test", "changed")
    failed = qa_result(False)
    review.verify_qa(failed, "test", "abc", require_passing=False)
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.verify_qa(failed, "test", "abc")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.verify_qa({}, "test", "abc", require_passing=False)
    empty_fp = {**qa_result(), "content_fingerprint": ""}
    with pytest.raises(review.ReviewError, match="CC_REVIEW_STALE"):
        review.verify_qa(empty_fp, "test", "abc", require_passing=False)
    bound = {"passed": False, "skeleton_id": "test", "content_fingerprint": "abc"}
    review.verify_qa(bound, "test", "abc", require_passing=False)
    with pytest.raises(review.ReviewError, match="CC_REVIEW_QA"):
        review.verify_qa(bound, "test", "abc")


def test_polish_override_requires_matching_source_and_never_overwrites(tmp_path):
    directory = tmp_path / "polish"
    directory.mkdir()
    blend = directory / "master.blend"
    blend.write_bytes(b"authored polish")
    manifest = directory / "override.json"
    manifest.write_text(json.dumps({"skeleton_id": "test", "source_fingerprint": "abc", "blend": "master.blend"}))
    assert review.resolve_polish(directory, "test", "abc") == blend
    with pytest.raises(review.ReviewError, match="CC_POLISH_STALE"):
        review.resolve_polish(directory, "test", "different")
    assert blend.read_bytes() == b"authored polish"


def test_edited_polish_and_pipeline_changes_invalidate_existing_build(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from critter_crafter.library import commands
    from critter_crafter.library.catalog import compile_catalog, load_sources
    from critter_crafter.config import paths
    authored_catalog = compile_catalog(load_sources(paths().data))
    monkeypatch.setattr(commands, "paths", lambda: SimpleNamespace(work=tmp_path))
    skeleton = copy.deepcopy(authored_catalog["skeletons"][0])
    original = commands.skeleton_source_hash(authored_catalog, skeleton)
    directory = tmp_path / "polish" / "skeletons" / skeleton["skeleton_id"]
    directory.mkdir(parents=True)
    (directory / "master.blend").write_bytes(b"authored motion")
    (directory / "override.json").write_text(json.dumps({"skeleton_id": skeleton["skeleton_id"],
        "source_fingerprint": original, "blend": "master.blend"}))
    skeleton["asset"] = {"polish_fingerprint": commands.skeleton_polish_hash(authored_catalog, skeleton)}
    (directory / "master.blend").write_bytes(b"changed authored motion")
    with pytest.raises(review.ReviewError, match="authored polish changed"):
        commands.skeleton_content_hash(authored_catalog, skeleton, tmp_path)
    monkeypatch.setattr(commands, "build_pipeline_fingerprint", lambda: "changed implementation")
    assert commands.skeleton_source_hash(authored_catalog, skeleton) != original
