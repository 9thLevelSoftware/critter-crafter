"""Host-level review gate regressions; Blender transport is intentionally mocked."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click import ClickException
from click.testing import CliRunner

from critter_crafter.blender.runner import BlenderError
from critter_crafter.library import commands as library_commands
from critter_crafter.skeletons import commands as skeleton_commands
from critter_crafter.skeletons import qa, review


def _catalog_and_skeleton() -> tuple[dict, dict]:
    profile = {"binding_profile_id": "leg", "binding_profile_version": "1.0.0", "binding_profile_hash": "profile-hash"}
    skeleton = {
        "schema_version": "3.0.0", "skeleton_id": "review_subject", "family": "biped", "locomotion_hint": "biped",
        "bones": [{"name": "root"}], "branches": [{"branch_id": "leg", "bone_names": ["root"],
            "binding_profile_id": "leg", "binding_profile_version": "1.0.0"}],
        "neutral_pose": {"root_offset_m": [0, 0, 0]}, "anatomy": {"support_branches": ["leg"]},
        "asset": {"glb": "skeletons/review_subject/review_subject.glb", "motion": "skeletons/review_subject/motion.json",
                  "assembled_glb": "skeletons/review_subject/assembled.glb"},
    }
    return {"gait_profiles": [{"hint": "biped"}], "binding_profiles": [profile], "skeletons": [skeleton]}, skeleton


@pytest.mark.parametrize("mutation", ["source", "profiles", "snapshot"])
def test_qa_rejects_current_source_profile_and_snapshot_provenance(tmp_path, monkeypatch, mutation):
    """A baked transport can be structurally valid while referring to prior inputs."""
    catalog, skeleton = _catalog_and_skeleton()
    source_fingerprint = library_commands.skeleton_source_hash(catalog, skeleton)
    expected_profiles = library_commands.skeleton_profiles(catalog, skeleton)
    snapshot_keys = ("schema_version", "skeleton_id", "family", "locomotion_hint", "bones", "branches", "neutral_pose", "anatomy")
    motion = {"skeleton_id": skeleton["skeleton_id"], "source_fingerprint": source_fingerprint,
              "binding_profiles": [{key: profile[key] for key in ("binding_profile_id", "binding_profile_version", "binding_profile_hash")}
                                   for profile in expected_profiles],
              "skeleton_snapshot": {key: skeleton[key] for key in snapshot_keys}}
    if mutation == "source":
        motion["source_fingerprint"] = source_fingerprint[:-1] + ("0" if source_fingerprint[-1] != "0" else "1")
    elif mutation == "profiles":
        motion["binding_profiles"][0]["binding_profile_hash"] = "stale-profile-hash"
    else:
        motion["skeleton_snapshot"] = {"skeleton_id": skeleton["skeleton_id"]}
    motion_path = tmp_path / skeleton["asset"]["motion"]
    motion_path.parent.mkdir(parents=True)
    motion_path.write_text(json.dumps(motion))
    for name in ("glb", "assembled_glb"):
        artifact = tmp_path / skeleton["asset"][name]
        artifact.write_bytes(b"built")

    monkeypatch.setattr(library_commands, "skeleton_content_hash", lambda *_: "content-at-runtime")
    monkeypatch.setattr(qa, "evaluate_motion", lambda *_args, **_kwargs: {
        "skeleton_id": skeleton["skeleton_id"], "passed": True, "diagnostics": [],
        "qa_version": qa.verification_version(), "sample_source": "evaluated_blender",
    })
    from critter_crafter.library import export_validation
    from critter_crafter.skeletons import action_qa
    monkeypatch.setattr(action_qa, "evaluate_actions", lambda *_: {"passed": True, "diagnostics": []})
    monkeypatch.setattr(export_validation, "validate_glb_rest", lambda *_: {"diagnostics": []})
    monkeypatch.setattr(export_validation, "validate_glb_motion", lambda *_: {"diagnostics": []})
    monkeypatch.setattr(export_validation, "validate_glb_surface", lambda *_: {"diagnostics": []})

    result = skeleton_commands._qa(catalog, tmp_path, [skeleton])[0]
    assert result["content_fingerprint"] == "content-at-runtime"
    assert not result["passed"]
    assert {item["code"] for item in result["diagnostics"]} == {"CC_MOTION_PROVENANCE"}


def _qa_result(passed: bool, fingerprint: str) -> dict:
    return {"passed": passed, "skeleton_id": "review_subject", "content_fingerprint": fingerprint,
            "qa_version": qa.verification_version(), "sample_source": "evaluated_blender",
            "clips_checked": 8, "samples_checked": 16, "contacts_checked": 8, "joint_limits_checked": 1}


def test_optional_receipts_still_require_complete_coverage_and_bind_runtime_qa(tmp_path):
    output = tmp_path / "review.html"; output.write_text("review")
    fingerprint = "fingerprint-at-runtime"
    receipt = review.make_receipt("review_subject", fingerprint, _qa_result(True, fingerprint), [output],
                                  modes=["bones", "mannequin", "assembled"],
                                  views=["front", "side", "top", "three_quarter"])
    review.verify_receipt(receipt, "review_subject", fingerprint)

    incomplete = copy.deepcopy(receipt); incomplete["views"].remove("top")
    with pytest.raises(review.ReviewError, match="CC_REVIEW_COVERAGE"):
        review.verify_receipt(incomplete, "review_subject", fingerprint)

    rejected = review.make_receipt("review_subject", fingerprint, _qa_result(False, fingerprint), [output],
                                   modes=["bones", "mannequin", "assembled"],
                                   views=["front", "side", "top", "three_quarter"])
    review.verify_receipt(rejected, "review_subject", fingerprint, require_passing=False)


def _status_fixture(tmp_path, monkeypatch, *, passed: bool = True, qa_fingerprint: str | None = None,
                    write_qa: bool = True, write_receipt: bool = False):
    fingerprint = "content-at-runtime"
    catalog, skeleton = _catalog_and_skeleton()
    data = tmp_path / "data" / "skeletons"
    data.mkdir(parents=True)
    source = data / f"{skeleton['skeleton_id']}.skeleton.json"
    source.write_text(json.dumps({"schema_version": "3.0.0", "skeleton_id": skeleton["skeleton_id"],
                                  "family": "biped", "status": "draft"}))
    monkeypatch.setattr(library_commands, "_built_catalog", lambda: (catalog, tmp_path / "library"))
    monkeypatch.setattr(library_commands, "skeleton_content_hash", lambda *_: fingerprint)
    monkeypatch.setattr(skeleton_commands, "paths", lambda: SimpleNamespace(data=tmp_path / "data", work=tmp_path / "work"))
    if write_qa:
        qa_path = tmp_path / "work" / "review" / "foundation-v3" / "qa.json"
        qa_path.parent.mkdir(parents=True)
        qa_path.write_text(json.dumps({"schema_version": "3.0.0", "results": [
            _qa_result(passed, qa_fingerprint if qa_fingerprint is not None else fingerprint)
        ]}))
    if write_receipt:
        receipt_path = tmp_path / "work" / "review" / "receipts" / f"{skeleton['skeleton_id']}.json"
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(json.dumps({"schema_version": "3.0.0", "skeleton_id": skeleton["skeleton_id"],
                                            "content_fingerprint": fingerprint}))
    return skeleton["skeleton_id"], source, fingerprint


def test_approve_reads_passing_qa_bound_to_fingerprint_without_a_receipt(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch)
    assert not (tmp_path / "work" / "review" / "receipts").exists()
    assert skeleton_commands._set_status(None, (sid,), "approved") == 1
    assert json.loads(source.read_text())["status"] == "approved"


def test_leftover_receipt_cannot_approve_without_current_qa(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch, write_qa=False, write_receipt=True)
    with pytest.raises(ClickException, match="CC_REVIEW_QA"):
        skeleton_commands._set_status(None, (sid,), "approved")
    assert json.loads(source.read_text())["status"] == "draft"


def test_failing_qa_blocks_approve_but_allows_reject_without_a_receipt(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch, passed=False)
    assert not (tmp_path / "work" / "review" / "receipts").exists()
    with pytest.raises(ClickException, match="CC_REVIEW_QA"):
        skeleton_commands._set_status(None, (sid,), "approved")
    assert json.loads(source.read_text())["status"] == "draft"
    assert skeleton_commands._set_status(None, (sid,), "rejected") == 1
    assert json.loads(source.read_text())["status"] == "rejected"


def test_stale_qa_fingerprint_blocks_approve_and_reject(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch, qa_fingerprint="previous-content")
    with pytest.raises(ClickException, match="CC_REVIEW_STALE"):
        skeleton_commands._set_status(None, (sid,), "approved")
    with pytest.raises(ClickException, match="CC_REVIEW_STALE"):
        skeleton_commands._set_status(None, (sid,), "rejected")
    assert json.loads(source.read_text())["status"] == "draft"


def test_empty_fingerprint_on_existing_qa_row_is_stale(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch, qa_fingerprint="")
    with pytest.raises(ClickException, match="CC_REVIEW_STALE"):
        skeleton_commands._set_status(None, (sid,), "approved")
    with pytest.raises(ClickException, match="CC_REVIEW_STALE"):
        skeleton_commands._set_status(None, (sid,), "rejected")
    assert json.loads(source.read_text())["status"] == "draft"


def test_malformed_qa_json_blocks_approve_and_reject(tmp_path, monkeypatch):
    sid, source, _fingerprint = _status_fixture(tmp_path, monkeypatch, write_qa=False)
    qa_path = tmp_path / "work" / "review" / "foundation-v3" / "qa.json"
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text("{")
    with pytest.raises(ClickException, match="CC_REVIEW_QA"):
        skeleton_commands._set_status(None, (sid,), "approved")
    with pytest.raises(ClickException, match="CC_REVIEW_QA"):
        skeleton_commands._set_status(None, (sid,), "rejected")
    assert json.loads(source.read_text())["status"] == "draft"


def test_missing_qa_file_differs_from_report_without_this_id(tmp_path, monkeypatch):
    sid, source, fingerprint = _status_fixture(tmp_path, monkeypatch, write_qa=False)
    qa_path = tmp_path / "work" / "review" / "foundation-v3" / "qa.json"
    with pytest.raises(ClickException, match="has no current bound QA result"):
        skeleton_commands._set_status(None, (sid,), "approved")
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text(json.dumps({"schema_version": "3.0.0", "results": [
        {"passed": True, "skeleton_id": "sibling", "content_fingerprint": fingerprint}
    ]}))
    with pytest.raises(ClickException, match="is not in the current QA report"):
        skeleton_commands._set_status(None, (sid,), "approved")
    with pytest.raises(ClickException, match="is not in the current QA report"):
        skeleton_commands._set_status(None, (sid,), "rejected")
    assert json.loads(source.read_text())["status"] == "draft"


def test_reject_allows_fingerprint_bound_row_without_evaluated_blender(tmp_path, monkeypatch):
    sid, source, fingerprint = _status_fixture(tmp_path, monkeypatch, write_qa=False)
    qa_path = tmp_path / "work" / "review" / "foundation-v3" / "qa.json"
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text(json.dumps({"schema_version": "3.0.0", "results": [
        {"passed": False, "skeleton_id": sid, "content_fingerprint": fingerprint}
    ]}))
    with pytest.raises(ClickException, match="CC_REVIEW_QA"):
        skeleton_commands._set_status(None, (sid,), "approved")
    assert json.loads(source.read_text())["status"] == "draft"
    assert skeleton_commands._set_status(None, (sid,), "rejected") == 1
    assert json.loads(source.read_text())["status"] == "rejected"


def test_skeleton_qa_merges_selected_rows_and_keeps_siblings(tmp_path, monkeypatch):
    catalog = {"skeletons": [
        {"skeleton_id": "review_subject", "family": "biped"},
        {"skeleton_id": "sibling", "family": "biped"},
    ]}
    work = tmp_path / "work"
    qa_path = work / "review" / "foundation-v3" / "qa.json"
    qa_path.parent.mkdir(parents=True)
    sibling = {"passed": True, "skeleton_id": "sibling", "content_fingerprint": "keep-me"}
    qa_path.write_text(json.dumps({"schema_version": "3.0.0", "results": [
        sibling, {"passed": False, "skeleton_id": "review_subject", "content_fingerprint": "old"},
    ]}))
    monkeypatch.setattr(skeleton_commands, "paths", lambda: SimpleNamespace(work=work, data=tmp_path / "data"))
    monkeypatch.setattr(library_commands, "_built_catalog", lambda: (catalog, tmp_path / "library"))
    updated = {"passed": True, "skeleton_id": "review_subject", "content_fingerprint": "new",
               "diagnostics": [], "clips_checked": 8}
    monkeypatch.setattr(skeleton_commands, "_qa", lambda *_: [updated])
    result = CliRunner().invoke(skeleton_commands.skeleton, ["qa", "review_subject"])
    assert result.exit_code == 0, result.output
    by_id = {row["skeleton_id"]: row for row in json.loads(qa_path.read_text())["results"]}
    assert by_id["sibling"] == sibling
    assert by_id["review_subject"] == updated


def test_interrupted_assembly_persists_base_catalog_without_assembled_glb(tmp_path, monkeypatch):
    """The resumable base catalog must not advertise the assembly that never completed."""
    catalog = {"schema_version": "3.0.0", "library_id": "review", "version": "1", "gait_profiles": [{"hint": "biped"}],
               "binding_profiles": [], "parts": [], "skeletons": [{"skeleton_id": "review_subject", "locomotion_hint": "biped",
                   "branches": [], "bones": [], "asset": {"assembled_glb": "skeletons/review_subject/stale.glb"}}]}
    monkeypatch.setattr(library_commands, "_catalog", lambda: copy.deepcopy(catalog))
    monkeypatch.setattr(library_commands, "build_jobs", lambda cat, out: [{"op": "skeleton", "args": {"skeleton": cat["skeletons"][0]}}])
    monkeypatch.setattr(library_commands, "build_pipeline_fingerprint", lambda: "pipeline-at-runtime")
    monkeypatch.setattr(library_commands, "skeleton_source_hash", lambda *_: "source-at-runtime")
    monkeypatch.setattr(library_commands, "skeleton_content_hash", lambda *_: "content-at-runtime")
    monkeypatch.setattr(library_commands, "generate_for_skeleton", lambda *_: {"fills": []})

    def apply_base(cat, out, _results):
        asset = cat["skeletons"][0]["asset"] = {"fbx": "skeletons/review_subject/a.fbx", "glb": "skeletons/review_subject/a.glb",
                                                   "blend": "skeletons/review_subject/a.blend", "motion": "skeletons/review_subject/motion.json",
                                                   "assembled_glb": "skeletons/review_subject/stale.glb"}
        for path in asset.values():
            target = out / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"built")
        return []
    monkeypatch.setattr(library_commands, "apply_results", apply_base)
    monkeypatch.setattr(library_commands, "export_qa_problems", lambda *a, **k: [])
    monkeypatch.setattr(library_commands, "resolve_gltf_validator",
                        lambda: (None, "CC_GLTF_VALIDATOR_MISSING: test"))

    calls = 0
    def interrupted(_op, _args):
        nonlocal calls; calls += 1
        if calls == 1:
            return {"results": [{"ok": True, "op": "skeleton", "result": {}}]}
        raise BlenderError("assembly interrupted")
    monkeypatch.setattr(library_commands, "run_op", interrupted)

    result = CliRunner().invoke(library_commands.library, ["build", "--out", str(tmp_path)])
    assert result.exit_code != 0
    base = tmp_path / "review-v1" / "catalog.json"
    persisted = json.loads(base.read_text())
    assert "assembled_glb" not in persisted["skeletons"][0]["asset"]


def test_assembly_cache_tracks_baked_base_content_and_polish(tmp_path, monkeypatch):
    """Assembly reuse is keyed by the baked base, not only the unchanged source."""
    sid = "review_subject"
    catalog = {
        "schema_version": "3.0.0", "library_id": "review", "version": "1",
        "gait_profiles": [{"hint": "biped"}], "binding_profiles": [],
        "branch_templates": [], "parts": [{
            "part_id": "review_body", "category": "body", "source": "reference",
            "inventory_kind": "reference", "status": "reference", "max_triangles": 100,
        }],
        "skeletons": [{
            "schema_version": "3.0.0", "skeleton_id": sid, "family": "biped",
            "locomotion_hint": "biped", "bones": [{"name": "root"}], "branches": [],
            "neutral_pose": {"root_offset_m": [0, 0, 0]}, "anatomy": {},
        }],
    }
    polish = {"hash": "polish-v1"}
    baked_revision = {"value": "bake-v1"}
    calls = {"skeleton": 0, "part": 0, "assemble": 0}

    monkeypatch.setattr(library_commands, "_catalog", lambda: copy.deepcopy(catalog))
    monkeypatch.setattr(library_commands, "skeleton_source_hash", lambda *_: "stable-source-hash")
    monkeypatch.setattr(library_commands, "skeleton_polish_hash", lambda *_: polish["hash"])
    recipe = {"recipe_id": "review-recipe", "skeleton_id": sid,
              "fills": [{"part_id": "review_body", "connector_part_id": ""}]}
    monkeypatch.setattr(library_commands, "generate_for_skeleton", lambda *_: copy.deepcopy(recipe))
    monkeypatch.setattr(library_commands, "assembly_pipeline_fingerprint", lambda: "assembly-pipeline-v1")
    monkeypatch.setattr(library_commands, "part_pipeline_fingerprint", lambda: "part-pipeline-v1")
    monkeypatch.setattr(library_commands, "export_qa_problems", lambda *a, **k: [])
    monkeypatch.setattr(library_commands, "resolve_gltf_validator",
                        lambda: (None, "CC_GLTF_VALIDATOR_MISSING: test"))

    def jobs(cat, out):
        skeleton = cat["skeletons"][0]
        directory = out / "skeletons" / sid
        return [{"op": "skeleton", "args": {
            "skeleton": skeleton,
            "out_fbx": str(directory / f"{sid}.fbx"),
            "out_glb": str(directory / f"{sid}.glb"),
            "out_blend": str(directory / f"{sid}.blend"),
            "out_motion": str(directory / "motion.json"),
        }}, {"op": "placeholder", "args": {
            "part": cat["parts"][0],
            "out_fbx": str(out / "parts" / "review_body" / "review_body.fbx"),
            "out_glb": str(out / "parts" / "review_body" / "review_body.glb"),
        }}]
    monkeypatch.setattr(library_commands, "build_jobs", jobs)

    def run_batch(_op, args):
        results = []
        for job in args["jobs"]:
            if job["op"] == "skeleton":
                calls["skeleton"] += 1
                for key in ("out_fbx", "out_glb", "out_blend", "out_motion"):
                    artifact = Path(job["args"][key])
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    if key == "out_motion":
                        artifact.write_text(json.dumps({"source_fingerprint": "stable-source-hash",
                                                       "revision": baked_revision["value"]}))
                    else:
                        artifact.write_bytes(f"{baked_revision['value']}:{key}".encode())
                results.append({"ok": True, "op": "skeleton", "result": {
                    "skeleton_id": sid, "bones": 1, "clips": [],
                }})
            elif job["op"] == "placeholder":
                calls["part"] += 1
                for key in ("out_fbx", "out_glb"):
                    artifact = Path(job["args"][key])
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_bytes(f"part:{calls['part']}:{key}".encode())
                results.append({"ok": True, "op": "placeholder", "result": {
                    "part_id": "review_body", "triangles": 10,
                }})
            else:
                calls["assemble"] += 1
                assembled = Path(job["args"]["out_glb"])
                assembled.parent.mkdir(parents=True, exist_ok=True)
                assembled.write_bytes(f"assembly:{calls['assemble']}".encode())
                results.append({"ok": True, "op": "assemble", "result": {}})
        return {"results": results}
    monkeypatch.setattr(library_commands, "run_op", run_batch)

    runner = CliRunner()
    first = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path)])
    assert first.exit_code == 0, first.output
    assert calls == {"skeleton": 1, "part": 1, "assemble": 1}

    unchanged = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert unchanged.exit_code == 0, unchanged.output
    assert calls == {"skeleton": 1, "part": 1, "assemble": 1}

    out = tmp_path / "review-v1"
    built_before_edit = json.loads((out / "catalog.json").read_text())
    original_fingerprint = built_before_edit["skeletons"][0]["content_fingerprint"]
    (out / "parts" / "review_body" / "review_body.fbx").write_bytes(b"externally-edited-part")
    assert (library_commands.skeleton_content_hash(
        built_before_edit, built_before_edit["skeletons"][0], out
    ) != original_fingerprint)

    changed_part = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert changed_part.exit_code == 0, changed_part.output
    assert calls == {"skeleton": 1, "part": 2, "assemble": 2}

    unchanged_part = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert unchanged_part.exit_code == 0, unchanged_part.output
    assert calls == {"skeleton": 1, "part": 2, "assemble": 2}

    (out / "skeletons" / sid / "motion.json").write_text(json.dumps({
        "source_fingerprint": "stable-source-hash", "revision": "externally-updated-baked-motion"}))
    changed_bake = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert changed_bake.exit_code == 0, changed_bake.output
    # Corrupted base bytes must rebuild the skeleton, not be re-fingerprinted in place.
    assert calls == {"skeleton": 2, "part": 2, "assemble": 2}

    unchanged_again = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert unchanged_again.exit_code == 0, unchanged_again.output
    assert calls == {"skeleton": 2, "part": 2, "assemble": 2}

    (out / "skeletons" / sid / "assembled.glb").write_bytes(b"tampered-assembled-glb")
    changed_assembly = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert changed_assembly.exit_code == 0, changed_assembly.output
    assert calls == {"skeleton": 2, "part": 2, "assemble": 3}

    unchanged_assembly = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert unchanged_assembly.exit_code == 0, unchanged_assembly.output
    assert calls == {"skeleton": 2, "part": 2, "assemble": 3}

    polish["hash"] = "polish-v2"
    baked_revision["value"] = "bake-v2"
    changed_polish = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert changed_polish.exit_code == 0, changed_polish.output
    assert calls == {"skeleton": 3, "part": 2, "assemble": 4}

    polished_unchanged = runner.invoke(library_commands.library, ["build", "--out", str(tmp_path), "--no-clean"])
    assert polished_unchanged.exit_code == 0, polished_unchanged.output
    assert calls == {"skeleton": 3, "part": 2, "assemble": 4}

    built_catalog = json.loads((out / "catalog.json").read_text())
    built_skeleton = built_catalog["skeletons"][0]
    built_skeleton["asset"].pop("assembled_glb")
    expected_base = library_commands.skeleton_content_hash(built_catalog, built_skeleton, out)
    state = json.loads((out / ".build-state.json").read_text())
    assert state["assemblies"][sid]["skeleton"] == expected_base


def test_built_catalog_requires_current_part_source_pipeline_and_artifacts(tmp_path, monkeypatch):
    """A base-only catalog remains reviewable, but stale part provenance never does."""
    out = tmp_path / "review-v1"
    part_source = {
        "part_id": "review_body", "category": "body", "source": "reference",
        "inventory_kind": "reference", "status": "reference", "max_triangles": 100,
    }
    skeleton_source = {"skeleton_id": "review_subject", "source_fingerprint": "source-v1"}
    current = {
        "schema_version": "3.0.0", "library_id": "review", "version": "1",
        "parts": [part_source], "skeletons": [skeleton_source],
    }
    built = copy.deepcopy(current)
    built_part = built["parts"][0]
    built_part["asset"] = {
        "fbx": "parts/review_body/review_body.fbx",
        "glb": "parts/review_body/review_body.glb",
    }
    built_skeleton = built["skeletons"][0]
    built_skeleton["asset"] = {"polish_fingerprint": ""}
    built_skeleton["content_fingerprint"] = "base-content"
    for relative in built_part["asset"].values():
        artifact = out / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(relative.encode())
    state = {"parts": {"review_body": library_commands.part_build_state(
        part_source, out, asset=built_part["asset"], pipeline="part-pipeline-v1"
    )}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalog.json").write_text(json.dumps(built))
    (out / ".build-state.json").write_text(json.dumps(state))

    active = {"catalog": current, "pipeline": "part-pipeline-v1"}
    monkeypatch.setattr(library_commands, "_catalog", lambda: copy.deepcopy(active["catalog"]))
    monkeypatch.setattr(library_commands, "library_dir", lambda *_args, **_kwargs: out)
    monkeypatch.setattr(library_commands, "part_pipeline_fingerprint", lambda: active["pipeline"])
    monkeypatch.setattr(library_commands, "skeleton_source_hash", lambda *_: "source-v1")
    monkeypatch.setattr(library_commands, "skeleton_polish_hash", lambda *_: "")
    monkeypatch.setattr(library_commands, "skeleton_content_hash", lambda *_: "base-content")

    loaded, loaded_out = library_commands._built_catalog()
    assert loaded_out == out
    assert "assembled_glb" not in loaded["skeletons"][0]["asset"]
    with pytest.raises(ClickException, match="release package requires an assembled GLB"):
        library_commands._built_catalog(tmp_path, require_assemblies=True)

    fbx = out / built_part["asset"]["fbx"]
    original_fbx = fbx.read_bytes()
    fbx.write_bytes(b"edited-after-build")
    with pytest.raises(ClickException, match="part pipeline or artifacts changed"):
        library_commands._built_catalog()
    fbx.write_bytes(original_fbx)

    active["catalog"] = copy.deepcopy(current)
    active["catalog"]["parts"][0]["max_triangles"] = 101
    with pytest.raises(ClickException, match="part source changed"):
        library_commands._built_catalog()

    active["catalog"] = current
    active["pipeline"] = "part-pipeline-v2"
    with pytest.raises(ClickException, match="part pipeline or artifacts changed"):
        library_commands._built_catalog()

    active["pipeline"] = "part-pipeline-v1"
    assembled = out / "skeletons" / "review_subject" / "assembled.glb"
    assembled.parent.mkdir(parents=True)
    assembled.write_bytes(b"assembled")
    built_skeleton["asset"]["assembled_glb"] = assembled.relative_to(out).as_posix()
    built_skeleton["content_fingerprint"] = "assembled-content"
    recipe = {"recipe_id": "review-recipe", "fills": []}
    monkeypatch.setattr(library_commands, "_review_recipe", lambda *_: recipe)
    monkeypatch.setattr(
        library_commands,
        "skeleton_content_hash",
        lambda _catalog, skeleton, _out: (
            "assembled-content" if skeleton.get("asset", {}).get("assembled_glb") else "base-content"
        ),
    )
    state["assemblies"] = {"review_subject": library_commands._assembly_state(
        built, recipe, out, "base-content"
    )}
    (out / "catalog.json").write_text(json.dumps(built))
    (out / ".build-state.json").write_text(json.dumps(state))
    library_commands._built_catalog(tmp_path, require_assemblies=True)

    monkeypatch.setattr(library_commands, "paths", lambda: SimpleNamespace(root=tmp_path))
    runner = CliRunner()
    packed = runner.invoke(library_commands.library, ["pack", "--out", str(tmp_path)])
    assert packed.exit_code == 0, packed.output
    archive = tmp_path / "dist" / "critter-library-review-v1.zip"
    assert archive.is_file()

    state["assemblies"]["review_subject"]["pipeline"] = "stale-assembly-pipeline"
    (out / ".build-state.json").write_text(json.dumps(state))
    archive.unlink()
    stale = runner.invoke(library_commands.library, ["pack", "--out", str(tmp_path)])
    assert stale.exit_code != 0
    assert "assembly inputs changed" in stale.output
    assert not archive.exists()


def test_build_cannot_relabel_baked_motion_when_source_changes(tmp_path, monkeypatch):
    catalog = {"schema_version": "3.0.0", "skeletons": [
        {"skeleton_id": "subject", "bones": [{"name": "root"}]}], "parts": []}
    motion = tmp_path / "skeletons" / "subject" / "motion.json"
    motion.parent.mkdir(parents=True)
    motion.write_text(json.dumps({"source_fingerprint": "source-before-build"}))
    monkeypatch.setattr(library_commands, "skeleton_source_hash", lambda *_: "changed-during-build")
    monkeypatch.setattr(library_commands, "skeleton_polish_hash", lambda *_: "")
    problems = library_commands.apply_results(catalog, tmp_path, [{
        "op": "skeleton", "ok": True, "result": {"skeleton_id": "subject", "bones": 1, "clips": []}}])
    assert any(problem.startswith("CC_BUILD_STALE:") for problem in problems)
    assert catalog["skeletons"][0]["source_fingerprint"] == "source-before-build"
